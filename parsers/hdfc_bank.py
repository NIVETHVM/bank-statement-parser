"""
parsers/hdfc_bank.py
HDFC Bank statement parser — subclass of BankStatementParser.

Layout:
  Date  Narration  Chq./Ref.No.  Value Dt  Withdrawal Amt.  Deposit Amt.  Closing Balance

KEY DIFFERENCE from other banks: HDFC's table has two separate amount
columns (Withdrawal / Deposit), but Tesseract's --psm 4 OCR collapses
them into a single number per row — there is no punctuation, suffix, or
pipe character that tells you which column the amount came from.

So this parser CANNOT determine withdrawal vs deposit from the line
text alone. Instead it uses balance-direction inference as the primary
mechanism (not just a safety net like in other parsers):
    prev_balance - amount == new_balance  -> withdrawal
    prev_balance + amount == new_balance  -> deposit
This is resolved in a second pass after all rows are extracted, once
every row's raw amount and balance are known.

Other quirks handled:
  - every transaction's narration wraps onto a second OCR line
  - balances can go negative (e.g. "-17,097.22") before a same-day
    reversal — the regex must accept a leading minus sign
  - the opening balance for the very first transaction is NOT printed
    anywhere on the statement itself, so the first row's direction is
    inferred from the "Opening Balance" in the STATEMENT SUMMARY block
    on the last page when available, else left as a best-guess deposit
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bank_parser_core import BankStatementParser


DATE_RE   = re.compile(r'\b(\d{2}/\d{2}/\d{2})\b')
# Allow an optional leading minus sign for negative running balances
AMOUNT_RE = re.compile(r'(-?\d{1,3}(?:,\d{2,3})*\s?\.\d{2})')

OPENING_BAL_RE = re.compile(r'Opening Balance.*?\n\s*([\d,]+\.\d{2})', re.IGNORECASE | re.DOTALL)


def clean_amount(s):
    neg = s.strip().startswith('-')
    digits = re.sub(r'[^\d.]', '', s)
    if not digits:
        return None
    val = float(digits)
    return -val if neg else val


class HDFCBankParser(BankStatementParser):

    BANK_NAME = "HDFC Bank"
    DETECT_KEYWORDS = ["HDFC BANK", "HDFC Bank", "hdfcbank.com"]

    SKIP_LINE_MARKERS = BankStatementParser.SKIP_LINE_MARKERS + [
        'We understand your world', 'Account Branch', 'Address -',
        'City :', 'State :', 'Phone no.', 'OD Limit', 'Currency :',
        'Email :', 'Cust ID', 'Account No :', 'A/C Open Date',
        'Account Status', 'RTGS/NEFT IFSC', 'Branch Code', 'Nomination',
        'JOINT HOLDERS', 'STATEMENT SUMMARY', 'Opening Balance',
        'Generated On', 'Requesting Branch', 'computer generated statement',
        'HDFC BANK LIMITED', 'Closing balance includes', 'Contents of this',
        'State account branch', 'GSTIN number details', 'Registered Office',
        'M/S.', 'C/O ', 'Date  Narration', 'Date Narration',
    ]

    # ── Metadata ──────────────────────────────────────────────────────────────

    def extract_metadata(self, text):
        meta = {}
        patterns = {
            'name':            r'M/S\.\s*([^\n]+)',
            'account_number':  r'Account No\s*:\s*(\S+)',
            'period':          r'From\s*:\s*(\S+)\s+To\s*:\s*(\S+)',
            'ifsc':            r'IFSC:\s*(\S+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                if key == 'period':
                    meta[key] = f"{m.group(1)} to {m.group(2)}"
                else:
                    meta[key] = m.group(1).strip()
        return meta

    def extract_opening_balance(self, all_pages_text):
        """Search every page for the STATEMENT SUMMARY's Opening Balance figure."""
        for text in all_pages_text:
            m = OPENING_BAL_RE.search(text)
            if m:
                val = clean_amount(m.group(1))
                if val is not None:
                    return val
        return None

    # ── Per-line parsing ─────────────────────────────────────────────────────
    # NOTE: withdrawals/deposits cannot be split here — both fields are left
    # in a temporary '_raw_amount' key and resolved in resolve_directions().

    def parse_line(self, line):
        dates = DATE_RE.findall(line)
        if not dates:
            return None

        date       = dates[0]
        value_date = dates[1] if len(dates) > 1 else date

        amount_tokens = AMOUNT_RE.findall(line)
        cleaned = [clean_amount(a) for a in amount_tokens]
        cleaned = [c for c in cleaned if c is not None]

        raw_amount = balance = None
        if len(cleaned) >= 2:
            balance    = cleaned[-1]
            raw_amount = abs(cleaned[-2])   # magnitude only; direction resolved later
        elif len(cleaned) == 1:
            balance = cleaned[0]

        # ── Chq./Ref.No. — long digit/alnum token right after the narration ──
        # HDFC ref numbers are typically 10-16 digits, sometimes prefixed
        # with letters (e.g. MB01090552625T50, ICICR22025041408864161).
        tran_id_match = re.search(r'\b([A-Z]{0,6}\d{8,18}[A-Z0-9]{0,4})\b', line)
        tran_id = tran_id_match.group(1) if tran_id_match else None

        # ── Particulars cleanup ─────────────────────────────────────────────
        particulars = line
        for d in dates:
            particulars = particulars.replace(d, '')
        for a in amount_tokens:
            particulars = particulars.replace(a, '')
        if tran_id:
            particulars = particulars.replace(tran_id, '')
        particulars = re.sub(r'[|\\]+', ' ', particulars)
        particulars = re.sub(r'\s{2,}', ' ', particulars).strip()
        particulars = re.sub(r'^[\s,\-]+|[\s,]+$', '', particulars)

        tran_type = None
        for tt in ["IMPS", "NEFT", "RTGS", "UPI", "ACH", "DOMIMPS01", "FT", "CHQ", "NWD"]:
            if particulars.upper().startswith(tt):
                tran_type = tt
                break

        return {
            'date':        date,
            'value_date':  value_date,
            'particulars': particulars,
            'tran_type':   tran_type or '',
            'tran_id':     tran_id or '',
            'withdrawals': None,        # resolved later
            'deposits':    None,        # resolved later
            'balance':     balance,
            'dr_cr':       '',
            '_raw_amount': raw_amount,  # internal only — not written to Excel
        }

    # ── Direction resolution (runs after all pages are parsed) ──────────────

    def resolve_directions(self, transactions, opening_balance=None):
        """
        Walk the transaction list and assign each '_raw_amount' to either
        withdrawals or deposits by checking which direction makes the
        balance chain reconcile against the previous row's balance.
        """
        prev_balance = opening_balance

        for tx in transactions:
            amount = tx.pop('_raw_amount', None)
            bal    = tx.get('balance')

            if amount is None or bal is None:
                continue

            if prev_balance is None:
                # No anchor yet — can't infer direction for the very first
                # row without an opening balance. Default to deposit and
                # let validate_balances() flag/fix it if wrong.
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
                # Neither direction reconciles (likely an OCR slip on this
                # row's amount/balance) — default to deposit; the shared
                # validate_balances() pass will flag it for review.
                tx['deposits'] = amount

            prev_balance = bal

        return transactions

    # ── Override parse_pdf to inject the direction-resolution pass ──────────

    def parse_pdf(self, pdf_path, progress_callback=None):
        # Run the standard pipeline first (OCR + parse_line + validate)
        # but we need the opening balance and raw OCR text from every page
        # before validate_balances() runs, so we re-implement the loop here
        # rather than calling super().parse_pdf() directly.
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
        all_pages_text  = []
        meta            = {}
        meta_extracted  = False
        low_conf_pages  = []

        for i, img in enumerate(images, 1):
            text, avg_conf = self.ocr_page(img)
            all_pages_text.append(text)

            if not meta_extracted:
                m = self.extract_metadata(text)
                if m:
                    meta = m
                    meta_extracted = True

            if avg_conf < 60:
                low_conf_pages.append(i)

            page_txns = self.parse_page_text(text, avg_conf)
            all_transactions.extend(page_txns)

            if progress_callback:
                progress_callback(i, total)

        # Resolve withdrawal vs deposit for every row using balance direction
        opening_balance = self.extract_opening_balance(all_pages_text)
        all_transactions = self.resolve_directions(all_transactions, opening_balance)

        # Now run the shared balance validator/auto-corrector as a final pass
        all_transactions = self.validate_balances(all_transactions)

        for tx in reversed(all_transactions):
            if tx.get('balance') is not None:
                meta['closing_balance'] = tx['balance']
                break

        meta['bank']            = self.BANK_NAME
        meta['low_conf_pages']  = low_conf_pages
        meta['total_pages']     = total
        if opening_balance is not None:
            meta['opening_balance'] = opening_balance

        return all_transactions, meta

    # ── HDFC's own column layout ──────────────────────────────────────────────
    EXCEL_HEADERS = ['Date', 'Narration', 'Chq./Ref.No.', 'Value Dt',
                      'Withdrawal Amt.', 'Deposit Amt.', 'Closing Balance']
    EXCEL_COL_WIDTHS = {'A': 12, 'B': 50, 'C': 18, 'D': 12,
                        'E': 16, 'F': 16, 'G': 16}
    EXCEL_NUMBER_COLS = {5, 6, 7}   # Withdrawal, Deposit, Balance

    def to_excel_row(self, tx):
        return [
            tx.get('date', ''),
            tx.get('particulars', ''),
            tx.get('tran_id', ''),
            tx.get('value_date', ''),
            tx.get('withdrawals'),
            tx.get('deposits'),
            tx.get('balance'),
        ]
