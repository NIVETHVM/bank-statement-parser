"""
parsers/axis_bank.py
Axis Bank statement parser — subclass of BankStatementParser.

Layout (the "Smart Statement Report" / Neo for corporates format):
  S.No.  Transaction Date  Value Date  Cheque Number  Transaction Particulars
  Amount  Transaction Type  Balance  Branch Name

The "Transaction Type" column explicitly prints DR or CR — this is the
simplest of the bank layouts seen so far, no balance-direction inference
needed at all, similar to IndusInd/IDBI. Amounts are printed as
"INR 1,23,456.00" (Indian lakh-grouping) and Tesseract sometimes drops
the leading "I" so it reads as "NR" — handled below.

Quirks:
  - leading S.No. column (a small integer) must not be mistaken for an
    amount or a cheque number
  - some rows have a Cheque Number (e.g. SAK/CASH WDL rows show "77323")
    sitting between Value Date and the particulars — must not be
    swallowed into the particulars text or mistaken for a transaction ID
  - "Total Transaction Amount" footer row on the last page must be
    skipped, not parsed as a transaction
  - particulars wrap onto 2-4 OCR lines per transaction (this statement
    has the longest wraps seen across any bank so far)
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bank_parser_core import BankStatementParser


DATE_RE    = re.compile(r'\b(\d{2}/\d{2}/\d{4})\b')
# "INR" sometimes OCRs as "NR" or "INR" — accept either, amount follows
AMOUNT_RE  = re.compile(r'(?:I?NR)\s*(-?\d{1,3}(?:,\d{2,3})*\.\d{2})')
DRCR_RE    = re.compile(r'\b(DR|CR)\b')
SNO_RE     = re.compile(r'^\s*(\d{1,3})\s+\d{2}/\d{2}/\d{4}')


def clean_amount(s):
    neg = s.strip().startswith('-')
    digits = re.sub(r'[^\d.]', '', s)
    if not digits:
        return None
    val = float(digits)
    return -val if neg else val


class AxisBankParser(BankStatementParser):

    BANK_NAME = "Axis Bank"
    DETECT_KEYWORDS = ["AXIS BANK", "Axis Bank", "UTIB0", "axisbank.com"]

    SKIP_LINE_MARKERS = BankStatementParser.SKIP_LINE_MARKERS + [
        'Smart Statement Report', 'Customer ID', 'Address:', 'Address :',
        'Statement of Account No', 'Opening Balance:', 'Closing Balance:',
        'S. No.', 'Transaction Date', 'Total Transaction Amount',
        'Unless the constituent', 'closing balance as shown',
        'We would like to reiterate', 'With effect from', 'Deposit Insurance',
        'In compliance with regulatory', 'Registered Office', 'BRANCH ADDRESS',
        'Legend:', 'End of Report', 'Note:', 'Save Paper', 'NEO for corporates',
        'ICONN', 'VMT-ICON', 'AUTOSWEEP', 'REV SWEEP', 'SWEEP TRF', 'CWDR',
        'PUR -', 'RATE.DIFF', 'CLG -', 'EDC -', 'SETU', 'Int,Coll', 'Int.pd',
        'ISSUE -', 'AMEND -', 'OW RTN', 'corporate.ib@axisbank.com',
    ]

    # ── Metadata ──────────────────────────────────────────────────────────────

    def extract_metadata(self, text):
        meta = {}
        patterns = {
            'name':            r'^([A-Z][A-Z &.,]+(?:LLP|LTD|LIMITED|INC)\.?)\s*$',
            'account_number':  r'Statement of Account No\s*-\s*(\d+)',
            'period':          r'for period\s*\((\S+)\s+to\s+(\S+)\)',
            'ifsc':            r'IFSC:\s*(\S+)',
            'opening_balance': r'Opening Balance:\s*INR\s*([\d,]+\.\d{2})',
        }
        for key, pat in patterns.items():
            flags = re.IGNORECASE if key != 'name' else re.MULTILINE
            m = re.search(pat, text, flags)
            if m:
                if key == 'period':
                    meta[key] = f"{m.group(1)} to {m.group(2)}"
                elif key == 'opening_balance':
                    val = clean_amount(m.group(1))
                    if val is not None:
                        meta[key] = val
                else:
                    meta[key] = m.group(1).strip()
        return meta

    # ── Per-line parsing ─────────────────────────────────────────────────────

    def parse_line(self, line):
        dates = DATE_RE.findall(line)
        if not dates:
            return None

        # Skip the "Total Transaction Amount" footer and similar non-row text
        if 'Total Transaction' in line:
            return None

        date       = dates[0]
        value_date = dates[1] if len(dates) > 1 else date

        drcr_match = DRCR_RE.search(line)
        dr_cr = drcr_match.group(1) if drcr_match else None

        amount_tokens_raw = AMOUNT_RE.findall(line)
        cleaned = [clean_amount(a) for a in amount_tokens_raw]
        cleaned = [c for c in cleaned if c is not None]

        # Need at least amount + balance (both printed as INR X.XX) to
        # treat this as a real transaction line, not a continuation/header
        if len(cleaned) < 2:
            return None

        amount  = cleaned[0]
        balance = cleaned[1]

        withdrawals = deposits = None
        if dr_cr == 'DR':
            withdrawals = amount
        elif dr_cr == 'CR':
            deposits = amount
        else:
            deposits = amount   # fallback; validate_balances() will flag if wrong

        # ── S.No. — strip the leading row number so it doesn't pollute particulars
        sno_match = SNO_RE.match(line)

        # ── Cheque number — rare; a short digit token between Value Date and
        # the particulars, distinct from any INR-prefixed amount ────────────
        # Pattern: after the second date, before the narration text begins
        chq_match = re.search(
            r'\d{2}/\d{2}/\d{4}\s+(\d{4,8})\s+[A-Za-z]', line
        )
        chq_no = chq_match.group(1) if chq_match else None

        # ── Particulars cleanup ─────────────────────────────────────────────
        particulars = line
        if sno_match:
            particulars = particulars[sno_match.end(1):]
        for d in dates:
            particulars = particulars.replace(d, '')
        if chq_no:
            particulars = re.sub(r'\b' + chq_no + r'\b', '', particulars, count=1)
        # Remove "INR <amount> DR/CR" together as one unit — doing the
        # amount and the DR/CR label in separate passes can leave the
        # label stranded mid-string if regex ordering doesn't line up.
        particulars = re.sub(
            r'I?NR\s*-?\d{1,3}(?:,\d{2,3})*\.\d{2}\s*(?:DR|CR)?', '', particulars
        )
        # Remove the second "INR <balance>" (and trailing branch name) entirely
        particulars = re.sub(r'I?NR\s*-?\d{1,3}(?:,\d{2,3})*\.\d{2}.*$', '', particulars)
        particulars = re.sub(r'[|\\]+', ' ', particulars)
        particulars = re.sub(r'\s{2,}', ' ', particulars).strip()
        particulars = re.sub(r'^[\s,\-]+|[\s,]+$', '', particulars)

        tran_type = None
        for tt in ["NEFT", "RTGS", "IMPS", "POS", "ATM", "INB", "TRF", "SAK", "TIPS"]:
            if particulars.upper().startswith(tt):
                tran_type = tt
                break

        return {
            'date':        date,
            'value_date':  value_date,
            'particulars': particulars,
            'tran_type':   tran_type or '',
            'tran_id':     chq_no or '',
            'withdrawals': withdrawals,
            'deposits':    deposits,
            'balance':     balance,
            'dr_cr':       dr_cr or '',
        }

    # ── Axis Bank's own column layout ───────────────────────────────────────
    EXCEL_HEADERS = ['Date', 'Value Date', 'Cheque Number', 'Transaction Particulars',
                      'Amount', 'Transaction Type', 'Balance']
    EXCEL_COL_WIDTHS = {'A': 12, 'B': 12, 'C': 14, 'D': 50,
                        'E': 16, 'F': 10, 'G': 16}
    EXCEL_NUMBER_COLS = {5, 7}   # Amount, Balance

    def to_excel_row(self, tx):
        amount = tx.get('withdrawals') if tx.get('withdrawals') is not None else tx.get('deposits')
        return [
            tx.get('date', ''),
            tx.get('value_date', ''),
            tx.get('tran_id', ''),
            tx.get('particulars', ''),
            amount,
            tx.get('dr_cr', ''),
            tx.get('balance'),
        ]
