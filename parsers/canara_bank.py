"""
parsers/canara_bank.py
Canara Bank statement parser — subclass of BankStatementParser.

This is structurally the hardest layout encountered so far: unlike every
other bank, a single transaction's date, amount, and balance do NOT
reliably sit on the same OCR line as each other or as the bulk of the
particulars text. The OCR table-merge scatters them depending on where
the multi-line particulars happen to wrap. What IS reliable:

  - every transaction block ends with a "Chq: <number>" line (sometimes
    "Cha: 0" due to OCR misreading "q" as "a")
  - somewhere inside that block, exactly one line contains the
    DD-MM-YYYY date plus the transaction amount plus the running
    balance, all three together
  - every other line in the block is pure particulars text

So this parser does NOT use the shared line-by-line parse_line() /
parse_page_text() flow from the base class at all — it overrides
parse_page_text() to chunk the page into "Chq:"-delimited blocks first,
then extracts date/amount/balance from whichever line in the block has
them, and treats the rest as particulars.

Other quirks:
  - this account runs in heavy overdraft — balances are negative
    throughout (e.g. -50,93,164.05). The amount regex and balance
    arithmetic must handle a leading minus sign correctly.
  - a DISCLAIMER block appears on the same page as "Closing Balance",
    directly after the last real transaction, with no dates inside it —
    naturally excluded since parsing is date-anchored, but skip markers
    are added for clean output anyway.
  - "Deposits" appears before "Withdrawals" in the column header (the
    reverse of every other bank seen so far) — but since this parser
    determines direction by testing both balance directions rather than
    column position, that ordering doesn't actually matter here.
"""

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bank_parser_core import BankStatementParser


DATE_RE   = re.compile(r'\b(\d{2}-\d{2}-\d{4})\b')
AMOUNT_RE = re.compile(r'(-?\d{1,3}(?:,\d{2,3})*\s?\.\d{2})')
# "Chq:" sometimes OCRs as "Cha:" — accept both, with or without a value
CHQ_RE    = re.compile(r'\bCh[qa]\s*:\s*(\S*)', re.IGNORECASE)


def clean_amount(s):
    neg = s.strip().startswith('-')
    digits = re.sub(r'[^\d.]', '', s)
    if not digits:
        return None
    val = float(digits)
    return -val if neg else val


class CanaraBankParser(BankStatementParser):

    BANK_NAME = "Canara Bank"
    DETECT_KEYWORDS = ["Canara Bank", "CANARA BANK", "CNRB0", "canarabank.com"]

    SKIP_LINE_MARKERS = BankStatementParser.SKIP_LINE_MARKERS + [
        'Customer Id', 'Branch Code', 'Branch Name', 'IFSC Code',
        'Name ', 'Phone ', 'Statement for A/c', 'page ',
        'DISCLAIMER', 'UNLESS THE CONSTITUENT', 'BEWARE OF PHISHING',
        'IMB USERS', 'CHANGE IN THE ADDRESS', 'DO NOT SHARE ATM',
        'Details of Ombudsman', 'The Banking Ombudsman', 'E-mail: bo',
        'ARE YOU A MERCHANT', 'COMPUTER OUTPUT', 'END OF STATEMENT',
        'Date Particulars Deposits', 'Closing Balance',
    ]

    # ── Metadata ──────────────────────────────────────────────────────────────

    def extract_metadata(self, text):
        meta = {}
        patterns = {
            'name':            r'Name\s+([A-Z][A-Z\s]+?)\s+Branch',
            'account_number':  r'Statement for A/c\s+(\S+)',
            'period':          r'between\s+(\S+\s+\S+\s+\S+)\s+and\s+(\S+\s+\S+\s+\S+)',
            'ifsc':            r'IFSC Code\s+(\S+)',
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.IGNORECASE)
            if m:
                if key == 'period':
                    meta[key] = f"{m.group(1)} to {m.group(2)}"
                else:
                    meta[key] = m.group(1).strip()

        opening_match = re.search(
            r'Opening Balance\s*(-?[\d,]+\.\d{2})', text, re.IGNORECASE
        )
        if opening_match:
            val = clean_amount(opening_match.group(1))
            if val is not None:
                meta['opening_balance'] = val

        return meta

    # ── parse_line() is required by the abstract base class, but this
    # bank's layout doesn't fit a single-line model — see parse_page_text()
    # below, which is fully overridden and never calls this method. ────────
    def parse_line(self, line):
        return None

    # ── Block-based parsing (overrides the line-by-line base class flow) ────

    def parse_page_text(self, text, page_avg_conf, prev_balance_hint=None):
        """
        Canara's OCR scrambles transaction anchors badly: a block's date+
        amount+balance line frequently does NOT land inside that block at
        all — it gets shoved into a LATER block, sometimes several blocks
        away, and even THEN the anchors within that later block are not
        necessarily in the same order as the particulars blocks waiting
        for them (see module docstring for a worked example).

        Text position alone cannot resolve this. The fix: use the
        BALANCE CHAIN itself as the ordering key. Given a running
        balance, only one of the page's orphaned anchors can correctly
        chain from it (prev_balance ± amount == that anchor's balance).
        Greedily picking the one that reconciles, repeating with the new
        running balance, reconstructs the true sequence even though the
        anchors appeared scrambled in the OCR text.

        prev_balance_hint lets the running balance carry over from the
        previous page, so cross-page anchor ordering is also correct.
        """
        kept_lines = []
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if any(marker in line for marker in self.SKIP_LINE_MARKERS):
                continue
            kept_lines.append(line)

        # Split into Chq:-delimited blocks
        raw_blocks = []
        current_block = []
        for line in kept_lines:
            current_block.append(line)
            if CHQ_RE.search(line):
                raw_blocks.append(current_block)
                current_block = []
        if current_block:
            raw_blocks.append(current_block)

        # Extract anchors and particulars-only text from every block,
        # keeping track of which block each piece of particulars belongs to
        block_particulars = []   # list of (chq_no, particulars_text) per block, in order
        all_anchors = []         # flat list of {date, amount, balance} across the page

        for block in raw_blocks:
            particulars_lines = []
            chq_no = None
            for line in block:
                chq_match = CHQ_RE.search(line)
                if chq_match:
                    chq_no = chq_match.group(1) if chq_match.group(1) else chq_no
                    line = line[:chq_match.start()].strip()
                    if not line:
                        continue

                date_match = DATE_RE.search(line)
                amounts_in_line = AMOUNT_RE.findall(line)

                if date_match and len(amounts_in_line) >= 2:
                    cleaned = [clean_amount(a) for a in amounts_in_line]
                    cleaned = [c for c in cleaned if c is not None]
                    if len(cleaned) >= 2:
                        all_anchors.append({
                            'date':    date_match.group(1),
                            'amount':  abs(cleaned[-2]),
                            'balance': cleaned[-1],
                        })
                    remainder = line
                    remainder = remainder.replace(date_match.group(1), '')
                    for a in amounts_in_line:
                        remainder = remainder.replace(a, '')
                    remainder = remainder.strip()
                    if remainder:
                        particulars_lines.append(remainder)
                elif line:
                    particulars_lines.append(line)

            particulars_text = ' '.join(particulars_lines)
            particulars_text = re.sub(r'\s{2,}', ' ', particulars_text).strip()
            particulars_text = re.sub(r'^[\s,\-]+|[\s,]+$', '', particulars_text)

            # Skip blocks that ended up with no particulars at all (rare;
            # e.g. a block that was pure anchor text with nothing else)
            if particulars_text or chq_no:
                block_particulars.append({'chq_no': chq_no, 'text': particulars_text})

        # Resolve the true anchor order via balance-chain matching
        ordered_anchors = self._resolve_anchor_order(all_anchors, prev_balance_hint)

        # Pair ordered anchors with particulars blocks, position by position
        transactions = []
        for block_info, anchor in zip(block_particulars, ordered_anchors):
            tran_type = None
            text_upper = block_info['text'].upper()
            for tt in ["NEFT", "RTGS", "MB", "SI", "ATM", "IMPS", "CHQ",
                       "COMM", "SL", "SC", "BY CLG", "FOLIO", "CASA"]:
                if text_upper.startswith(tt):
                    tran_type = tt
                    break

            is_wd = anchor.get('is_withdrawal')
            transactions.append({
                'date':        anchor['date'],
                'value_date':  anchor['date'],
                'particulars': block_info['text'],
                'tran_type':   tran_type or '',
                'tran_id':     block_info['chq_no'] or '',
                'withdrawals': anchor['amount'] if is_wd is True else None,
                'deposits':    anchor['amount'] if is_wd is not True else None,
                'balance':     anchor['balance'],
                'dr_cr':       '',
                '_low_conf':   page_avg_conf < 60,
            })

        return transactions

    @staticmethod
    def _resolve_anchor_order(anchors, prev_balance_hint=None):
        """
        Greedily reorder a list of {date, amount, balance} anchors so the
        balance chain reconciles: at each step, pick whichever remaining
        anchor's balance equals (running_balance ± its amount). This
        recovers the true document order even when Tesseract emitted the
        anchors out of sequence.

        Also records which direction (withdrawal vs deposit) made each
        anchor reconcile, as 'is_withdrawal' — this replaces the need for
        a separate balance-direction-inference pass afterward.

        If no anchor reconciles at some step (OCR error on that specific
        row), fall back to taking the next anchor in original order so
        the page doesn't lose transactions — the balance validator will
        flag that row downstream instead.
        """
        remaining = list(anchors)
        ordered = []
        running = prev_balance_hint

        while remaining:
            if running is None:
                chosen = remaining.pop(0)
                chosen['is_withdrawal'] = None   # unknown; no anchor to compare from
                ordered.append(chosen)
                running = chosen['balance']
                continue

            match_idx = None
            match_is_wd = None
            for idx, a in enumerate(remaining):
                if abs(running - a['amount'] - a['balance']) <= 0.02:
                    match_idx = idx
                    match_is_wd = True
                    break
                if abs(running + a['amount'] - a['balance']) <= 0.02:
                    match_idx = idx
                    match_is_wd = False
                    break

            if match_idx is not None:
                chosen = remaining.pop(match_idx)
                chosen['is_withdrawal'] = match_is_wd
            else:
                chosen = remaining.pop(0)
                chosen['is_withdrawal'] = None   # ambiguous; will be flagged

            ordered.append(chosen)
            running = chosen['balance']

        return ordered

    # ── Override parse_pdf to thread the running balance across pages ───────

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
        running_balance = None   # carries across pages for anchor-order resolution

        for i, img in enumerate(images, 1):
            text, avg_conf = self.ocr_page(img)

            if not meta_extracted:
                m = self.extract_metadata(text)
                if m:
                    meta = m
                    meta_extracted = True
                    if 'opening_balance' in m:
                        opening_balance = m['opening_balance']
                        running_balance = opening_balance

            if avg_conf < 60:
                low_conf_pages.append(i)

            page_txns = self.parse_page_text(text, avg_conf, prev_balance_hint=running_balance)
            all_transactions.extend(page_txns)

            if page_txns and page_txns[-1].get('balance') is not None:
                running_balance = page_txns[-1]['balance']

            if progress_callback:
                progress_callback(i, total)

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

    # ── Canara Bank's own column layout (Deposits before Withdrawals) ───────
    EXCEL_HEADERS = ['Date', 'Particulars', 'Chq.No.', 'Deposits',
                      'Withdrawals', 'Balance']
    EXCEL_COL_WIDTHS = {'A': 12, 'B': 55, 'C': 14, 'D': 14, 'E': 14, 'F': 16}
    EXCEL_NUMBER_COLS = {4, 5, 6}   # Deposits, Withdrawals, Balance

    def to_excel_row(self, tx):
        return [
            tx.get('date', ''),
            tx.get('particulars', ''),
            tx.get('tran_id', ''),
            tx.get('deposits'),
            tx.get('withdrawals'),
            tx.get('balance'),
        ]
