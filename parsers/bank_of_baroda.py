"""
parsers/bank_of_baroda.py
Bank of Baroda statement parser — subclass of BankStatementParser.

Layout:
  DATE  PARTICULARS  CHQ.NO.  WITHDRAWALS  DEPOSITS  BALANCE

Same core problem as HDFC: Tesseract's --psm 4 OCR collapses the
Withdrawal/Deposit columns into a single number per row with no Cr/Dr
label or pipe separator to disambiguate. So this parser also resolves
withdrawal vs deposit via balance-direction inference as the PRIMARY
mechanism, not a safety net — see resolve_directions().

Extra quirks specific to Bank of Baroda's layout:
  - the very first row (account opening) has NO transaction amount at
    all — only a balance. It must be recognized and skipped from
    direction-resolution (treated purely as the opening balance anchor).
  - amounts use Indian lakh-grouping, e.g. 5,00,000.00 (vs HDFC's
    crore-style chunking) — same regex pattern works for both since both
    only ever group in 2s after the first 3 digits, but tested separately
    to confirm.
  - a CHQ.NO. column (4-6 digit cheque/UTR number) sometimes appears
    between the particulars and the amount — must not be picked up as
    a transaction amount.
  - "Page Total: X Y Z Cr" footer lines must be skipped, not parsed as
    transactions (they repeat the same date-less running totals).
  - every transaction's particulars wraps onto a second OCR line, same
    as Federal Bank / HDFC.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bank_parser_core import BankStatementParser


DATE_RE   = re.compile(r'\b(\d{2}-\d{2}-\d{2})\b')
AMOUNT_RE = re.compile(r'(-?\d{1,3}(?:,\d{2,3})*\s?\.\d{2})')
CHQNO_RE  = re.compile(r'\b(\d{4,6})\b(?=\s+[\d,]+\.\d{2})')

OPENING_BAL_HEADER_RE = re.compile(
    r'^\s*\d{2}-\d{2}-\d{2}\s+\S.*\s+([\d,]+\.\d{2})\s*$'
)


def clean_amount(s):
    neg = s.strip().startswith('-')
    digits = re.sub(r'[^\d.]', '', s)
    if not digits:
        return None
    val = float(digits)
    return -val if neg else val


class BankOfBarodaParser(BankStatementParser):

    BANK_NAME = "Bank of Baroda"
    DETECT_KEYWORDS = ["BANK OF BARODA", "Bank of Baroda", "BARB0"]

    SKIP_LINE_MARKERS = BankStatementParser.SKIP_LINE_MARKERS + [
        'Transaction Details Page', 'NORTH CHALAKUDY', 'ADDRESS:',
        'HELPLINE NO', 'BRANCH PHONE NO', 'MICR CODE', 'A/C Name',
        'A/C Number', 'Account Open Date', 'Nomination Flag',
        'Scheme Description', 'Joint Holders', 'City :', 'Tel No.',
        'Statement of account for the period', 'Page Total:',
        'Note: Cheques received', 'returning on the basis',
        'Unless the constituent', 'DATE PARTICULARS', 'https://',
    ]

    # ── Metadata ──────────────────────────────────────────────────────────────

    def extract_metadata(self, text):
        meta = {}
        patterns = {
            'name':            r'A/C Name\s*:\s*([^\n]+)',
            'account_number':  r'A/C Number\s*:\s*(\S+)',
            'period':          r'period of\s+(\S+)\s+to\s+(\S+)',
            'ifsc':            r'IFSC CODE:\s*(\S+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                if key == 'period':
                    meta[key] = f"{m.group(1)} to {m.group(2)}"
                else:
                    meta[key] = m.group(1).strip()
        return meta

    def extract_opening_balance(self, first_page_text):
        """
        The very first transaction line has no amount column at all —
        just date, particulars, and a single trailing number that IS the
        opening balance itself (not a transaction). e.g.:
            "01-04-23 DIGITA-MUMBAI/ 14,282.85"
        Find that line and pull its number directly.
        """
        for line in first_page_text.splitlines():
            if DATE_RE.search(line):
                amounts = AMOUNT_RE.findall(line)
                if len(amounts) == 1:
                    val = clean_amount(amounts[0])
                    if val is not None:
                        return val, line.strip()
            # Stop scanning once we hit a line with 2+ amounts (a real
            # transaction with withdrawal/deposit + balance) — the
            # opening line is always the first one in the table.
            if DATE_RE.search(line) and len(AMOUNT_RE.findall(line)) >= 2:
                break
        return None, None

    # ── Per-line parsing ─────────────────────────────────────────────────────

    def parse_line(self, line):
        dates = DATE_RE.findall(line)
        if not dates:
            return None

        date       = dates[0]
        value_date = date   # Bank of Baroda statement shows only one date

        amount_tokens = AMOUNT_RE.findall(line)
        cleaned = [clean_amount(a) for a in amount_tokens]
        cleaned = [c for c in cleaned if c is not None]

        # The opening-balance-only line (one amount, no Chq/transaction
        # amount) is handled separately via extract_opening_balance() and
        # should not be emitted as a transaction row.
        if len(cleaned) < 2:
            return None

        raw_amount = balance = None
        balance    = cleaned[-1]
        raw_amount = abs(cleaned[-2])

        # ── Cheque number — 4-6 digit token sitting right before the
        # transaction amount, distinct from the amount itself ───────────────
        chq_match = CHQNO_RE.search(line)
        chq_no = chq_match.group(1) if chq_match else None

        # ── Particulars cleanup ─────────────────────────────────────────────
        particulars = line
        for d in dates:
            particulars = particulars.replace(d, '')
        for a in amount_tokens:
            particulars = particulars.replace(a, '')
        if chq_no:
            particulars = re.sub(r'\b' + chq_no + r'\b', '', particulars, count=1)
        particulars = re.sub(r'[|\\]+', ' ', particulars)
        particulars = re.sub(r'\s{2,}', ' ', particulars).strip()
        particulars = re.sub(r'^[\s,\-]+|[\s,]+$', '', particulars)

        tran_type = None
        for tt in ["NEFT", "RTGS", "UPI", "IMPS", "DIGITA", "DIGITB", "Charges"]:
            if particulars.upper().startswith(tt.upper()):
                tran_type = tt
                break

        return {
            'date':        date,
            'value_date':  value_date,
            'particulars': particulars,
            'tran_type':   tran_type or '',
            'tran_id':     chq_no or '',
            'withdrawals': None,        # resolved later
            'deposits':    None,        # resolved later
            'balance':     balance,
            'dr_cr':       '',
            '_raw_amount': raw_amount,  # internal only — not written to Excel
        }

    # ── Direction resolution (runs after all pages are parsed) ──────────────

    def resolve_directions(self, transactions, opening_balance=None):
        prev_balance = opening_balance

        for tx in transactions:
            amount = tx.pop('_raw_amount', None)
            bal    = tx.get('balance')

            if amount is None or bal is None:
                continue

            if prev_balance is None:
                tx['deposits'] = amount
                prev_balance = bal
                continue

            as_withdrawal = round(prev_balance - amount, 2)
            as_deposit    = round(prev_balance + amount, 2)

            if abs(as_withdrawal - bal) <= 0.02:
                tx['withdrawals'] = amount
            elif abs(as_deposit - bal) <= 0.02:
                tx['deposits'] = amount
            else:
                tx['deposits'] = amount   # best guess; validate_balances() will flag it

            prev_balance = bal

        return transactions

    # ── Override parse_pdf to inject the direction-resolution pass ──────────

    def parse_pdf(self, pdf_path, progress_callback=None):
        from pathlib import Path as _Path
        from pdf2image import convert_from_path as _convert

        pdf_path = _Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        kwargs = {'dpi': self.dpi}
        if self.poppler_path:
            kwargs['poppler_path'] = self.poppler_path

        images = _convert(str(pdf_path), **kwargs)
        total  = len(images)

        all_transactions = []
        meta            = {}
        meta_extracted  = False
        low_conf_pages  = []
        opening_balance = None

        for i, img in enumerate(images, 1):
            text, avg_conf = self.ocr_page(img)

            if not meta_extracted:
                m = self.extract_metadata(text)
                if m:
                    meta = m
                    meta_extracted = True

            if opening_balance is None:
                opening_balance, _ = self.extract_opening_balance(text)

            if avg_conf < 60:
                low_conf_pages.append(i)

            page_txns = self.parse_page_text(text, avg_conf)
            all_transactions.extend(page_txns)

            if progress_callback:
                progress_callback(i, total)

        all_transactions = self.resolve_directions(all_transactions, opening_balance)
        all_transactions = self.validate_balances(all_transactions)

        for tx in reversed(all_transactions):
            if tx.get('balance') is not None:
                meta['closing_balance'] = tx['balance']
                break

        meta['bank']           = self.BANK_NAME
        meta['low_conf_pages'] = low_conf_pages
        meta['total_pages']    = total
        if opening_balance is not None:
            meta['opening_balance'] = opening_balance

        return all_transactions, meta

    # ── Bank of Baroda's own column layout ──────────────────────────────────
    EXCEL_HEADERS = ['Date', 'Particulars', 'Chq.No.', 'Withdrawals',
                      'Deposits', 'Balance']
    EXCEL_COL_WIDTHS = {'A': 12, 'B': 50, 'C': 12, 'D': 14, 'E': 14, 'F': 16}
    EXCEL_NUMBER_COLS = {4, 5, 6}   # Withdrawals, Deposits, Balance

    def to_excel_row(self, tx):
        return [
            tx.get('date', ''),
            tx.get('particulars', ''),
            tx.get('tran_id', ''),
            tx.get('withdrawals'),
            tx.get('deposits'),
            tx.get('balance'),
        ]
