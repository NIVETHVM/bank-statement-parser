"""
parsers/idbi_bank.py
IDBI Bank statement parser — subclass of BankStatementParser.

Layout:
  Srl  TxnDate Time  ValueDate  Description  Cr./Dr.  INR  Amount  Balance

The "Cr." / "Dr." token right after the description directly tells us
which bucket the amount belongs to — no pipe or column-position guessing
needed, similar to IndusInd. Amounts use Indian comma grouping.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bank_parser_core import BankStatementParser


DATE_RE   = re.compile(r'\b(\d{2}/\d{2}/\d{4})\b')
AMOUNT_RE = re.compile(r'\b(\d{1,2}(?:,\d{2,3})*\.\d{2})\b')
CRDR_RE   = re.compile(r'\b(Cr|Dr)\.?', re.IGNORECASE)
SRL_RE    = re.compile(r'^\s*(\d{1,3})\s*[|/]')


def clean_amount(s):
    return re.sub(r'[^\d.]', '', s)


class IDBIBankParser(BankStatementParser):

    BANK_NAME = "IDBI Bank"
    DETECT_KEYWORDS = ["IDBI Bank", "IDBI BANK", "idbi.co.in"]

    SKIP_LINE_MARKERS = BankStatementParser.SKIP_LINE_MARKERS + [
        'Email address', 'Toll Free', 'Toll-free', 'Primary Account Holder',
        'Account Branch', 'Account No', 'Transaction Date From',
        'Statement Summary', 'Dr Count', 'does not require signature',
        'Regd. Office', 'Chargeable number', 'Srl', 'Txn Date',
    ]

    def extract_metadata(self, text):
        meta = {}
        patterns = {
            'name':            r'Primary Account Holder Name\s*:\s*([^\n]+)',
            'account_number':  r'Account No\s*:\s*(\S+)',
            'period':          r'Transaction Date From\s*:\s*(\S+)\s+to:\s*(\S+)',
            'account_branch':  r'Account Branch:\s*:\s*([^\n]+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                if key == 'period':
                    meta[key] = f"{m.group(1)} to {m.group(2)}"
                else:
                    meta[key] = m.group(1).strip()
        return meta

    def parse_line(self, line):
        dates = DATE_RE.findall(line)
        if not dates:
            return None

        date       = dates[0]
        value_date = dates[1] if len(dates) > 1 else date

        crdr_match = CRDR_RE.search(line)
        dr_cr = None
        if crdr_match:
            dr_cr = 'CR' if crdr_match.group(1).upper() == 'CR' else 'DR'

        amount_tokens = AMOUNT_RE.findall(line)
        cleaned = []
        for a in amount_tokens:
            s = clean_amount(a)
            if s:
                try:
                    cleaned.append(float(s))
                except ValueError:
                    pass

        withdrawals = deposits = balance = None

        if len(cleaned) >= 2:
            balance = cleaned[-1]
            txn_amount = cleaned[-2]
            if dr_cr == 'DR':
                withdrawals = txn_amount
            elif dr_cr == 'CR':
                deposits = txn_amount
            else:
                deposits = txn_amount   # fallback if Cr/Dr token wasn't caught
        elif len(cleaned) == 1:
            balance = cleaned[0]

        # ── Srl number / cheque-no column cleanup ───────────────────────────
        srl_match = SRL_RE.match(line)
        tran_id = srl_match.group(1) if srl_match else None

        # ── Particulars cleanup ─────────────────────────────────────────────
        particulars = line
        if srl_match:
            particulars = particulars[srl_match.end():]
        for d in dates:
            particulars = particulars.replace(d, '')
        particulars = re.sub(r'\d{1,2}:\d{2}:\d{2}', '', particulars)   # strip time
        if crdr_match:
            particulars = particulars.replace(crdr_match.group(0), '')
        particulars = re.sub(r'\bINR\b', '', particulars)
        for a in amount_tokens:
            particulars = particulars.replace(a, '')
        particulars = re.sub(r'[|\\]+', ' ', particulars)
        particulars = re.sub(r'\s{2,}', ' ', particulars).strip()
        particulars = re.sub(r'^[\s,]+|[\s,]+$', '', particulars)

        # Tran type — first meaningful token of the description
        tran_type = None
        for tt in ["IMPS", "UPI", "NEFT", "RTGS", "IPAY", "SMS"]:
            if particulars.upper().startswith(tt):
                tran_type = tt
                break

        return {
            'date':        date,
            'value_date':  value_date,
            'particulars': particulars,
            'tran_type':   tran_type or '',
            'tran_id':     tran_id or '',
            'withdrawals': withdrawals,
            'deposits':    deposits,
            'balance':     balance,
            'dr_cr':       dr_cr or '',
        }

    # ── IDBI's own column layout ─────────────────────────────────────────────
    EXCEL_HEADERS = ['Srl', 'Txn Date', 'Value Date', 'Description', 'CR/DR',
                      'Amount (INR)', 'Balance (INR)']
    EXCEL_COL_WIDTHS = {'A': 8, 'B': 16, 'C': 14, 'D': 50, 'E': 8,
                        'F': 16, 'G': 16}
    EXCEL_NUMBER_COLS = {6, 7}   # Amount, Balance

    def to_excel_row(self, tx):
        amount = tx.get('withdrawals') if tx.get('withdrawals') is not None else tx.get('deposits')
        return [
            tx.get('tran_id', ''),
            tx.get('date', ''),
            tx.get('value_date', ''),
            tx.get('particulars', ''),
            tx.get('dr_cr', ''),
            amount,
            tx.get('balance'),
        ]
