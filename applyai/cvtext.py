"""Read an existing CV (PDF, Word, LaTeX, Markdown, text) as plain text for the setup."""
from __future__ import annotations

import re
import subprocess
import zipfile
from pathlib import Path


def read_cv(path: Path) -> str:
    path = path.expanduser()
    if not path.exists():
        raise SystemExit(f"File not found: {path}")
    ext = path.suffix.lower()
    if ext == ".pdf":
        try:
            out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True,
                                 text=True, encoding="utf-8", errors="replace").stdout
        except FileNotFoundError:
            out = ""
        if not out.strip():
            from pdfminer.high_level import extract_text
            out = extract_text(str(path))
        if not out.strip():
            raise SystemExit("This PDF has no text layer (scanned image). Export it again as a "
                             "text PDF, or give the Word / LaTeX source.")
        return out
    if ext in (".docx", ".dotx"):
        with zipfile.ZipFile(path) as z:
            xml = z.read("word/document.xml").decode("utf-8", "replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        xml = re.sub(r"<w:tab/>", "\t", xml)
        xml = re.sub(r"<w:br/>", "\n", xml)
        text = re.sub(r"<[^>]+>", "", xml)
        import html
        return re.sub(r"\n{3,}", "\n\n", html.unescape(text)).strip()
    if ext in (".tex", ".md", ".txt", ".rtf", ".markdown"):
        return path.read_text(encoding="utf-8", errors="replace")
    if ext == ".doc":
        raise SystemExit("Old .doc files can't be read: save it as .docx or PDF first.")
    raise SystemExit(f"Unsupported CV format {ext}: use PDF, DOCX, TEX, MD or TXT.")
