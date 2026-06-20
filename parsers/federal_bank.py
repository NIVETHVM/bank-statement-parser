"""
parsers/federal_bank.py
Federal Bank statement parser — subclass of BankStatementParser.

Layout quirk (the key insight, found by trial and comparison against
ground truth): in Federal Bank's printed table, a deposit row shows
"amount | balance" with a pipe between the transaction amount and the
running balance. A withdrawal row shows "amount balance" with NO pipe.
That's how this parser tells the two columns apart.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bank_parser_core import BankStatementParser, DR_CR_RE


TRAN_TYPE_ORDER = ["NEFT", "RTGS", "IMPS", "CLG", "ATM", "CHQ", "SYS", "TFR", "FT"]

DATE_RE    = re.compile(r'\b(\d{2}-(?:JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)-\d{4})\b')
TRAN_ID_RE = re.compile(r'(?<![/\w])([A-Z8\$S])[\$]?(\d{6,12})\b')


def clean_amount_str(s):
    return re.sub(r'[^\d.,]', '', s)


class FederalBankParser(BankStatementParser):

    BANK_NAME = "Federal Bank"
    DETECT_KEYWORDS = ["Federal Bank", "federalbank.co.in", "FDRL"]

    # Federal Bank's own column layout — matches its printed statement
    EXCEL_HEADERS = ['Date', 'Value Date', 'Particulars', 'Tran Type', 'Tran ID',
                      'Withdrawals', 'Deposits', 'Balance', 'DR/CR']
    EXCEL_COL_WIDTHS = {'A': 14, 'B': 14, 'C': 55, 'D': 10, 'E': 16,
                        'F': 14, 'G': 14, 'H': 16, 'I': 8}
    EXCEL_NUMBER_COLS = {6, 7, 8}   # Withdrawals, Deposits, Balance

    def to_excel_row(self, tx):
        return [
            tx.get('date', ''), tx.get('value_date', ''), tx.get('particulars', ''),
            tx.get('tran_type', ''), tx.get('tran_id', ''),
            tx.get('withdrawals'), tx.get('deposits'), tx.get('balance'),
            tx.get('dr_cr', ''),
        ]

    def parse_line(self, line):
        dates = DATE_RE.findall(line)
        if not dates:
            return None

        date       = dates[0]
        value_date = dates[1] if len(dates) > 1 else date

        dr_cr_match = DR_CR_RE.search(line)
        dr_cr = dr_cr_match.group(1).upper() if dr_cr_match else None

        tran_type = None
        for tt in TRAN_TYPE_ORDER:
            if re.search(r'\b' + tt + r'\b', line):
                tran_type = tt
                break

        tran_id_match = TRAN_ID_RE.search(line)
        if tran_id_match:
            prefix = tran_id_match.group(1)
            prefix = 'S' if prefix in ('$', 'S', '8') else prefix
            tran_id = prefix + tran_id_match.group(2)
        else:
            tran_id = None

        # ── Amount extraction — the pipe-separator trick ───────────────────
        amount_tokens = re.findall(r'[\$S]?\s*(\d[\d,]*\.\d{2})', line)
        cleaned = []
        for a in amount_tokens:
            s = clean_amount_str(a)
            if s:
                try:
                    cleaned.append(float(s))
                except ValueError:
                    pass

        withdrawals = deposits = balance = None

        if len(cleaned) >= 2:
            pipe_between = bool(re.search(r'\d+\.\d{2}\s*\|\s*\d+\.\d{2}', line))
            balance = cleaned[-1]
            txn_amount = cleaned[-2]
            if pipe_between:
                deposits = txn_amount
            else:
                withdrawals = txn_amount
        elif len(cleaned) == 1:
            balance = cleaned[0]

        # ── Particulars cleanup ─────────────────────────────────────────────
        particulars = line
        for d in dates:
            particulars = particulars.replace(d, '')
        if dr_cr_match:
            particulars = particulars[:dr_cr_match.start()]
        particulars = re.sub(r'\b(Cr|Dr|CR|DR)\b', '', particulars)

        for tt in ["TFR", "FT", "CLG", "SYS", "ATM", "CHQ", "NEFT", "RTGS"]:
            particulars = re.sub(r'\b' + tt + r'\b', '', particulars)
        particulars = re.sub(r'\bIMPS/IFI\b', '', particulars)

        if tran_id:
            particulars = particulars.replace(tran_id, '')
            particulars = re.sub(r'[\$S]' + tran_id[1:] + r'\b', '', particulars)

        particulars = re.sub(r'[\$S]?\s*\d[\d,]*\.\d{2}', '', particulars)
        particulars = re.sub(r'\s[\$S]\d+', '', particulars)
        particulars = re.sub(r'\b\d{8,}\b', '', particulars)
        particulars = re.sub(r'[|\\]+', ' ', particulars)
        particulars = re.sub(r'[_—]{2,}', '', particulars)
        particulars = re.sub(r'(?<!\w)-{2,}(?!\w)', '', particulars)
        particulars = re.sub(r'\s[}\]{>\-]\s', ' ', particulars)
        particulars = re.sub(r'\s{2,}', ' ', particulars).strip()

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
