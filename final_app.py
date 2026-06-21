"""
app.py
Modern Tkinter GUI (ttkbootstrap) for the multi-bank statement parser.

Run with: python app.py

Requires:
    pip install ttkbootstrap

Features:
    - Pick a PDF file
    - Choose a bank from dropdown, or let it auto-detect
    - Set output Excel path
    - Dark, centered, icon-button layout
    - Progress bar + a single live status line
    - Open output folder / file once parsing finishes
"""

import os
import sys
import ctypes
import threading
import subprocess
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from datetime import datetime

try:
    import ttkbootstrap as tb
    from ttkbootstrap.constants import *
    from ttkbootstrap.dialogs import Messagebox
except ImportError:
    print(
        "This UI needs the 'ttkbootstrap' package.\n"
        "Install it with:\n\n    pip install ttkbootstrap\n"
    )
    sys.exit(1)

from pdf2image import convert_from_path
import pytesseract

from registry import get_parser_names, get_parser_class, detect_bank

# ── EDIT THESE if poppler/tesseract aren't on your system PATH ────────────────
POPPLER_PATH  = r"C:\Users\O M E N\Downloads\Release-26.02.0-0\poppler-26.02.0\Library\bin"   # e.g. r"C:\poppler\Library\bin"
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"  # e.g. r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ── Appearance ────────────────────────────────────────────────────────────────
THEME = "darkly"   # other dark options: "cyborg", "vapor", "solar"


def get_base_dir():
    """
    Return the folder this app lives in — works whether running as a plain
    .py script or as a packaged .exe (e.g. via PyInstaller).
    """
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    else:
        return Path(__file__).resolve().parent


def get_output_dir():
    """Return the 'output' folder next to the app, creating it if needed."""
    out_dir = get_base_dir() / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def make_output_filename(pdf_path):
    """
    Build a timestamped output filename from the source PDF name, e.g.
    'federal_bank_20260620_154301.xlsx' — so repeated runs never overwrite
    each other.
    """
    stem = Path(pdf_path).stem
    timestamp = datetime.now().strftime("%d-%m-%Y_%H-%M-%S")
    return f"{stem}_{timestamp}.xlsx"


class BankParserApp(tb.Window):

    def __init__(self):
        super().__init__(
            title="Bank Statement Parser",
            themename=THEME,
            size=(680, 780),
            minsize=(560, 680),
            resizable=(True, True),
        )

        self.pdf_path      = tk.StringVar()
        self.output_path   = tk.StringVar()
        self.bank_choice   = tk.StringVar(value="Auto-detect")
        self.pdf_status    = tk.StringVar(value="No PDF selected")
        self.output_status = tk.StringVar(value="Default output folder")
        self.status_text   = tk.StringVar(value="Choose a PDF to begin.")

        self._build_ui()
        self.after(10, self._apply_dark_titlebar)

    # ── UI layout ────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = tb.Frame(self, padding=(36, 24))
        outer.pack(fill=BOTH, expand=YES)

        # Centered content block — when the window is taller than the content
        # needs, the extra space is split evenly above/below instead of
        # collecting at the bottom.
        content = tb.Frame(outer)
        content.pack(fill=X, expand=YES)

        self._build_header(content)
        self._build_pdf_picker(content)
        self._build_bank_dropdown(content)
        self._build_output_picker(content)
        self._build_action(content)
        self._build_progress(content)
        self._build_open_buttons(content)

        tb.Label(
            self,
            text="© 2026 Niveth VM",
            bootstyle="secondary",
            font=("Segoe UI", 6)
        ).place(
            relx=0.99,
            rely=0.99,
            anchor="se"
        )

        # tb.Label(
        #     self,
        #     text="Tip: edit POPPLER_PATH / TESSERACT_CMD at the top of app.py if OCR tools aren't found.",
        #     bootstyle="secondary",
        #     font=("Segoe UI", 8),
        # ).pack(side=BOTTOM, pady=(0, 8))

    def _build_header(self, parent):
        header = tb.Frame(parent)
        header.pack(pady=(8, 22))

        title_row = tb.Frame(header)
        title_row.pack()
        tb.Label(title_row, text="📄", font=("Segoe UI", 20)).pack(side=LEFT, padx=(0, 10))
        tb.Label(title_row, text="Bank Statement Parser", font=("Segoe UI", 20, "bold")).pack(side=LEFT)

        tb.Label(
            header, text="Convert PDF Statements to Excel", bootstyle="secondary", font=("Segoe UI", 10)
        ).pack(pady=(4, 0))

    def _build_pdf_picker(self, parent):
        tb.Button(
            parent, text="📂  Select PDF File", bootstyle="primary", command=self.pick_pdf, padding=16
        ).pack(fill=X)
        tb.Label(parent, textvariable=self.pdf_status, bootstyle="secondary").pack(pady=(6, 22))

    def _build_bank_dropdown(self, parent):
        tb.Label(parent, text="BANK", bootstyle="light", font=("Segoe UI",10)).pack(pady=(0, 2))   # ← line 150: the caption text
        bank_options = ["Auto-detect"] + get_parser_names()                                            # ← line 151: dropdown list items
        self.bank_dropdown = tb.Combobox(
            parent, textvariable=self.bank_choice, values=bank_options,
            state='readonly', bootstyle="primary", width=22, justify=CENTER,                            # ← line 154: color/width of the box
        )
        self.bank_dropdown.pack(pady=(0, 22))

    def _build_output_picker(self, parent):
        tb.Button(
            parent, text="💾  Choose Output Location", bootstyle="secondary", command=self.pick_output, padding=6
        ).pack(fill=X)
        tb.Label(parent, textvariable=self.output_status, bootstyle="secondary").pack(pady=(6, 26))

    def _build_action(self, parent):
        self.run_btn = tb.Button(
            parent, text="🚀  Parse Statement", bootstyle="primary", command=self.run_parse, padding=18
        )
        self.run_btn.pack(fill=X, pady=(0, 16))

    def _build_progress(self, parent):
        self.progress = tb.Progressbar(parent, mode='determinate', bootstyle="success")
        self.progress.pack(fill=X, pady=(0, 8))
        tb.Label(parent, textvariable=self.status_text, bootstyle="success").pack(pady=(0, 22))

    def _build_open_buttons(self, parent):
        row = tb.Frame(parent)
        row.pack(pady=(0, 18))
        self.open_folder_btn = tb.Button(
            row, text="📁 Open Output Folder", bootstyle="secondary-outline",
            command=self.open_output_folder, state='disabled', width=22,
        )
        self.open_folder_btn.pack(side=LEFT, padx=4)
        self.open_file_btn = tb.Button(
            row, text="📊 Open Excel File", bootstyle="secondary-outline",
            command=self.open_output_file, state='disabled', width=22,
        )
        self.open_file_btn.pack(side=LEFT, padx=4)

    # ── Windows dark titlebar (best-effort, silently skipped elsewhere) ─────

    def _apply_dark_titlebar(self):
        if sys.platform != 'win32':
            return
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            DWMWA_USE_IMMERSIVE_DARK_MODE = 20
            value = ctypes.c_int(1)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, DWMWA_USE_IMMERSIVE_DARK_MODE, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass

    # ── File pickers ─────────────────────────────────────────────────────────

    def pick_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if path:
            self.pdf_path.set(path)
            self.pdf_status.set(Path(path).name)
            default_output = str(get_output_dir() / make_output_filename(path))
            self.output_path.set(default_output)
            self.output_status.set(Path(default_output).name)
            self.status_text.set("Ready to parse.")

    def pick_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialdir=str(get_output_dir()),
            filetypes=[("Excel files", "*.xlsx")],
        )
        if path:
            self.output_path.set(path)
            self.output_status.set(Path(path).name)

    # ── Parsing logic (runs in background thread) ──────────────────────────

    def run_parse(self):
        if not self.pdf_path.get():
            Messagebox.show_warning(message="Please choose a PDF file first.", title="No file")
            return
        if not self.output_path.get():
            Messagebox.show_warning(message="Please choose where to save the Excel file.", title="No output")
            return

        self.run_btn.config(state='disabled', text="Parsing…")
        self.open_folder_btn.config(state='disabled')
        self.open_file_btn.config(state='disabled')
        self.progress['value'] = 0

        thread = threading.Thread(target=self._parse_worker, daemon=True)
        thread.start()

    def _parse_worker(self):
        try:
            pdf_path    = self.pdf_path.get()
            output_path = self.output_path.get()
            chosen_bank = self.bank_choice.get()

            if TESSERACT_CMD:
                pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

            # Determine bank
            if chosen_bank == "Auto-detect":
                self._set_status("Detecting bank from page 1…")
                kwargs = {'dpi': 150, 'first_page': 1, 'last_page': 1}
                if POPPLER_PATH:
                    kwargs['poppler_path'] = POPPLER_PATH
                first_page_img = convert_from_path(pdf_path, **kwargs)[0]
                first_page_text = pytesseract.image_to_string(first_page_img)

                detected = detect_bank(first_page_text)
                if not detected:
                    self._set_status("Could not auto-detect bank.")
                    self._show_error(
                        "Auto-detect failed",
                        "Could not identify the bank from this PDF.\n"
                        "Please select the bank manually from the dropdown.",
                    )
                    self._reset_buttons()
                    return
                bank_name = detected
                self._set_status(f"Detected: {bank_name}")
            else:
                bank_name = chosen_bank

            parser_cls = get_parser_class(bank_name)
            if not parser_cls:
                self._show_error("Unsupported bank", f"No parser available for {bank_name} yet.")
                self._reset_buttons()
                return

            parser = parser_cls(
                tesseract_cmd=TESSERACT_CMD,
                poppler_path=POPPLER_PATH,
                dpi=300,
            )

            self._set_status(f"Parsing with {bank_name} parser…")

            def progress_cb(current, total):
                pct = int((current / total) * 100)
                self.progress['value'] = pct
                self._set_status(f"OCR page {current}/{total}… ({pct}%)")

            transactions, meta = parser.parse_pdf(pdf_path, progress_callback=progress_cb)

            self._set_status("Writing Excel file…")
            parser.write_excel(transactions, meta, output_path)

            flagged = sum(1 for t in transactions if t.get('_balance_flag') or t.get('_low_conf'))
            self._set_status(f"Done — {len(transactions)} transactions, {flagged} flagged for review.")
            self.open_folder_btn.config(state='normal')
            self.open_file_btn.config(state='normal')

        except Exception as e:
            self._show_error("Error", str(e))
            self._set_status("Failed — see error message.")
        finally:
            self._reset_buttons()

    # ── Status helpers ───────────────────────────────────────────────────────

    def _set_status(self, text):
        self.status_text.set(text)
        self.update_idletasks()

    def _show_error(self, title, message):
        self.after(0, lambda: Messagebox.show_error(message=message, title=title))

    def _reset_buttons(self):
        self.run_btn.config(state='normal', text="🚀  Parse Statement")

    # ── Open file / folder ───────────────────────────────────────────────────

    def _open_path(self, path):
        if sys.platform == 'win32':
            os.startfile(path)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', path])
        else:
            subprocess.Popen(['xdg-open', path])

    def open_output_folder(self):
        self._open_path(str(Path(self.output_path.get()).parent))

    def open_output_file(self):
        self._open_path(self.output_path.get())


if __name__ == '__main__':
    app = BankParserApp()
    app.mainloop()