#!/usr/bin/env python3
"""
Universal Filename Translator
==============================
Translate folder and file names from any language to any language.
Uses Google Translate free endpoint — no API key, no sign-up, no daily quota.
Names are batched (up to 100 per request) for maximum speed.

Requirements:
    Python 3.8+  (tkinterdnd2 optional — enables drag & drop)

Run:
    python universal_filename_translator.py
"""

import os
import re
import sys
import json
import time
import shutil
import threading
import urllib.request
import urllib.parse
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    HAS_DND = False


# ─────────────────────────────────────────────────────────
# Language list  (display name → Google language code)
# ─────────────────────────────────────────────────────────
LANGUAGES = {
    "Auto-detect":          "auto",
    "Arabic":               "ar",
    "Chinese (Simplified)": "zh-CN",
    "Chinese (Traditional)":"zh-TW",
    "Czech":                "cs",
    "Danish":               "da",
    "Dutch":                "nl",
    "English":              "en",
    "Finnish":              "fi",
    "French":               "fr",
    "German":               "de",
    "Greek":                "el",
    "Hebrew":               "iw",
    "Hindi":                "hi",
    "Hungarian":            "hu",
    "Indonesian":           "id",
    "Italian":              "it",
    "Japanese":             "ja",
    "Korean":               "ko",
    "Norwegian":            "no",
    "Persian":              "fa",
    "Polish":               "pl",
    "Portuguese":           "pt",
    "Romanian":             "ro",
    "Russian":              "ru",
    "Spanish":              "es",
    "Swedish":              "sv",
    "Thai":                 "th",
    "Turkish":              "tr",
    "Ukrainian":            "uk",
    "Vietnamese":           "vi",
}

# Ordered list for display
LANG_NAMES = list(LANGUAGES.keys())

# Default source / target
DEFAULT_SRC = "Auto-detect"
DEFAULT_TGT = "English"


# ─────────────────────────────────────────────────────────
# Translation logic
# ─────────────────────────────────────────────────────────

# Separator that survives Google Translate intact and is illegal in filenames
SEP     = " ║ "
SEP_KEY = "║"

# Max names per batch — 100 is reliable and fast (~0.5–1s per batch)
MAX_BATCH = 100
DELAY_BETWEEN_BATCHES = 0.2  # seconds — light courtesy delay

# Windows reserved filenames
_WIN_RESERVED = re.compile(
    r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$', re.IGNORECASE
)

# Matches any Unicode letter (works for Chinese, Arabic, Latin, Cyrillic, etc.)
_HAS_LETTER = re.compile(r'[^\W\d]', re.UNICODE)

# CJK Unified Ideographs + common extensions, Hiragana, Katakana, Hangul
_HAS_CJK = re.compile(
    r'[\u4e00-\u9fff'          # CJK Unified Ideographs (main block)
    r'\u3400-\u4dbf'          # CJK Extension A
    r'\U00020000-\U0002a6df'  # CJK Extension B
    r'\u3040-\u309f'          # Hiragana
    r'\u30a0-\u30ff'          # Katakana
    r'\uac00-\ud7af]',        # Hangul syllables
    re.UNICODE
)

# Scripts that are non-Latin and benefit from translation
_HAS_NON_LATIN = re.compile(
    r'[\u0400-\u04ff'          # Cyrillic
    r'\u0600-\u06ff'          # Arabic
    r'\u0900-\u097f'          # Devanagari
    r'\u0e00-\u0e7f'          # Thai
    r'\u0370-\u03ff]',        # Greek
    re.UNICODE
)

# Plain ASCII/Latin letters — these don't need translation for ZH→EN or similar
_ONLY_LATIN = re.compile(r'^[\x00-\x7f\u0080-\u024f\s\d\W]*$', re.UNICODE)


def needs_translation(text: str, src_lang: str) -> bool:
    """
    Decide whether a filename stem needs to be sent for translation.

    Strategy:
    - If source is a CJK language (zh-*, ja, ko): only translate names that
      actually contain CJK / Hiragana / Katakana / Hangul characters.  Pure
      ASCII/Latin names are skipped — they are already in the target script.
    - If source is a Cyrillic/Arabic/Greek/etc. language: only translate names
      that contain characters from that script family.
    - If source is "auto" or a Latin-script language: translate any name that
      has at least one letter (original behaviour, catches e.g. Italian→English).
    """
    t = text.strip()
    if not _HAS_LETTER.search(t):
        return False  # pure numbers / codes — skip always

    # CJK source languages: only send if there are actual CJK characters
    if src_lang in ("zh-CN", "zh-TW", "ja", "ko"):
        return bool(_HAS_CJK.search(t))

    # Cyrillic / Arabic / Devanagari / Thai / Greek sources
    if src_lang in ("ru", "uk", "bg", "ar", "fa", "hi", "th", "el"):
        return bool(_HAS_NON_LATIN.search(t))

    # Auto-detect or Latin-script sources: translate anything with a letter
    return True

def sanitize_filename(name: str) -> str:
    """
    Make a string safe as a filename on Windows, macOS, and Linux.
    Covers: illegal chars (incl. /), reserved names, trailing dots/spaces.
    """
    # Replace all Windows-illegal chars with dash (/ is the most common culprit)
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '-', name)
    # Collapse runs of dashes and clean edges
    name = re.sub(r'-{2,}', '-', name)
    name = re.sub(r'\s+', ' ', name).strip().strip('-').strip('.')
    # Windows reserved device names
    if _WIN_RESERVED.match(name):
        name = name + '_file'
    return name or 'unnamed'

def make_batches(items: list) -> list:
    """Split a flat list into sub-lists of at most MAX_BATCH items."""
    return [items[i:i + MAX_BATCH] for i in range(0, len(items), MAX_BATCH)]

def translate_batch(stems: list, src_lang: str, tgt_lang: str,
                    retries: int = 3) -> list:
    """
    Translate a list of stems in ONE Google Translate request using ║ as separator.
    Also requests language detection (dt=ld) from the API.

    Returns (translated_list, detected_src_language).
    - If the detected source language equals the target language, the caller
      should keep the originals (names are already in the target language).
    - Falls back to (originals, None) on failure.
    """
    joined = SEP.join(stems)
    for attempt in range(retries):
        try:
            url = (
                "https://translate.googleapis.com/translate_a/single"
                f"?client=gtx&sl={src_lang}&tl={tgt_lang}&dt=t&dt=ld"
                f"&q={urllib.parse.quote(joined)}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())

            raw = ''.join(part[0] for part in data[0] if part[0])
            detected_lang = data[2] if len(data) > 2 else None

            # If detected source == target, names are already in target language
            if detected_lang == tgt_lang:
                return stems, detected_lang  # keep originals unchanged

            # Detect API failure: empty or echoed input
            if not raw or raw.strip() == joined.strip():
                if attempt < retries - 1:
                    time.sleep(2.0 * (attempt + 1))
                    continue
                return stems, detected_lang

            parts = [p.strip() for p in raw.split(SEP_KEY)]

            if len(parts) == len(stems):
                return parts, detected_lang

            # Count mismatch — split in half and recurse
            if attempt == retries - 1 and len(stems) > 1:
                mid = len(stems) // 2
                left,  dl = translate_batch(stems[:mid],  src_lang, tgt_lang, 2)
                right, _  = translate_batch(stems[mid:],  src_lang, tgt_lang, 2)
                return left + right, dl

            if attempt < retries - 1:
                time.sleep(1.5)

        except Exception:
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))

    return stems, None  # final fallback

def collect_unique_names(folder: str) -> list:
    names = set()
    for root, dirs, files in os.walk(folder):
        for d in dirs:
            names.add(d)
        for f in files:
            names.add(f)
    return sorted(names)

def win_path(p: str) -> str:
    """Prefix with \\\\?\\ on Windows to bypass 260-char MAX_PATH limit."""
    if os.name == 'nt' and not p.startswith('\\\\?\\'):
        return '\\\\?\\' + os.path.abspath(p)
    return p

def copy_translated(src: str, dst: str, translation_map: dict,
                    errors: list = None) -> list:
    """
    Recursively copy src → dst, renaming every entry via translation_map.
    Errors are collected instead of crashing.
    """
    if errors is None:
        errors = []
    try:
        os.makedirs(win_path(dst), exist_ok=True)
    except OSError as e:
        errors.append(f"Cannot create folder '{dst}': {e}")
        return errors

    try:
        entries = list(os.scandir(win_path(src)))
    except OSError as e:
        errors.append(f"Cannot read folder '{src}': {e}")
        return errors

    for entry in entries:
        new_name = translation_map.get(entry.name, entry.name)
        src_path = entry.path
        dst_path = os.path.join(dst, new_name)
        try:
            if entry.is_dir(follow_symlinks=False):
                copy_translated(src_path, dst_path, translation_map, errors)
            else:
                shutil.copy2(win_path(src_path), win_path(dst_path))
        except OSError as e:
            errors.append(f"'{entry.name}' → '{new_name}': {e}")
    return errors


# ─────────────────────────────────────────────────────────
# App
# ─────────────────────────────────────────────────────────
class UniversalTranslatorApp:
    BG          = "#1e1e2e"
    PANEL       = "#2a2a3e"
    ACCENT      = "#5b8dee"
    ACCENT_DARK = "#3a6bcc"
    SUCCESS     = "#4caf85"
    WARNING     = "#e8a838"
    ERROR       = "#e05c5c"
    TEXT        = "#dde1f0"
    SUBTEXT     = "#8888aa"
    BORDER      = "#3a3a55"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Universal Filename Translator")
        self.root.configure(bg=self.BG)
        self.root.resizable(True, True)
        self.root.minsize(640, 600)

        w, h = 720, 660
        sw = root.winfo_screenwidth()
        sh = root.winfo_screenheight()
        root.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

        self._src_folder = tk.StringVar()
        self._cancel_flag = threading.Event()
        self._is_running = False
        self._last_output = None

        self._build_ui()

        if HAS_DND:
            self._drop_area.drop_target_register(DND_FILES)
            self._drop_area.dnd_bind("<<Drop>>", self._on_drop)

    # ── UI ────────────────────────────────────────────────
    def _build_ui(self):
        root = self.root

        # ── Header
        header = tk.Frame(root, bg="#16162a", pady=14)
        header.pack(fill="x")
        tk.Label(
            header, text="Universal Filename Translator",
            font=("Segoe UI", 16, "bold"),
            bg="#16162a", fg=self.TEXT
        ).pack()
        tk.Label(
            header,
            text="Translate folder & file names between any two languages  ·  Free, no API key needed",
            font=("Segoe UI", 9),
            bg="#16162a", fg=self.SUBTEXT
        ).pack()

        content = tk.Frame(root, bg=self.BG, padx=24, pady=16)
        content.pack(fill="both", expand=True)

        # ── Language selection row
        lang_frame = tk.Frame(content, bg=self.PANEL, pady=12, padx=16)
        lang_frame.pack(fill="x", pady=(0, 14))

        tk.Label(lang_frame, text="From:", font=("Segoe UI", 9),
                 bg=self.PANEL, fg=self.SUBTEXT).grid(row=0, column=0, sticky="e", padx=(0,6))

        self._src_lang_var = tk.StringVar(value=DEFAULT_SRC)
        src_menu = ttk.Combobox(
            lang_frame, textvariable=self._src_lang_var,
            values=LANG_NAMES, state="readonly", width=22,
            font=("Segoe UI", 10)
        )
        src_menu.grid(row=0, column=1, padx=(0, 16))

        # Swap button
        swap_btn = tk.Button(
            lang_frame, text="⇄",
            font=("Segoe UI", 12),
            bg=self.PANEL, fg=self.ACCENT,
            activebackground=self.BORDER,
            activeforeground=self.ACCENT,
            relief="flat", padx=6, pady=0,
            cursor="hand2",
            command=self._swap_languages
        )
        swap_btn.grid(row=0, column=2, padx=(0, 16))

        tk.Label(lang_frame, text="To:", font=("Segoe UI", 9),
                 bg=self.PANEL, fg=self.SUBTEXT).grid(row=0, column=3, sticky="e", padx=(0,6))

        self._tgt_lang_var = tk.StringVar(value=DEFAULT_TGT)
        tgt_menu = ttk.Combobox(
            lang_frame, textvariable=self._tgt_lang_var,
            values=[l for l in LANG_NAMES if l != "Auto-detect"],
            state="readonly", width=22,
            font=("Segoe UI", 10)
        )
        tgt_menu.grid(row=0, column=4)

        # ── Drop / browse area
        self._drop_area = tk.Frame(
            content, bg=self.PANEL,
            highlightthickness=2,
            highlightbackground=self.BORDER,
            highlightcolor=self.ACCENT,
            cursor="hand2"
        )
        self._drop_area.pack(fill="x", pady=(0, 14))
        self._drop_area.bind("<Button-1>", lambda e: self._browse())

        tk.Label(
            self._drop_area, text="📂",
            font=("Segoe UI Emoji", 28),
            bg=self.PANEL, fg=self.TEXT
        ).pack(pady=(14, 4))

        self._drop_label = tk.Label(
            self._drop_area,
            text="Click to select folder" + ("  or  drag & drop here" if HAS_DND else ""),
            font=("Segoe UI", 10),
            bg=self.PANEL, fg=self.SUBTEXT
        )
        self._drop_label.pack()

        self._folder_label = tk.Label(
            self._drop_area, text="",
            font=("Segoe UI", 9, "bold"),
            bg=self.PANEL, fg=self.ACCENT,
            wraplength=580, justify="center"
        )
        self._folder_label.pack(pady=(4, 14))

        # ── Output suffix row
        out_row = tk.Frame(content, bg=self.BG)
        out_row.pack(fill="x", pady=(0, 12))
        tk.Label(
            out_row, text="Output folder suffix:",
            font=("Segoe UI", 9), bg=self.BG, fg=self.SUBTEXT
        ).pack(side="left")
        self._suffix_var = tk.StringVar(value="_translated")
        tk.Entry(
            out_row, textvariable=self._suffix_var, width=18,
            font=("Consolas", 10),
            bg=self.PANEL, fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat", bd=4,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.ACCENT
        ).pack(side="left", padx=(8, 0))
        tk.Label(
            out_row, text="  (saved next to your original folder)",
            font=("Segoe UI", 9), bg=self.BG, fg=self.SUBTEXT
        ).pack(side="left")

        # ── Status + progress
        prog_frame = tk.Frame(content, bg=self.BG)
        prog_frame.pack(fill="x", pady=(0, 6))

        self._status_label = tk.Label(
            prog_frame, text="Ready",
            font=("Segoe UI", 9, "bold"),
            bg=self.BG, fg=self.SUBTEXT, anchor="w"
        )
        self._status_label.pack(fill="x")

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Custom.Horizontal.TProgressbar",
            troughcolor=self.PANEL, background=self.ACCENT,
            bordercolor=self.BORDER, lightcolor=self.ACCENT,
            darkcolor=self.ACCENT, thickness=10
        )
        self._progress = ttk.Progressbar(
            prog_frame, style="Custom.Horizontal.TProgressbar",
            orient="horizontal", mode="determinate", maximum=100
        )
        self._progress.pack(fill="x", pady=(4, 0))

        self._count_label = tk.Label(
            prog_frame, text="",
            font=("Segoe UI", 8),
            bg=self.BG, fg=self.SUBTEXT, anchor="e"
        )
        self._count_label.pack(fill="x")

        # ── Log
        log_outer = tk.Frame(content, bg=self.BORDER, bd=1)
        log_outer.pack(fill="both", expand=True, pady=(4, 12))

        self._log = tk.Text(
            log_outer,
            font=("Consolas", 9),
            bg="#141422", fg=self.TEXT,
            insertbackground=self.TEXT,
            relief="flat", bd=6,
            state="disabled",
            wrap="word",
            height=10
        )
        self._log.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(log_outer, command=self._log.yview)
        scrollbar.pack(side="right", fill="y")
        self._log["yscrollcommand"] = scrollbar.set

        self._log.tag_configure("info",    foreground=self.TEXT)
        self._log.tag_configure("success", foreground=self.SUCCESS)
        self._log.tag_configure("warn",    foreground=self.WARNING)
        self._log.tag_configure("error",   foreground=self.ERROR)
        self._log.tag_configure("dim",     foreground=self.SUBTEXT)
        self._log.tag_configure("arrow",   foreground=self.ACCENT)
        self._log.tag_configure("batch",   foreground=self.WARNING)

        # ── Buttons
        btn_row = tk.Frame(content, bg=self.BG)
        btn_row.pack(fill="x")

        self._translate_btn = tk.Button(
            btn_row,
            text="▶  Translate Names",
            font=("Segoe UI", 10, "bold"),
            bg=self.ACCENT, fg="white",
            activebackground=self.ACCENT_DARK,
            activeforeground="white",
            relief="flat", padx=22, pady=9,
            cursor="hand2",
            command=self._start_translation
        )
        self._translate_btn.pack(side="left")

        self._cancel_btn = tk.Button(
            btn_row,
            text="✕  Cancel",
            font=("Segoe UI", 10),
            bg=self.PANEL, fg=self.ERROR,
            activebackground=self.BORDER,
            activeforeground=self.ERROR,
            relief="flat", padx=16, pady=9,
            cursor="hand2",
            state="disabled",
            command=self._cancel
        )
        self._cancel_btn.pack(side="left", padx=(10, 0))

        self._open_btn = tk.Button(
            btn_row,
            text="📁  Open Output Folder",
            font=("Segoe UI", 10),
            bg=self.PANEL, fg=self.SUBTEXT,
            activebackground=self.BORDER,
            activeforeground=self.TEXT,
            relief="flat", padx=16, pady=9,
            cursor="hand2",
            state="disabled",
            command=self._open_output
        )
        self._open_btn.pack(side="right")

    # ── Actions ───────────────────────────────────────────
    def _swap_languages(self):
        src = self._src_lang_var.get()
        tgt = self._tgt_lang_var.get()
        # Don't swap if source is Auto-detect
        if src == "Auto-detect":
            self._src_lang_var.set(tgt)
            self._tgt_lang_var.set("English")
        else:
            self._src_lang_var.set(tgt)
            self._tgt_lang_var.set(src)

    def _browse(self):
        if self._is_running:
            return
        folder = filedialog.askdirectory(title="Select folder to translate")
        if folder:
            self._set_folder(folder)

    def _on_drop(self, event):
        path = event.data.strip().strip("{}")
        if os.path.isdir(path):
            self._set_folder(path)

    def _set_folder(self, folder: str):
        self._src_folder.set(folder)
        name = os.path.basename(folder)
        self._folder_label.config(text=f"{name}   ({folder})")
        self._drop_label.config(text="Folder selected — click to change")
        self._log_clear()
        self._set_status("Ready", self.SUBTEXT)
        self._progress["value"] = 0
        self._count_label.config(text="")
        self._open_btn.config(state="disabled")
        self._last_output = None

    def _start_translation(self):
        src = self._src_folder.get()
        if not src or not os.path.isdir(src):
            messagebox.showerror("No folder selected", "Please select a folder first.")
            return

        src_lang_name = self._src_lang_var.get()
        tgt_lang_name = self._tgt_lang_var.get()

        if src_lang_name == tgt_lang_name:
            messagebox.showerror("Same language", "Source and target language are the same.")
            return

        src_lang = LANGUAGES[src_lang_name]
        tgt_lang = LANGUAGES[tgt_lang_name]

        suffix = self._suffix_var.get().strip() or "_translated"
        parent = os.path.dirname(src)
        folder_name = os.path.basename(src)
        dst = os.path.join(parent, folder_name + suffix)

        if os.path.exists(dst):
            if not messagebox.askyesno(
                "Output folder exists",
                f"'{os.path.basename(dst)}' already exists.\nOverwrite it?"
            ):
                return
            shutil.rmtree(dst)

        self._cancel_flag.clear()
        self._is_running = True
        self._translate_btn.config(state="disabled")
        self._cancel_btn.config(state="normal")
        self._open_btn.config(state="disabled")
        self._log_clear()

        threading.Thread(
            target=self._run_translation,
            args=(src, dst, src_lang, src_lang_name, tgt_lang, tgt_lang_name),
            daemon=True
        ).start()

    def _cancel(self):
        self._cancel_flag.set()
        self._log_append("Cancelling after current batch…\n", "warn")
        self._cancel_btn.config(state="disabled")

    def _open_output(self):
        if self._last_output and os.path.isdir(self._last_output):
            if sys.platform == "win32":
                os.startfile(self._last_output)
            elif sys.platform == "darwin":
                os.system(f'open "{self._last_output}"')
            else:
                os.system(f'xdg-open "{self._last_output}"')

    # ── Worker thread ─────────────────────────────────────
    def _run_translation(self, src, dst, src_lang, src_lang_name,
                         tgt_lang, tgt_lang_name):
        try:
            self._set_status("Scanning folder…", self.ACCENT)
            self._log_append(f"Source:  {src}\n", "dim")
            self._log_append(f"Output:  {dst}\n", "dim")
            self._log_append(
                f"Language:  {src_lang_name}  →  {tgt_lang_name}\n\n", "dim"
            )

            all_names = collect_unique_names(src)

            # Determine which names need translation
            to_translate = [
                n for n in all_names
                if needs_translation(os.path.splitext(n)[0], src_lang)
            ]
            total = len(to_translate)
            batches = make_batches(to_translate)

            self._log_append(
                f"Found {len(all_names)} unique names — "
                f"{total} to translate in {len(batches)} batch(es) "
                f"(up to {MAX_BATCH} per batch).\n\n",
                "info"
            )

            translation_map = self._build_map_with_cancel(
                all_names, src_lang, src_lang_name,
                tgt_lang, tgt_lang_name, total, batches
            )

            if translation_map is None:
                self._finish(cancelled=True)
                return

            # Copy
            self._log_append("\nCopying files with translated names…\n", "dim")
            self._set_status("Copying files…", self.ACCENT)
            self._set_progress(88, "Copying…")
            copy_errors = copy_translated(src, dst, translation_map)

            total_files = sum(len(files) for _, _, files in os.walk(dst))
            self._set_progress(100, "Done")
            self._last_output = dst

            if copy_errors:
                self._log_append(
                    f"\n⚠  Completed with {len(copy_errors)} skipped file(s):\n",
                    "warn"
                )
                for err in copy_errors[:20]:
                    self._log_append(f"  • {err}\n", "warn")
                if len(copy_errors) > 20:
                    self._log_append(
                        f"  … and {len(copy_errors) - 20} more\n", "warn"
                    )
                self._log_append(
                    f"\n{total_files} file(s) copied to:\n{dst}\n", "success"
                )
                self._finish(cancelled=False, partial=True)
            else:
                self._log_append(
                    f"\n✓  Done!  {total_files} file(s) copied to:\n{dst}\n",
                    "success"
                )
                self._finish(cancelled=False)

        except Exception as e:
            self._log_append(f"\n✗ Error: {e}\n", "error")
            self._finish(cancelled=False, error=True)

    def _build_map_with_cancel(self, all_names, src_lang, src_lang_name,
                                tgt_lang, tgt_lang_name, total, batches) -> dict:
        translation_map = {}
        to_translate_names = []
        to_translate_stems = []
        to_translate_exts  = []

        for name in all_names:
            stem, ext = os.path.splitext(name)
            if needs_translation(stem, src_lang):
                to_translate_names.append(name)
                to_translate_stems.append(stem)
                to_translate_exts.append(ext)
            else:
                translation_map[name] = name

        skipped_no_letters = len(all_names) - len(to_translate_names)
        if skipped_no_letters:
            self._log_append(
                f"Skipped {skipped_no_letters} names with no letters "
                f"(pure numbers / codes — e.g. '26', '001-002').\n",
                "dim"
            )

        if not to_translate_names:
            self._log_append("No names need translation.\n", "warn")
            return translation_map

        done = 0
        skipped_already_target = 0
        idx = 0
        # Accumulate batch results between UI renders to avoid flooding root.after
        pending_log_batches = []

        all_batches = make_batches(to_translate_stems)
        n_batches = len(all_batches)

        for b_num, batch_stems in enumerate(all_batches):
            if self._cancel_flag.is_set():
                return None

            translated_stems, detected_lang = translate_batch(
                batch_stems, src_lang, tgt_lang
            )
            # NOTE: do NOT use detected_lang == tgt_lang to skip whole batches
            # here, because needs_translation() has already filtered the list
            # to only names that actually need translating.  The batch-level
            # language detection is unreliable on short / mixed inputs.
            already_in_target = False

            batch_results = []  # (original, translated, was_skipped)
            for i, stem in enumerate(batch_stems):
                original_name = to_translate_names[idx]
                ext           = to_translate_exts[idx]
                safe = sanitize_filename(translated_stems[i])
                translated_name = (safe or stem) + ext
                translation_map[original_name] = translated_name
                batch_results.append((original_name, translated_name, False))
                idx += 1

            done += len(batch_stems)
            pending_log_batches.append((b_num + 1, batch_results, already_in_target, detected_lang))

            # ── Throttled UI update: render accumulated batches every N batches
            #    or on the very last batch, to prevent flooding the Tk mainloop.
            is_last = (b_num == n_batches - 1)
            if len(pending_log_batches) >= self._LOG_EVERY_N_BATCHES or is_last:
                for args in pending_log_batches:
                    self._log_append_batch_coloured(*args)
                pending_log_batches = []

            pct = int((done / total) * 80) if total else 80
            self._set_progress(pct, f"Translating… {done} / {total}")
            self._count_label_set(f"{done} / {total}")
            time.sleep(DELAY_BETWEEN_BATCHES)

        if skipped_already_target:
            self._log_append(
                f"\n✓ Skipped {skipped_already_target} names already in "
                f"{tgt_lang_name} — kept unchanged.\n",
                "dim"
            )

        return translation_map

    # ── UI helpers (thread-safe) ──────────────────────────
    def _finish(self, cancelled: bool, error: bool = False,
                partial: bool = False):
        def _ui():
            self._is_running = False
            self._translate_btn.config(state="normal")
            self._cancel_btn.config(state="disabled")
            if cancelled:
                self._set_status("Cancelled", self.WARNING)
                self._set_progress(0, "")
            elif error:
                self._set_status("Error — see log above", self.ERROR)
            elif partial:
                self._set_status("Done with warnings — see log above", self.WARNING)
                self._open_btn.config(state="normal")
            else:
                self._set_status("Translation complete ✓", self.SUCCESS)
                self._open_btn.config(state="normal")
        self.root.after(0, _ui)

    def _set_status(self, text: str, color: str):
        self.root.after(0, lambda: self._status_label.config(text=text, fg=color))

    def _set_progress(self, value: int, label: str = ""):
        def _ui():
            self._progress["value"] = value
            if label:
                self._status_label.config(text=label)
        self.root.after(0, _ui)

    def _count_label_set(self, text: str):
        self.root.after(0, lambda: self._count_label.config(text=text))

    # Maximum lines kept in the log widget — keeps it fast for large jobs
    _LOG_MAX_LINES = 600
    # Only render every Nth batch in the log to prevent mainloop saturation
    # on large jobs (e.g. 140 batches for 14 000 files).  Between logged
    # batches the progress bar + counter still update every batch.
    _LOG_EVERY_N_BATCHES = 5

    def _log_append(self, text: str, tag: str = "info"):
        """Append a short message (header, status, error) with a colour tag."""
        def _ui():
            self._log.config(state="normal")
            # Prune oldest lines if log is getting too long
            current = int(self._log.index("end-1c").split(".")[0])
            if current > self._LOG_MAX_LINES:
                self._log.delete("1.0", f"{current - self._LOG_MAX_LINES // 2}.0")
            self._log.insert("end", text, tag)
            self._log.see("end")
            self._log.config(state="disabled")
        self.root.after(0, _ui)

    def _log_append_batch_coloured(self, b_num: int, results: list,
                                    already_in_target: bool,
                                    detected_lang: str):
        """
        Write an entire batch to the log in ONE scheduling call for performance,
        but with full colour tagging per segment.
        Skipped batches shown in dim; translated pairs shown in colour.
        """
        def _ui():
            self._log.config(state="normal")
            # Prune if growing too long
            current = int(self._log.index("end-1c").split(".")[0])
            if current > self._LOG_MAX_LINES:
                self._log.delete("1.0", f"{current - self._LOG_MAX_LINES // 2}.0")

            if already_in_target:
                # Whole batch already in target lang — show dim header only
                header = (
                    f"── Batch {b_num}  ──────────────────────────────\n"
                    f"  (detected: {detected_lang} = target language —"
                    f" {len(results)} names kept unchanged)\n"
                )
                self._log.insert("end", header, "dim")
            else:
                # Coloured header
                self._log.insert(
                    "end",
                    f"── Batch {b_num}  ({len(results)} names)"
                    f"  [{detected_lang or '?'}]────────────────────\n",
                    "batch"
                )
                for original, translated, skipped in results:
                    if skipped:
                        self._log.insert("end", f"  {original}\n", "dim")
                    else:
                        self._log.insert("end", f"  {original}", "info")
                        self._log.insert("end", "  →  ", "arrow")
                        self._log.insert("end", f"{translated}\n", "success")

            self._log.see("end")
            self._log.config(state="disabled")
        self.root.after(0, _ui)

    def _log_clear(self):
        def _ui():
            self._log.config(state="normal")
            self._log.delete("1.0", "end")
            self._log.config(state="disabled")
        self.root.after(0, _ui)


# ─────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────
def main():
    root = TkinterDnD.Tk() if HAS_DND else tk.Tk()
    try:
        root.iconbitmap("universal_translator.ico")
    except Exception:
        pass
    UniversalTranslatorApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
