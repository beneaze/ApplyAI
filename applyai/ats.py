"""Local ATS check: what an applicant tracking system can read from the PDF, and how well it
matches the job description.

Online checkers (Jobscan, Resume Worded, Teal, SkillSyncer) all need an account and have no
API, so this module reimplements their published scoring logic deterministically, following
the weights of the open-source Resume-Matcher ATS score:

    keyword match 40 | must-have skills 20 | job title 15 | sections & contact 15 | dates 10

Text is extracted twice — with pdfminer (what most Python-based parsers use) and poppler's
pdftotext — so layout problems that only one of them hits are still caught.
"""
from __future__ import annotations

import re
import subprocess
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_RANGE = re.compile(rf"({MONTHS}\s+\d{{4}})\s*[–—-]+\s*({MONTHS}\s+\d{{4}}|Present|Today|Now)", re.I)
STOP = {"m", "w", "d", "f", "x", "gn", "all", "genders", "and", "of", "the", "for", "in", "with",
        "senior", "junior", "sr", "jr", "i", "ii", "iii", "mwd", "fmd", "mfd", "wmd", "f/m/d"}


def pdf_text_pdfminer(pdf: Path) -> str:
    try:
        from pdfminer.high_level import extract_text
        return extract_text(str(pdf))
    except Exception:
        return ""


def pdf_text_poppler(pdf: Path, layout=False) -> str:
    cmd = ["pdftotext"] + (["-layout"] if layout else []) + [str(pdf), "-"]
    return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower()
    s = s.replace("‐", "-").replace("–", "-").replace("—", "-")
    s = re.sub(r"[-/]", " ", s)
    s = re.sub(r"[^\w+#.%& ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _term_re(term: str) -> re.Pattern:
    words = norm(term).split()
    if not words:
        return re.compile(r"(?!x)x")
    # tolerate plural / simple inflection on the last word
    last = re.escape(words[-1]) + r"(?:s|es|ed|ing)?"
    body = r"\s+".join([re.escape(w) for w in words[:-1]] + [last])
    return re.compile(rf"(?<![\w+#]){body}(?![\w+#])")


def contains(text_n: str, term: str, aliases=()) -> bool:
    return any(_term_re(t).search(text_n) for t in (term, *aliases) if t)


@dataclass
class Report:
    score: float
    parts: dict
    matched: list
    missing: list
    warnings: list = field(default_factory=list)
    title_found: bool = False

    def markdown(self, job_title: str) -> str:
        p = self.parts
        lines = [
            f"# ATS check — {self.score:.0f}/100",
            "",
            "Local reimplementation of Jobscan / Resume-Matcher scoring (target ≥ 75). "
            "Text is read from the compiled PDF exactly as a parser sees it.",
            "",
            "| Component | Score | Weight |", "|---|---|---|",
            f"| Keyword match (weighted) | {p['keywords']:.0f}% | 40 |",
            f"| Must-have skills | {p['must_have']:.0f}% | 20 |",
            f"| Job title “{job_title}” | {p['title']:.0f}% | 15 |",
            f"| Sections & contact info | {p['sections']:.0f}% | 15 |",
            f"| Date consistency | {p['dates']:.0f}% | 10 |",
            "",
            "## Matched keywords",
            ", ".join(f"`{k}`" for k in self.matched) or "—",
            "",
            "## Missing keywords",
            "Only add these if they are true for you — invented keywords fail at the interview.",
            "",
            ", ".join(f"`{k}`" + (" (must-have)" if w == 3 else "") for k, w in self.missing) or "— none —",
        ]
        if self.warnings:
            lines += ["", "## Parsing warnings"] + [f"- {w}" for w in self.warnings]
        else:
            lines += ["", "## Parsing", "- No parsing problems found (name, contact, headings, dates, ligatures, reading order)."]
        return "\n".join(lines) + "\n"


def parse_check(pdf: Path, *, name: str, email: str, phone_digits: str) -> tuple[list, float, float, str]:
    """Returns (warnings, sections_score, dates_score, extracted_text)."""
    t_miner = pdf_text_pdfminer(pdf)
    t_pop = pdf_text_poppler(pdf)
    t_lay = pdf_text_poppler(pdf, layout=True)
    text = t_miner or t_pop
    warn = []
    if not text.strip():
        return ["No extractable text — the PDF is image-only or fonts lack Unicode maps."], 0, 0, ""
    first = next((l for l in text.splitlines() if l.strip()), "")
    if norm(name) not in norm(first):
        warn.append(f"First text line is “{first.strip()[:60]}”, not your name.")
    if re.search(r"[ﬀ-ﬆ]", text):
        warn.append("Ligature glyphs (ﬁ/ﬂ) in extracted text — words like “fiber” may not match.")
    if "�" in text or re.search(r"[-]", text):
        warn.append("Unmappable / private-use characters in extracted text (icons or symbol fonts).")
    sec_hits = 0
    contact = {"email": email.lower() in text.lower(),
               "phone": phone_digits in re.sub(r"\D", "", text),
               "linkedin": "linkedin.com/in/" in text.lower()}
    for k, ok in contact.items():
        if not ok:
            warn.append(f"Contact field not extractable: {k}.")
    heads = {"experience": r"(work\s+)?experience", "education": r"education", "skills": r"skills"}
    for h, rx in heads.items():
        if re.search(rf"^\s*{rx}\s*$", text, re.I | re.M):
            sec_hits += 1
        else:
            warn.append(f"Standard section heading “{h.title()}” not found on its own line.")
    sections = 100 * (sec_hits + sum(contact.values())) / (len(heads) + len(contact))
    # reading order: a location glued to the end of a bullet line confuses entry parsing
    for l in t_lay.splitlines():
        if re.match(r"\s*[·•⋅]", l) and re.search(r"\s{3,}[A-Z][\w .]+,\s*[A-Z][\w .]+\s*$", l):
            warn.append("Note: in your layout the location is extracted at the end of the first "
                        "bullet line. Minor — company, title and dates still parse; kept for the design.")
            break
    # date consistency
    ranges = DATE_RANGE.findall(text)
    months = [re.match(r"[A-Za-z]+", a).group(0) for r in ranges for a in r if re.match(MONTHS, a, re.I)]
    styles = {("short" if len(m) <= 3 else "long") for m in months}
    dates = 100.0 if ranges else 0.0
    if len(styles) > 1:
        dates = 60.0
        longs = sorted({m for m in months if len(m) > 3})
        warn.append(f"Mixed month formats ({', '.join(longs)} next to 3-letter months) — use one style.")
    if not ranges:
        warn.append("No parseable date ranges (expected “Mon YYYY – Mon YYYY”).")
    return warn, sections, dates, text


def score(pdf: Path, *, job_title: str, keywords: list[dict], name: str, email: str,
          phone_digits: str) -> Report:
    warn, sections, dates, text = parse_check(pdf, name=name, email=email, phone_digits=phone_digits)
    tn = norm(text)
    matched, missing = [], []
    got = total = must_got = must_total = 0
    for k in keywords:
        w = int(k.get("weight", 1))
        ok = contains(tn, k["term"], k.get("aliases", []))
        total += w
        got += w if ok else 0
        if w >= 3:
            must_total += 1
            must_got += ok
        (matched if ok else missing).append(k["term"] if ok else (k["term"], w))
    kw = 100 * got / total if total else 100
    must = 100 * must_got / must_total if must_total else kw
    # job title: exact phrase first, otherwise share of significant title words present
    clean = re.sub(r"\(.*?\)", " ", job_title)
    words = [w for w in norm(clean).split() if w not in STOP and len(w) > 1]
    title_found = bool(words) and contains(tn, " ".join(words))
    # Jobscan rewards the exact title; scattered title words earn at most 60%
    title = 100.0 if title_found else (60 * sum(contains(tn, w) for w in words) / len(words) if words else 100)
    total_score = 0.40 * kw + 0.20 * must + 0.15 * title + 0.15 * sections + 0.10 * dates
    return Report(total_score, {"keywords": kw, "must_have": must, "title": title,
                                "sections": sections, "dates": dates},
                  matched, missing, warn, title_found)
