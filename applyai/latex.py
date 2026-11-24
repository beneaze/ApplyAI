from __future__ import annotations

import re
import subprocess
import unicodedata
from pathlib import Path

from .log import log


class LatexError(RuntimeError):
    pass


# Symbols pdflatex's utf8 input can't typeset (it stops with "Unicode character ... not set up").
# The writer model sometimes uses them in prose ("π-phase", "≈ 20 dB"), so swap in LaTeX commands.
_SYMBOLS = {
    "≈": r"\ensuremath{\approx}", "≤": r"\ensuremath{\leq}", "≥": r"\ensuremath{\geq}",
    "≠": r"\ensuremath{\neq}", "∼": r"\ensuremath{\sim}", "∝": r"\ensuremath{\propto}",
    "∞": r"\ensuremath{\infty}", "√": r"\ensuremath{\surd}", "∆": r"\ensuremath{\Delta}",
    "Ω": r"\ensuremath{\Omega}", "−": r"\ensuremath{-}", "⋅": r"\ensuremath{\cdot}",
    "→": r"\ensuremath{\rightarrow}", "←": r"\ensuremath{\leftarrow}",
    "↔": r"\ensuremath{\leftrightarrow}", "⇒": r"\ensuremath{\Rightarrow}",
    "′": r"\ensuremath{'}", "‑": "-", "\u202f": "\\,", "\u2009": "\\,",
    "μ": r"\textmu{}",  # Greek mu used as the micro sign (μm)
}
_GREEK_LATIN = {"Alpha": "A", "Beta": "B", "Epsilon": "E", "Zeta": "Z", "Eta": "H", "Iota": "I",
                "Kappa": "K", "Mu": "M", "Nu": "N", "Rho": "P", "Tau": "T", "Chi": "X"}
_GREEK_CAPS = {"Gamma", "Delta", "Theta", "Lambda", "Xi", "Pi", "Sigma", "Upsilon", "Phi", "Psi", "Omega"}


def _symbol(ch: str) -> str | None:
    if ch in _SYMBOLS:
        return _SYMBOLS[ch]
    if "\u0391" <= ch <= "\u03c9":
        name = unicodedata.name(ch, "")
        m = re.fullmatch(r"GREEK (SMALL|CAPITAL) LETTER (FINAL )?(\w+)", name)
        if m:
            letter = m.group(3).capitalize() if m.group(1) == "CAPITAL" else m.group(3).lower()
            if m.group(2):
                return r"\ensuremath{\varsigma}"
            if letter in ("omicron", "Omicron"):
                return letter[0]
            if m.group(1) == "CAPITAL" and letter not in _GREEK_CAPS:
                return _GREEK_LATIN.get(letter)
            return r"\ensuremath{\%s}" % letter.replace("Lamda", "Lambda").replace("lamda", "lambda")
    return None


def make_tex_safe(src: str) -> str:
    """Replace Unicode symbols pdflatex can't handle with LaTeX commands (text-, math-mode safe)."""
    out = []
    for ch in src:
        rep = _symbol(ch) if ord(ch) > 0xFF else None
        out.append(rep if rep is not None else ch)
    return "".join(out)


def compile_pdf(tex: Path) -> Path:
    """pdflatex twice-safe compile in the file's own folder; returns the PDF path."""
    src = tex.read_text(encoding="utf-8")
    safe = make_tex_safe(src)
    if safe != src:
        tex.write_text(safe, encoding="utf-8")
    cmd = ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", tex.name]
    p = subprocess.run(cmd, cwd=tex.parent, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    pdf = tex.with_suffix(".pdf")
    if p.returncode != 0 or not pdf.exists():
        err = re.findall(r"^! .*(?:\n.*){0,3}", p.stdout, re.M)
        raise LatexError(f"pdflatex failed for {tex.name}: {' | '.join(err)[:1200]}")
    for ext in (".aux", ".log", ".out"):
        tex.with_suffix(ext).unlink(missing_ok=True)
    return pdf


def pages(pdf: Path) -> int:
    out = subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    m = re.search(r"^Pages:\s+(\d+)", out, re.M)
    return int(m.group(1)) if m else 0


def overflow_lines(pdf: Path) -> int:
    """How many text lines spilled past page 1 (0 if it fits)."""
    if pages(pdf) <= 1:
        return 0
    txt = subprocess.run(["pdftotext", "-f", "2", "-layout", str(pdf), "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    return sum(1 for l in txt.splitlines() if l.strip())


def page_fill(pdf: Path) -> float:
    """Fraction of page 1 height used by text (rough; via bbox of the last text line)."""
    out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-bbox", str(pdf), "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    h = re.search(r'<page width="[\d.]+" height="([\d.]+)"', out)
    ys = [float(y) for y in re.findall(r'yMax="([\d.]+)"', out)]
    return max(ys) / float(h.group(1)) if h and ys else 0.0


_ESC = {"&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_", "{": r"\{", "}": r"\}",
        "~": r"\textasciitilde{}", "^": r"\textasciicircum{}", "\\": r"\textbackslash{}"}


def escape(s: str) -> str:
    return "".join(_ESC.get(ch, ch) for ch in s)


def runts(pdf: Path, max_words: int = 2) -> list[str]:
    """Bullet lines whose last line holds only 1-2 words ("Harvard", "TWPA)") — they look
    accidental and waste a full line. Returns the full text of each affected bullet."""
    import html
    out = subprocess.run(["pdftotext", "-f", "1", "-l", "1", "-bbox-layout", str(pdf), "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    width = float(re.search(r'<page width="([\d.]+)"', out).group(1))
    lines = []
    for ln in re.findall(r"<line[^>]*>(.*?)</line>", out, re.S):
        words = [(float(x), html.unescape(w)) for x, w in
                 re.findall(r'<word xMin="([\d.]+)"[^>]*>([^<]*)</word>', ln)]
        left = [(x, w) for x, w in words if x < width * 0.68]  # ignore the date/location column
        if left:
            lines.append(left)
    found, bullet = [], None
    indent = None
    for words in lines:
        first_x, first = words[0]
        if first in ("·", "⋅", "•"):
            bullet = [w for _, w in words[1:]]
            indent = words[1][0] if len(words) > 1 else None
            continue
        if bullet is not None and indent is not None and abs(first_x - indent) < 3:
            text = [w for _, w in words]
            if len(text) <= max_words:
                found.append(" ".join(bullet + text))
            bullet += text
            continue
        bullet = None
    return found
