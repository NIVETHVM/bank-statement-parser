# Bank Statement Parser

Extracts transactions from scanned bank statement PDFs into Excel.
Built as an object-oriented system: one shared base class handles OCR,
Excel writing, and balance validation; each bank gets its own small
subclass that only implements the column-parsing logic specific to
that bank's layout.

## Setup

1. Install Python packages:
   ```
   pip install -r requirements.txt
   ```

2. Install Tesseract OCR (Windows): download from
   https://github.com/UB-Mannheim/tesseract/wiki and note the install path
   (default: `C:\Program Files\Tesseract-OCR\tesseract.exe`)

3. Install Poppler (Windows): download from
   https://github.com/oschwartz10612/poppler-windows/releases,
   extract anywhere, and note the path to its `Library\bin` folder.

4. Open `app.py` and set these two lines near the top:
   ```python
   POPPLER_PATH  = r"C:\path\to\poppler\Library\bin"
   TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
   ```

## Running

```
python app.py
```

This opens the GUI:
1. Browse and pick your PDF statement
2. Choose the bank from the dropdown, or leave it on "Auto-detect"
3. The output path auto-fills to an `output/` folder created next to
   `app.py` (or next to the .exe, if packaged), with the filename
   timestamped as `<pdf_name>_YYYYMMDD_HHMMSS.xlsx` so repeated runs
   never overwrite each other. Click "Choose…" if you want a different
   location instead.
4. Click "Parse Statement"

The output Excel has two sheets:
- **Transactions** — every row, with yellow highlighting on flagged rows
  (low OCR confidence, or balance chain doesn't reconcile)
- **Summary** — account info, totals, flagged row count

## Project structure

```
bank_parser/
├── app.py                    GUI entry point — run this
├── registry.py                maps bank name → parser class, auto-detect
├── bank_parser_core.py        abstract base class (shared logic)
├── parsers/
│   └── federal_bank.py        Federal Bank specific parsing rules
└── requirements.txt
```

## Adding a new bank

1. Get a sample PDF statement from the new bank.
2. Run a quick OCR test to see the raw text layout:
   ```python
   from pdf2image import convert_from_path
   import pytesseract
   img = convert_from_path("sample.pdf", dpi=300, first_page=1, last_page=1)[0]
   print(pytesseract.image_to_string(img, config='--psm 4 --oem 3'))
   ```
3. Create `parsers/<bank_name>.py`:
   ```python
   import sys
   from pathlib import Path
   sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
   from bank_parser_core import BankStatementParser

   class HDFCParser(BankStatementParser):
       BANK_NAME = "HDFC Bank"
       DETECT_KEYWORDS = ["HDFC Bank", "hdfcbank.com"]

       def parse_line(self, line):
           # your bank-specific regex logic here
           # must return a dict with: date, value_date, particulars,
           # tran_type, tran_id, withdrawals, deposits, balance, dr_cr
           # or None if the line isn't a transaction
           ...
   ```
4. Register it in `registry.py`:
   ```python
   from parsers.hdfc import HDFCParser

   PARSER_REGISTRY = {
       "Federal Bank": FederalBankParser,
       "HDFC Bank": HDFCParser,
   }
   ```
5. Done — it now shows up in the GUI dropdown and auto-detect.

## Notes

- Default OCR DPI is 300 — this gave 100% balance accuracy and 98.7%+
  field accuracy on the Federal Bank test set. Lower DPI is faster but
  noticeably less accurate; don't go below 250 without re-validating.
- The balance validator (`prev_balance ± amount == current_balance`)
  runs automatically on every parser and flags rows that don't
  reconcile — useful for catching OCR errors a regex pattern alone
  would miss.
