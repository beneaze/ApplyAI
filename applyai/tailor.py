"""Tailor CV + cover letter for one job, keep both at exactly one page, and push the ATS score.

Loop per job:
  1. Claude (writer model) rewrites the CV body and drafts the letter from the fact bank only.
  2. Compile. If the CV spills onto page 2, Claude cuts exactly that many lines (up to 3 rounds),
     then tighter spacing. If space is left, relevant content is put back and the spacing between
     entries is evened out so the page is full, like the hand-made original.
  3. Score the PDF with the local ATS checker. Below target -> one revision that works in the
     missing keywords that are TRUE per the fact bank, then re-fit to one page. Best version wins.
  4. Optional fact check: a second model lists any claim not backed by the fact bank and the
     writer fixes it.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import ats, latex
from .llm import LLMError, ask, ask_json, between, extract_json
from .log import log
from .models import Job
from .util import file_slug

TEMPLATES = Path(__file__).resolve().parent / "templates"
LANG_NAMES = {"en": "English", "de": "German", "fr": "French", "es": "Spanish", "it": "Italian",
              "nl": "Dutch", "pt": "Portuguese", "sv": "Swedish", "da": "Danish", "pl": "Polish"}

RULES = """HARD RULES
- Truth: use ONLY facts in the FACT BANK. You may rephrase, reorder, merge, shorten, choose
  which bullets/entries to show, and mirror the posting's wording for things the candidate
  really did. Never invent skills, tools, numbers, titles, employers, dates or results. If a
  keyword from the posting is not supported by the fact bank, leave it out.
- Mirror the posting's exact terms where truthful (e.g. write "photonic integrated circuits
  (PIC)" if the posting says that) — ATS systems match literal keywords.
- The CV is entirely in <<CV_LANG>>, even for postings in another language: use the
  <<CV_LANG>> equivalent of a foreign keyword; never mix languages in the CV.
- Keep the given LaTeX format (resume.cls environments rSection and
  rSubsection{Organization}{Mon YYYY -- Mon YYYY}{Title}{Location}, bullets with \\item; an
  entry without bullets is written \\rEntry{Organization}{Dates}{Title}{Location}). Use
  3-letter months only. Keep the section headings of the BASE CV.
- The CV MUST fit on ONE page and should fill it: the BASE CV fills exactly one page, so keep
  the same overall length — when you add or lengthen something, cut or shorten something less
  relevant. Write locations as "City, Country". Bullets are narrow (the location sits in the
  right margin next to the first bullet), so keep each bullet to one or two lines. Keep the
  entry structure close to the base.
- Avoid bullets whose last line would hold only one or two words; trim or rephrase instead.
- No first-person pronouns in the CV. Strong verbs, quantified results from the fact bank.
- The Skills section is a tabular with rows "Category & comma-separated items \\\\" (max ~90
  characters per row so it stays on one line) — reorder and reword categories so the posting's
  hard skills come first.

THE CANDIDATE'S OWN CV RULES (always follow these; they win over the rules above)
<<CV_RULES>>
"""

WRITE_PROMPT = """You tailor the CV and cover letter of {name} for one job.

{rules}

JOB
Title: {title}
Company: {company}
Location: {location}
Posting URL: {url}
ATS keywords (weight 3 = must-have): {keywords}
Description:
{description}

FACT BANK
{facts}

CURRENT BASE CV (LaTeX body between \\begin{{document}} and \\end{{document}})
{cv_body}

STYLE EXAMPLES — the candidate's previous cover letters (match this voice: direct, concrete,
first person, specific detail, a genuine reason for THIS company, no clichés like "I am writing
to express my interest", no bullet lists, ~330-400 words, 5-7 paragraphs):
{letter_examples}

LETTER DETAILS
- Language: {letter_lang}
- The candidate lives in {home} and is available {availability}. {relocation}
{letter_notes}
- Recipient: company name plus street address if the posting or your knowledge gives one you
  are confident about; otherwise company name and city only. A named contact only if the
  posting names one.
- The letter must fit on one A4 page with the given header (about 400 words max).

OUTPUT exactly these three blocks and nothing else:

<<<CV_BODY>>>
(the complete LaTeX between \\begin{{document}} and \\end{{document}}, not including those lines)
<<<END CV_BODY>>>

<<<HEADLINE>>>
the posting's job title, cleaned of gender tags / percentages / locations, max ~55 characters
(e.g. "Quantum Hardware Engineer – Optical Systems"). Plain text, nothing else.
<<<END HEADLINE>>>

<<<LETTER>>>
{{"recipient_lines": ["Company GmbH", "Street 1", "12345 City"],
  "subject": "Job Application for <exact job title>",
  "salutation": "Dear ... ,",
  "paragraphs": ["plain text paragraph", "..."],
  "closing": "Sincerely,"}}
<<<END LETTER>>>

<<<NOTES>>>
3-6 short bullet points: what you emphasized and which posting keywords you deliberately left
out because they are not supported by the fact bank.
<<<END NOTES>>>
"""

SHRINK_PROMPT = """This one-page LaTeX CV body overflows onto a second page by {lines} text
line(s). Make it fit on one page by removing or condensing the LEAST relevant content for the
job "{title}" at {company}: shorten long bullets so they fit in one line, merge bullets, or
drop a low-relevance bullet/entry. Do not remove must-have keywords: {must}. Do not add
anything new. Remove a bit more than strictly needed ({target} lines) to be safe.

{rules}

CV BODY:
{cv_body}

Return ONLY the full revised body inside <<<CV_BODY>>> ... <<<END CV_BODY>>>."""

KEYWORD_PROMPT = """The tailored CV below scored {score:.0f}/100 in an ATS keyword check for
"{title}" at {company}. These posting keywords are missing from the CV text:
{missing}

Revise the CV body so that every missing keyword that is TRUE according to the FACT BANK
appears verbatim (in a bullet or in Skills); for a German keyword use its English
equivalent instead — the CV stays English. Skip any keyword that is not supported — do not
stretch the truth. Keep it on one page: compensate any added words by tightening other text.

{rules}

FACT BANK
{facts}

CV BODY:
{cv_body}

Return the revised body inside <<<CV_BODY>>> ... <<<END CV_BODY>>>, then
<<<SKIPPED>>> comma-separated keywords you skipped because they are untrue <<<END SKIPPED>>>."""

FACTCHECK_PROMPT = """You are a strict fact checker. Compare the CV and cover letter below
against the FACT BANK. List every claim (skill, tool, number, title, employer, date, result,
responsibility) that is NOT supported by the fact bank. Rewording and reasonable
generalization of a stated fact is fine; new facts are not.

FACT BANK
{facts}

CV (LaTeX):
{cv_body}

LETTER:
{letter}

Return ONLY JSON: {{"unsupported": [{{"where": "cv|letter", "claim": "...", "fix": "how to rephrase or remove"}}]}}"""

FIX_PROMPT = """Fix these unsupported claims in the CV body and letter. Change nothing else.

{issues}

{rules}

CV BODY:
{cv_body}

LETTER (JSON):
{letter_json}

Return <<<CV_BODY>>> ... <<<END CV_BODY>>> and <<<LETTER>>> ... <<<END LETTER>>> (same JSON format)."""

EXPAND_PROMPT = """This one-page LaTeX CV body for "{title}" at {company} now leaves about
{lines} empty text lines at the bottom of the page. Use roughly {use} of them: bring back the
most job-relevant content that was cut (compare with the BASE CV and the FACT BANK) or add a
relevant true detail. Do not touch anything else.

{rules}

FACT BANK
{facts}

BASE CV BODY:
{base}

CURRENT CV BODY:
{cv_body}

Return ONLY the full revised body inside <<<CV_BODY>>> ... <<<END CV_BODY>>>."""

RUNT_PROMPT = """In this one-page LaTeX CV body, these bullets end with only one or two words
on their last line, which looks accidental and wastes a line:
{runts}

Reword ONLY these bullets so the last line is either gone (preferred: trim a few words) or
well filled. Keep the facts, the keywords ({must}) and every other line unchanged.

{rules}

CV BODY:
{cv_body}

Return ONLY the full revised body inside <<<CV_BODY>>> ... <<<END CV_BODY>>>."""

LETTER_SHRINK = """This cover letter overflows one A4 page by {lines} line(s). Shorten the
paragraphs (keep the voice and the strongest, job-specific points; same facts only). Return
ONLY the JSON with the same keys inside <<<LETTER>>> ... <<<END LETTER>>>.

{letter_json}"""


@dataclass
class Profile:
    """The user's profile folder: facts.md, cv_base.tex, letter_examples.md, cv_rules.md, ..."""
    root: Path
    cfg: dict

    def _read(self, name: str, default: str = "") -> str:
        f = self.root / name
        return f.read_text() if f.exists() else default

    @property
    def cv_base(self) -> str:
        return (self.root / "cv_base.tex").read_text()

    @property
    def facts(self) -> str:
        return (self.root / "facts.md").read_text()

    @property
    def letter_examples(self) -> str:
        return self._read("letter_examples.md", "(no examples given: write in a clear, direct, professional voice)")

    @property
    def cv_rules(self) -> str:
        return self._read("cv_rules.md", "(none)")

    @property
    def resume_cls(self) -> Path:
        own = self.root / "resume.cls"
        return own if own.exists() else TEMPLATES / "resume.cls"

    @property
    def signature(self) -> Path | None:
        f = self.root / "signature.png"
        return f if f.exists() else None

    def split_cv(self, tex: str | None = None):
        tex = tex or self.cv_base
        pre, rest = tex.split("\\begin{document}", 1)
        body = rest.rsplit("\\end{document}", 1)[0]
        return pre, body.strip("\n")


def _kw_str(job: Job) -> str:
    return "; ".join(f"{k['term']} ({k.get('weight', 1)})" for k in job.keywords) or "(none extracted)"


def _must(job: Job) -> str:
    return ", ".join(k["term"] for k in job.keywords if int(k.get("weight", 1)) >= 3) or "-"


_BLOCK = re.compile(r"\\begin\{rSubsection\}.*?\\end\{rSubsection\}|\\rEntry(?:\{(?:[^{}]|\{[^{}]*\})*\}){4}", re.S)


def _section_end(body: str, title: str) -> int:
    i = body.find("\\begin{rSection}{%s}" % title)
    return body.find("\\end{rSection}", i) if i != -1 else -1


def _restore_kept(body: str, base: str, keep: list) -> str:
    """Entries the candidate wants on every CV (config `tailoring.must_keep`). Each item is a
    text that identifies the entry in the base CV ("EPFL"), or {match, latex, section} for an
    entry that is not in the base CV. A dropped entry is put back where it was; an entry whose
    bullets were all cut gets them back."""
    base_blocks = list(_BLOCK.finditer(base))
    for item in keep or []:
        key = item if isinstance(item, str) else item.get("match", "")
        if not key:
            continue
        src = next((m for m in base_blocks if key in m.group(0)), None)
        block = src.group(0) if src else (item.get("latex", "") if isinstance(item, dict) else "")
        if not block:
            continue
        cur = next((m for m in _BLOCK.finditer(body) if key in m.group(0)), None)
        if cur is not None:
            if cur.group(0).startswith("\\rEntry") and block.startswith("\\begin{rSubsection}"):
                body = body[:cur.start()] + block + body[cur.end():]
            continue
        at = -1
        if src:  # before the entry that follows it in the base CV
            for m in base_blocks[base_blocks.index(src) + 1:]:
                at = body.find(m.group(0).split("\n", 1)[0][:70])
                if at != -1:
                    break
        if at == -1 and isinstance(item, dict) and item.get("section"):
            sec = body.find("\\begin{rSection}{%s}" % item["section"])
            first = body.find("\\begin{rSubsection}", sec) if sec != -1 else -1
            at = first if item.get("position") == "first" and first != -1 else _section_end(body, item["section"])
        if at != -1:
            body = body[:at] + block + "\n\n" + body[at:]
    return body


def _ensure_skills(body: str, ensure: dict) -> str:
    """config `tailoring.ensure_skills`: {row: item} — items that are always listed."""
    for row, item in (ensure or {}).items():
        if item.lower() in body.lower():
            continue
        m = re.search(rf"^(\s*{re.escape(row)}[^&\n]*&[^\n]*?)\s*\\\\\s*$", body, re.M)
        if m:
            body = body[:m.end(1)] + ", " + item + body[m.end(1):]
        else:
            k = max(body.rfind("\\end{tabularx}"), body.rfind("\\end{tabular}"))
            if k != -1:
                body = body[:k] + f"{row} & {item} \\\\\n" + body[k:]
    return body


def _validate_body(body: str, base: str = "", cfg: dict | None = None) -> str:
    if not body or "\\begin{rSection}" not in body:
        raise LLMError("model returned no usable CV body")
    body = body.replace("\\begin{document}", "").replace("\\end{document}", "")
    body = re.sub(r"\\documentclass.*?\n", "", body)
    if body.count("\\begin{rSection}") != body.count("\\end{rSection}"):
        raise LLMError("unbalanced rSection environments")
    body = re.sub(r"\\begin\{tabular\}\s*\{[^\n]*\}", r"\\begin{tabularx}{\\textwidth}{@{} >{\\bfseries}l @{\\hspace{4ex}} X @{}}", body)
    body = body.replace("\\end{tabular}", "\\end{tabularx}")
    # an entry whose bullets were all dropped would be an empty list (LaTeX error)
    body = re.sub(r"\\begin\{rSubsection\}((?:\{(?:[^{}]|\{[^{}]*\})*\}){4})\s*(?:%[^\n]*\s*)*\\end\{rSubsection\}",
                  lambda m: "\\rEntry" + m.group(1), body)
    t = (cfg or {}).get("tailoring", {})
    body = _restore_kept(body, base, t.get("must_keep", []))
    body = _ensure_skills(body, t.get("ensure_skills", {}))
    return body.strip("\n")


BABEL = {"de": "ngerman", "fr": "french", "es": "spanish", "it": "italian", "nl": "dutch",
         "pt": "portuguese", "sv": "swedish", "da": "danish", "pl": "polish"}


def _letter_lang_text(lang: str) -> str:
    if lang == "de":
        return "German (formal Sie; subject 'Bewerbung als ...', closing 'Mit freundlichen Grüßen')"
    if lang == "en":
        return "English"
    name = LANG_NAMES.get(lang, lang)
    return f"{name} (formal register, with the subject line, salutation and closing usual in {name})"


class Tailor:
    def __init__(self, profile: Profile, cfg: dict):
        self.p = profile
        self.cfg = cfg
        self.me = cfg["me"]
        t = cfg["tailoring"]
        self.writer, self.editor, self.checker = t["writer_model"], t["editor_model"], t["factcheck_model"]
        self.ats_target = t.get("ats_target", 80)
        self.headline = ""
        self.cv_lang = t.get("cv_language", "English")
        self.rules = RULES.replace("<<CV_LANG>>", self.cv_lang).replace("<<CV_RULES>>", profile.cv_rules.strip())
        self.base = profile.split_cv()[1]

    # ---------- assembly --------------------------------------------------------------
    def _cv_tex(self, body: str, job: Job, layout: str = "original", squeeze: int = 0) -> str:
        """squeeze > 0 tightens margins/leading (overflow fallback); squeeze < 0 adds space
        between entries and sections so a slightly short CV still fills the page."""
        pre, _ = self.p.split_cv()
        pre = re.sub(r"% BASE VERSION[^\n]*", lambda m: f"% Tailored by ApplyAI: {job.company} - {job.title}", pre, count=1)
        if squeeze > 0:
            top = {1: "0.25in", 2: "0.2in"}[squeeze]
            pre = re.sub(r"top=[\d.]+in", f"top={top}", pre)
            pre = re.sub(r"bottom=[\d.]+in", f"bottom={top}", pre)
            pre += "\\linespread{%s}\n" % {1: "0.79", 2: "0.77"}[squeeze]
        elif squeeze < 0:
            k = -squeeze
            pre += ("\\def\\entryskip{%.2fem}\n\\def\\sectionskip{\\vspace{%.2fem}}\n"
                    % (0.1 + 0.15 * k, 0.4 + 0.2 * k))
        if self.headline and self.cfg["tailoring"].get("cv_headline", True):
            pre += "\\headline{%s}\n" % latex.escape(self.headline)
        meta = ("\\hypersetup{pdftitle={%s - CV}, pdfauthor={%s}, pdfsubject={%s}}\n"
                % (self.me["name"], self.me["name"], latex.escape(f"{job.title} at {job.company}")))
        lay = "\\photo{photo.jpg}\n" if self.wants_photo(job) else ""
        if self.me.get("born") and job.country in self.me.get("born_countries", []):
            lay += "\\personal{Born %s}\n" % latex.escape(self.me["born"])
        return pre + meta + lay + "\\begin{document}\n" + body + "\n\\end{document}\n"

    def _letter_tex(self, L: dict, job: Job, tighten: int = 0) -> str:
        e = latex.escape
        today = dt.date.today()
        place = self.me.get("letter_city") or self.me.get("city", "")
        if L.get("lang") == "de":
            months = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August",
                      "September", "Oktober", "November", "Dezember"]
            date = f"{self.me.get('city_de') or place}, {today.day}. {months[today.month - 1]} {today.year}"
        else:
            date = f"{place}, {today.strftime('%B')} {today.day} {today.year}"
        date = date.lstrip(", ")
        rec = "\\\\\n".join(e(l) for l in L.get("recipient_lines", [job.company]))
        paras = "\n\n".join(e(p) for p in L["paragraphs"])
        gap = ["1cm", "0.7cm", "0.5cm"][min(tighten, 2)]
        me = self.me
        sig = ("\\includegraphics[height=1.3cm]{signature.png}\\\\[-0.1cm]" if self.p.signature
               else "\\vspace{0.6cm}")
        head = "\\\\\n".join([e(me["name"])] + [e(me[k]) for k in ("street", "zip_city") if me.get(k)]
                               + ([e(me.get("phone_letter") or me["phone_cv"])] if me.get("phone_letter") or me.get("phone_cv") else [])
                               + [f"\\href{{mailto:{me['email']}}}{{{e(me['email'])}}}"])
        return rf"""\documentclass[11pt,a4paper]{{article}}
\usepackage[utf8]{{inputenc}}
\usepackage{{cmap}}
\usepackage[T1]{{fontenc}}
\usepackage{{lmodern}}
\input{{glyphtounicode}}\pdfgentounicode=1
\usepackage[{BABEL.get(L.get('lang'), 'english')}]{{babel}}
\usepackage{{geometry}}
\usepackage{{parskip}}
\usepackage{{graphicx}}
\usepackage[hidelinks]{{hyperref}}
\hypersetup{{pdftitle={{{e(me['name'])} - Cover Letter}}, pdfauthor={{{e(me['name'])}}}}}
\geometry{{left=2.5cm, right=2.0cm, top={'1.4cm' if tighten < 2 else '1.1cm'}, bottom=1.0cm}}
\pagestyle{{empty}}
\begin{{document}}
\begin{{flushright}}
{head}
\end{{flushright}}

\vspace{{{gap}}}

{rec}

\begin{{flushright}}
{e(date)}
\end{{flushright}}

\vspace{{{gap}}}

\textbf{{{e(L['subject'])}}}

\vspace{{0.5em}}

{e(L['salutation'])}

{paras}

\vspace{{1em}}

{e(L.get('closing', 'Sincerely,'))}

\vspace{{0.1cm}}
{sig}
{e(me['name'])}

\end{{document}}
"""

    def letter_language(self, job: Job) -> str:
        """auto = the posting's language if it is one you write letters in, else English."""
        t = self.cfg["tailoring"]
        want = t.get("letter_language", "auto")
        if want != "auto":
            return want
        ok = t.get("letter_languages", ["en"])
        return job.language if job.language in ok else "en"

    # ---------- steps -----------------------------------------------------------------
    def wants_photo(self, job: Job) -> bool:
        """DACH + traditional employer (listed company or German-language posting)."""
        t = self.cfg["tailoring"]
        if not t.get("photo") or job.country not in t.get("photo_countries", []):
            return False
        name = job.company.lower()
        listed = any(c.lower() in name for c in t.get("photo_companies", []))
        return listed or (t.get("photo_for_german_postings", True) and job.language == "de")

    def _fit_cv(self, body: str, job: Job, folder: Path, stem: str):
        """Compile; shrink until one page; then fill the page. Returns (body, pdf, layout, spacing)."""
        layout = "original"
        tex = folder / f"{stem}.tex"
        if self.wants_photo(job):
            shutil.copy(self.p.root.parent / self.cfg["tailoring"]["photo"], folder / "photo.jpg")
        else:
            (folder / "photo.jpg").unlink(missing_ok=True)
        for attempt in range(4):
            tex.write_text(self._cv_tex(body, job))
            pdf = latex.compile_pdf(tex)
            over = latex.overflow_lines(pdf)
            if over == 0:
                body, pdf = self._fix_runts(body, job, tex, pdf)
                body2, pdf = self._refill(body, job, tex, pdf)
                if body2 != body:  # refilled text can bring new one-word lines
                    body2, pdf = self._fix_runts(body2, job, tex, pdf)
                body = body2
                pdf, spacing = self._stretch(body, job, tex, pdf)
                return body, pdf, layout, spacing
            if attempt == 3:
                break
            log.info("  CV overflows by %d lines -> shrinking (round %d)", over, attempt + 1)
            reply = ask(SHRINK_PROMPT.format(lines=over, target=over + 1, title=job.title,
                                             company=job.company, must=_must(job), rules=self.rules,
                                             cv_body=body), model=self.editor)
            body = _validate_body(between(reply, "CV_BODY"), self.base, self.cfg)
        for sq in (1, 2):  # last resort: slightly tighter margins / leading
            tex.write_text(self._cv_tex(body, job, layout, sq))
            pdf = latex.compile_pdf(tex)
            if latex.pages(pdf) == 1:
                log.info("  CV fits with tightened spacing (level %d)", sq)
                return body, pdf, layout, sq
        raise latex.LatexError("CV could not be brought to one page")

    def _fix_runts(self, body, job, tex, pdf):
        """Reword bullets whose last line is a lone word; keep only if it stays 1 page and helps."""
        found = latex.runts(pdf)
        if not found:
            return body, pdf
        log.info("  %d bullet(s) end in a one-word line -> rewording", len(found))
        try:
            reply = ask(RUNT_PROMPT.format(runts="\n".join(f"- {r}" for r in found), must=_must(job),
                                           rules=self.rules, cv_body=body), model=self.editor)
            new = _validate_body(between(reply, "CV_BODY"), self.base, self.cfg)
            tex.write_text(self._cv_tex(new, job))
            new_pdf = latex.compile_pdf(tex)
            left = latex.runts(new_pdf) if latex.pages(new_pdf) == 1 else None
            if left is not None and len(left) < len(found):
                if left:
                    log.info("  %d one-word line(s) left", len(left))
                return new, new_pdf
        except (LLMError, latex.LatexError) as e:
            log.warning("  runt fix failed: %s", e)
        tex.write_text(self._cv_tex(body, job))  # revert
        return body, latex.compile_pdf(tex)

    def _stretch(self, body, job, tex, pdf):
        """Even out the space between entries/sections until the page is full (max ~1 line each)."""
        best, level = pdf, 0
        for k in range(1, 7):
            if latex.page_fill(best) >= 0.96:  # your hand-made CV ends at 0.969
                break
            tex.write_text(self._cv_tex(body, job, "original", -k))
            p = latex.compile_pdf(tex)
            if latex.pages(p) > 1:
                break
            best, level = p, -k
        tex.write_text(self._cv_tex(body, job, "original", level))
        return latex.compile_pdf(tex), level

    def _refill(self, body, job, tex, pdf):
        """If shrinking overshot and left a gap at the bottom, put relevant content back."""
        fill = latex.page_fill(pdf)
        free = int((0.965 - fill) * 62)  # ~62 text lines on a full page
        if free < 3:
            return body, pdf
        log.info("  CV uses %.0f%% of the page -> refilling ~%d lines", fill * 100, free)
        try:
            reply = ask(EXPAND_PROMPT.format(title=job.title, company=job.company, lines=free,
                                             use=max(2, free - 2), rules=self.rules, facts=self.p.facts,
                                             base=self.p.split_cv()[1], cv_body=body), model=self.editor)
            new = _validate_body(between(reply, "CV_BODY"), self.base, self.cfg)
            tex.write_text(self._cv_tex(new, job))
            new_pdf = latex.compile_pdf(tex)
            if latex.pages(new_pdf) == 1:
                return new, new_pdf
        except (LLMError, latex.LatexError) as e:
            log.warning("  refill failed: %s", e)
        tex.write_text(self._cv_tex(body, job))  # revert
        return body, latex.compile_pdf(tex)

    def _ats(self, pdf: Path, job: Job):
        return ats.score(pdf, job_title=job.title, keywords=job.keywords, name=self.me["name"],
                         email=self.me["email"], phone_digits=re.sub(r"\D", "", self.me["phone_cv"]))

    def _fit_letter(self, L: dict, job: Job, folder: Path, stem: str) -> Path:
        tex = folder / f"{stem}.tex"
        for attempt in range(5):
            tex.write_text(self._letter_tex(L, job, tighten=min(attempt, 2)))
            pdf = latex.compile_pdf(tex)
            over = latex.overflow_lines(pdf)
            if over == 0:
                return pdf
            if attempt >= 2:  # spacing alone was not enough: shorten the text
                log.info("  letter overflows by %d lines -> shortening", over)
                reply = ask(LETTER_SHRINK.format(lines=over, letter_json=json.dumps(L, ensure_ascii=False)),
                            model=self.editor)
                L.update(json.loads(between(reply, "LETTER") or "{}") or {})
        raise latex.LatexError("letter could not be brought to one page")

    # ---------- main ------------------------------------------------------------------
    def run(self, job: Job, folder: Path) -> dict:
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.p.resume_cls, folder / "resume.cls")
        if self.p.signature:
            shutil.copy(self.p.signature, folder / "signature.png")
        _, base_body = self.p.split_cv()
        lang = self.letter_language(job)
        home = self.me.get("city", "")
        cand = self.cfg.get("candidate", {})
        reloc = "" if not home or job.city.lower() == home.lower() or job.city == "Remote" else (
            f"Mention briefly that they are happy to relocate to {job.city}." if cand.get("willing_to_relocate", True)
            else "")
        notes = cand.get("letter_notes", "").strip()
        prompt = WRITE_PROMPT.format(
            name=self.me["name"], rules=self.rules, title=job.title, company=job.company,
            location=job.location, url=job.url, keywords=_kw_str(job),
            description=job.description[:9000], facts=self.p.facts, cv_body=base_body,
            letter_examples=self.p.letter_examples, letter_lang=_letter_lang_text(lang),
            home=home or "(see fact bank)", availability=cand.get("availability", "on short notice"),
            relocation=reloc, letter_notes=("- " + notes.replace("\n", "\n  ")) if notes else "")
        log.info("  writing CV + letter (%s)", self.writer)
        reply = ask(prompt, model=self.writer, timeout=1200)
        body = _validate_body(between(reply, "CV_BODY"), self.base, self.cfg)
        self.headline = re.sub(r"\s+", " ", between(reply, "HEADLINE") or "").strip()[:70]
        L = extract_json(between(reply, "LETTER") or "")
        L["lang"] = lang
        notes = between(reply, "NOTES") or ""

        if self.cfg["tailoring"].get("fact_check", True):
            body, L = self._fact_check(body, L)

        parts = self.me["name"].split()
        who = file_slug(self.me.get("file_name") or (f"{parts[0]} {parts[-1]}" if parts else "CV"))
        co = file_slug(job.company, 30)
        cv_stem, letter_stem = f"CV_{who}_{co}", f"Cover_Letter_{who}_{co}"

        body, pdf, layout, sq = self._fit_cv(body, job, folder, cv_stem)
        rep = self._ats(pdf, job)
        log.info("  ATS %.0f/100 (keywords %.0f%%, must-have %.0f%%)", rep.score,
                 rep.parts["keywords"], rep.parts["must_have"])
        skipped = ""
        if rep.score < self.ats_target and rep.missing:
            reply = ask(KEYWORD_PROMPT.format(score=rep.score, title=job.title, company=job.company,
                                              missing=", ".join(f"{k} ({w})" for k, w in rep.missing),
                                              rules=self.rules, facts=self.p.facts, cv_body=body),
                        model=self.writer)
            try:
                body2 = _validate_body(between(reply, "CV_BODY"), self.base, self.cfg)
                skipped = between(reply, "SKIPPED") or ""
                body2, pdf2, layout2, sq2 = self._fit_cv(body2, job, folder, cv_stem + "_v2")
                rep2 = self._ats(pdf2, job)
                log.info("  ATS after keyword pass: %.0f/100", rep2.score)
                if rep2.score > rep.score:
                    body, layout, sq, rep = body2, layout2, sq2, rep2
            except (LLMError, latex.LatexError) as e:
                log.warning("  keyword pass failed: %s", e)
            for f in folder.glob(cv_stem + "_v2.*"):
                f.unlink()
            (folder / f"{cv_stem}.tex").write_text(self._cv_tex(body, job, layout, sq))
            pdf = latex.compile_pdf(folder / f"{cv_stem}.tex")
            rep = self._ats(pdf, job)

        letter_pdf = self._fit_letter(L, job, folder, letter_stem)
        (folder / "ATS_Report.md").write_text(
            rep.markdown(job.title) + (f"\n## Keywords skipped as not true\n{skipped}\n" if skipped else "")
            + f"\nLayout: your original design{' (tightened spacing)' if sq > 0 else ''}. CV pages: "
              f"{latex.pages(pdf)}, letter pages: {latex.pages(letter_pdf)}.\n")
        return {"cv_pdf": pdf.name, "letter_pdf": letter_pdf.name, "ats": round(rep.score),
                "ats_parts": rep.parts, "missing": rep.missing, "notes": notes, "layout": layout}

    def _fact_check(self, body: str, L: dict):
        try:
            res = ask_json(FACTCHECK_PROMPT.format(facts=self.p.facts, cv_body=body,
                                                   letter="\n\n".join(L.get("paragraphs", []))),
                           model=self.checker)
        except (LLMError, ValueError) as e:
            log.warning("  fact check skipped: %s", e)
            return body, L
        issues = res.get("unsupported", []) if isinstance(res, dict) else []
        if not issues:
            log.info("  fact check: clean")
            return body, L
        log.info("  fact check: fixing %d unsupported claim(s)", len(issues))
        reply = ask(FIX_PROMPT.format(issues=json.dumps(issues, ensure_ascii=False, indent=1), rules=self.rules,
                                      cv_body=body, letter_json=json.dumps(L, ensure_ascii=False)),
                    model=self.writer)
        try:
            body = _validate_body(between(reply, "CV_BODY"), self.base, self.cfg)
            lang = L.get("lang")
            L = extract_json(between(reply, "LETTER") or "") or L
            L["lang"] = lang
        except (LLMError, ValueError) as e:
            log.warning("  fact fix failed, keeping draft: %s", e)
        return body, L
