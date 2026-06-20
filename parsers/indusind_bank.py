"""
parsers/indusind_bank.py
IndusInd Bank statement parser — subclass of BankStatementParser.

Layout (simpler than Federal Bank — separate Debit/Credit columns):
  DD Mon YYYY   Transfer Credit/Debit   Description   Debit   Credit   Balance

The "Type" field (Transfer Credit / Transfer Debit) directly tells us
which column holds the amount — no pipe-separator guessing needed.
Amounts use Indian comma grouping (e.g. 4,00,000.00) and a "-" placeholder
for whichever column doesn't apply to that row.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bank_parser_core import BankStatementParser


MONTHS = "JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC"
DATE_RE = re.compile(r'\b(\d{1,2}\s+(?:' + MONTHS + r')\s+\d{4})\b', re.IGNORECASE)

# Indian-grouped amount: 4,00,000.00  or  1,79,000.00  or plain 500.00
AMOUNT_RE = re.compile(r'\b(\d{1,2}(?:,\d{2,3})*\.\d{2})\b')

TYPE_RE = re.compile(r'\bTransfer\s+(Credit|Debit)\b', re.IGNORECASE)


def clean_amount(s):
    return re.sub(r'[^\d.]', '', s)


class IndusIndBankParser(BankStatementParser):

    BANK_NAME = "IndusInd Bank"
    DETECT_KEYWORDS = ["IndusInd Bank", "Indusind Bank", "INDB0"]

    # IndusInd's own column layout — matches its printed statement exactly:
    # Date | Type | Description | Debit | Credit | Balance
    EXCEL_HEADERS = ['Date', 'Type', 'Description', 'Debit', 'Credit', 'Balance']
    EXCEL_COL_WIDTHS = {'A': 14, 'B': 16, 'C': 50, 'D': 14, 'E': 14, 'F': 16}
    EXCEL_NUMBER_COLS = {4, 5, 6}   # Debit, Credit, Balance

    def to_excel_row(self, tx):
        # "Type" column shows the original Transfer Credit/Debit wording,
        # reconstructed from dr_cr so the Excel output reads like the source PDF.
        if tx.get('dr_cr') == 'DR':
            type_label = 'Transfer Debit'
        elif tx.get('dr_cr') == 'CR':
            type_label = 'Transfer Credit'
        else:
            type_label = ''

        return [
            tx.get('date', ''),
            type_label,
            tx.get('particulars', ''),
            tx.get('withdrawals'),   # Debit
            tx.get('deposits'),      # Credit
            tx.get('balance'),
        ]

    # IndusInd-specific noise lines to skip (in addition to base class defaults)
    SKIP_LINE_MARKERS = BankStatementParser.SKIP_LINE_MARKERS + [
        'computer generated statement', 'does not require signature',
        'Account Branch', 'Account No.', 'IFSC Code', 'MICR Code',
        'Customer Id', 'Account type', 'From :', 'Date Type Description',
    ]

    def extract_metadata(self, text):
        """IndusInd-specific header fields."""
        meta = {}
        patterns = {
            'account_number':  r'Account No\.\s*:\s*(\S+)',
            'ifsc':            r'IFSC Code\s*:\s*(\S+)',
            'period':          r'From\s*:\s*(\S+\s+\S+\s+\S+)\s+To\s*:\s*(\S+\s+\S+\s+\S+)',
            'account_branch':  r'Account Branch\s*:\s*([^\n]+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                if key == 'period':
                    meta[key] = f"{m.group(1)} to {m.group(2)}"
                else:
                    meta[key] = m.group(1).strip()

        # Account holder is the first non-empty line on page 1 in this layout
        for line in text.splitlines():
            line = line.strip()
            if line and 'IndusInd' not in line and 'Indusind' not in line:
                meta['name'] = line
                break

        return meta

    def parse_line(self, line):
        dates = DATE_RE.findall(line)
        if not dates:
            return None

        date = dates[0]
        # IndusInd statements show only one date per row (no separate value date)
        value_date = date

        type_match = TYPE_RE.search(line)
        dr_cr = None
        if type_match:
            dr_cr = 'DR' if type_match.group(1).upper() == 'DEBIT' else 'CR'

        # Find amount tokens (Indian comma grouping)
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
            # Last number is always balance; the one before it is the
            # transaction amount. Type tells us which bucket it goes in.
            balance = cleaned[-1]
            txn_amount = cleaned[-2]
            if dr_cr == 'DR':
                withdrawals = txn_amount
            elif dr_cr == 'CR':
                deposits = txn_amount
            else:
                # Fallback: no Type detected — guess from "-" placeholder position
                # (rare; Type is almost always present in this layout)
                deposits = txn_amount
        elif len(cleaned) == 1:
            balance = cleaned[0]

        # ── Particulars cleanup ─────────────────────────────────────────────
        particulars = line
        for d in dates:
            particulars = particulars.replace(d, '')
        if type_match:
            particulars = particulars.replace(type_match.group(0), '')

        # Remove amount tokens (including the "-" placeholder)
        for a in amount_tokens:
            particulars = particulars.replace(a, '')
        particulars = re.sub(r'(?<!\w)-(?!\w)', '', particulars)  # standalone "-" placeholders

        particulars = re.sub(r'\s{2,}', ' ', particulars).strip()
        particulars = re.sub(r'^[\s,]+|[\s,]+$', '', particulars)

        # Tran ID — pull from the description if present (e.g. IMPS/P2A/609417914271/...)
        tran_id_match = re.search(r'/(\d{9,15})/', line)
        tran_id = tran_id_match.group(1) if tran_id_match else None

        # Tran type — first token of the description (IMPS, R, CASH, etc.)
        tran_type = None
        for tt in ["IMPS", "NEFT", "RTGS", "UPI", "R", "CASH"]:
            if re.search(r'\b' + tt + r'\b', particulars[:20]):
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
