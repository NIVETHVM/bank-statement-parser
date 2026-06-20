"""
app.py
Tkinter GUI for the multi-bank statement parser.

Run with: python app.py

Features:
    - Pick a PDF file
    - Choose a bank from dropdown, or let it auto-detect
    - Set output Excel path
    - Progress bar + live page count while parsing
    - Opens the output folder when done
"""

import os
import sys
import threading
import subprocess
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from datetime import datetime

from pdf2image import convert_from_path
import pytesseract

from registry import get_parser_names, get_parser_class, detect_bank

# ── EDIT THESE if poppler/tesseract aren't on your system PATH ────────────────
POPPLER_PATH  = r"C:\Users\O M E N\Downloads\Release-26.02.0-0\poppler-26.02.0\Library\bin"   # e.g. r"C:\poppler\Library\bin"
TESSERACT_CMD = r"C:\Program Files\Tesseract-OCR\tesseract.exe"  # e.g. r"C:\Program Files\Tesseract-OCR\tesseract.exe"

def get_base_dir():
    """
    Return the folder this app lives in — works whether running as a plain
    .py script or as a packaged .exe (e.g. via PyInstaller).
    """
    if getattr(sys, 'frozen', False):
        # Running as a bundled .exe — sys.executable is the .exe path itself
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


class BankParserApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Bank Statement Parser")
        self.geometry("560x420")
        self.resizable(False, False)

        self.pdf_path    = tk.StringVar()
        self.output_path = tk.StringVar()
        self.bank_choice = tk.StringVar(value="Auto-detect")
        self.status_text = tk.StringVar(value="Choose a PDF to begin.")

        self._build_ui()

    # ── UI layout ────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = {'padx': 16, 'pady': 8}

        title = tk.Label(self, text="Bank Statement → Excel", font=("Segoe UI", 16, "bold"))
        title.pack(pady=(20, 10))

        # PDF picker
        frame1 = tk.Frame(self)
        frame1.pack(fill='x', **pad)
        tk.Label(frame1, text="PDF file:", width=12, anchor='w').pack(side='left')
        tk.Entry(frame1, textvariable=self.pdf_path, state='readonly').pack(side='left', fill='x', expand=True, padx=(0, 8))
        tk.Button(frame1, text="Browse…", command=self.pick_pdf).pack(side='left')

        # Bank dropdown
        frame2 = tk.Frame(self)
        frame2.pack(fill='x', **pad)
        tk.Label(frame2, text="Bank:", width=12, anchor='w').pack(side='left')
        bank_options = ["Auto-detect"] + get_parser_names()
        self.bank_dropdown = ttk.Combobox(frame2, textvariable=self.bank_choice,
                                          values=bank_options, state='readonly')
        self.bank_dropdown.pack(side='left', fill='x', expand=True)

        # Output path
        frame3 = tk.Frame(self)
        frame3.pack(fill='x', **pad)
        tk.Label(frame3, text="Save as:", width=12, anchor='w').pack(side='left')
        tk.Entry(frame3, textvariable=self.output_path, state='readonly').pack(side='left', fill='x', expand=True, padx=(0, 8))
        tk.Button(frame3, text="Choose…", command=self.pick_output).pack(side='left')

        # Run button
        self.run_btn = tk.Button(self, text="Parse Statement", font=("Segoe UI", 12, "bold"),
                                 bg="#1F4E79", fg="white", command=self.run_parse, height=2)
        self.run_btn.pack(fill='x', padx=16, pady=(20, 10))

        # Progress bar
        self.progress = ttk.Progressbar(self, mode='determinate')
        self.progress.pack(fill='x', padx=16, pady=(0, 8))

        # Status label
        self.status_label = tk.Label(self, textvariable=self.status_text, fg="gray")
        self.status_label.pack(pady=(0, 10))

        # Open output folder
        self.open_btn = tk.Button(self, text="Open Output Folder", command=self.open_output_folder, state='disabled')
        self.open_btn.pack()

    # ── File pickers ─────────────────────────────────────────────────────────

    def pick_pdf(self):
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf")])
        if path:
            self.pdf_path.set(path)
            default_output = str(get_output_dir() / make_output_filename(path))
            self.output_path.set(default_output)
            self.status_text.set("Ready to parse.")

    def pick_output(self):
        path = filedialog.asksaveasfilename(
            defaultextension=".xlsx",
            initialdir=str(get_output_dir()),
            filetypes=[("Excel files", "*.xlsx")]
        )
        if path:
            self.output_path.set(path)

    # ── Parsing logic (runs in background thread) ──────────────────────────

    def run_parse(self):
        if not self.pdf_path.get():
            messagebox.showwarning("No file", "Please choose a PDF file first.")
            return
        if not self.output_path.get():
            messagebox.showwarning("No output", "Please choose where to save the Excel file.")
            return

        self.run_btn.config(state='disabled', text="Parsing…")
        self.open_btn.config(state='disabled')
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
                    self._show_error("Auto-detect failed",
                                     "Could not identify the bank from this PDF.\n"
                                     "Please select the bank manually from the dropdown.")
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
                dpi=300
            )

            self._set_status(f"Parsing with {bank_name} parser…")

            def progress_cb(current, total):
                pct = int((current / total) * 100)
                self.progress['value'] = pct
                self._set_status(f"OCR page {current}/{total}…")

            transactions, meta = parser.parse_pdf(pdf_path, progress_callback=progress_cb)

            self._set_status("Writing Excel file…")
            parser.write_excel(transactions, meta, output_path)

            flagged = sum(1 for t in transactions if t.get('_balance_flag') or t.get('_low_conf'))
            self._set_status(
                f"Done — {len(transactions)} transactions, {flagged} flagged for review."
            )
            self.open_btn.config(state='normal')

        except Exception as e:
            self._show_error("Error", str(e))
            self._set_status("Failed — see error message.")
        finally:
            self._reset_buttons()

    def _set_status(self, text):
        self.status_text.set(text)
        self.update_idletasks()

    def _show_error(self, title, message):
        self.after(0, lambda: messagebox.showerror(title, message))

    def _reset_buttons(self):
        self.run_btn.config(state='normal', text="Parse Statement")

    def open_output_folder(self):
        folder = str(Path(self.output_path.get()).parent)
        if sys.platform == 'win32':
            os.startfile(folder)
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])


if __name__ == '__main__':
    app = BankParserApp()
    app.mainloop()
