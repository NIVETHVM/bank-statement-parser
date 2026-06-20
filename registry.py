"""
registry.py
Central registry mapping bank names to their parser classes.

To add a new bank:
    1. Create parsers/<bank_name>.py with a class subclassing BankStatementParser
    2. Import it below
    3. Add one line to PARSER_REGISTRY
That's it — the GUI dropdown and auto-detect both pick it up automatically.
"""

from parsers.federal_bank import FederalBankParser
from parsers.indusind_bank import IndusIndBankParser
from parsers.idbi_bank import IDBIBankParser

# Bank display name → parser class
PARSER_REGISTRY = {
    "Federal Bank": FederalBankParser,
    "IndusInd Bank": IndusIndBankParser,
    "IDBI Bank": IDBIBankParser,
    # "HDFC Bank": HDFCParser,        # add when ready
    # "SBI": SBIParser,               # add when ready
    # "ICICI Bank": ICICIParser,      # add when ready
}


def get_parser_names():
    """Return list of bank names for the GUI dropdown."""
    return list(PARSER_REGISTRY.keys())


def get_parser_class(bank_name):
    """Return the parser class for a given bank name, or None."""
    return PARSER_REGISTRY.get(bank_name)


def detect_bank(first_page_text):
    """
    Try to auto-detect which bank a statement belongs to by checking
    each registered parser's DETECT_KEYWORDS against the OCR'd first page.
    Returns the bank name, or None if no match found.
    """
    for bank_name, parser_cls in PARSER_REGISTRY.items():
        for keyword in parser_cls.DETECT_KEYWORDS:
            if keyword.lower() in first_page_text.lower():
                return bank_name
    return None
