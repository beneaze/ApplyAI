"""Two-stage job matching: a cheap title filter, then Claude rates the fit and extracts the
ATS keywords for every posting that survives."""
from __future__ import annotations

import re

from .llm import LLMError, ask_json
from .log import log
from .models import Job


def title_filter(jobs: list[Job], include: list[str], exclude: list[str]) -> list[Job]:
    inc = re.compile("|".join(include), re.I)
    exc = re.compile("|".join(exclude), re.I) if exclude else None
    return [j for j in jobs if inc.search(j.title) and not (exc and exc.search(j.title))]


# Cheap pre-ranking before the LLM screening. Terms come from config.yaml `relevance_terms`
# ({regex: weight}); without them, the search queries themselves are used.
DEFAULT_NEGATIVE = {r"\b(?:[7-9]|1\d)\+?\s*years": -6, r"\bsenior\b|\bstaff\b": -3}


def _terms(cfg: dict) -> tuple[dict, dict]:
    pos = cfg.get("relevance_terms") or {re.escape(q.lower()): 4 for q in cfg.get("queries", [])}
    neg = cfg.get("relevance_negative", DEFAULT_NEGATIVE) or {}
    return pos, neg


def relevance(job: Job, pos: dict, neg: dict) -> float:
    title, desc = job.title.lower(), (job.description or "").lower()
    s = 0.0
    for rx, w in pos.items():
        if re.search(rx, title):
            s += 3 * w
        n = len(re.findall(rx, desc))
        s += w * min(n, 5) ** 0.5
    for rx, w in neg.items():
        if re.search(rx, title) or re.search(rx, desc):
            s += w
    return s


def rank(jobs: list[Job], cfg: dict) -> list[Job]:
    """Most profile-relevant first; drops postings with no domain signal at all."""
    pos, neg = _terms(cfg)
    floor = cfg.get("relevance_min", 6)
    scored = [(relevance(j, pos, neg), j) for j in jobs]
    return [j for s, j in sorted(scored, key=lambda x: -x[0]) if s >= floor]


SCORE_PROMPT = """You screen job postings for one candidate. Be realistic and strict.

CANDIDATE
{profile}

THE CANDIDATE'S OWN SCREENING RULES
{rules}

FACT BANK (everything true about the candidate)
{facts}

For EACH posting below return an object:
- "id": the posting id given
- "score": 0-10 fit (10 = near-perfect; 7 = strong, worth applying; 5 = stretch; <4 = no).
  Penalize: required experience far above what the fact bank shows, seniority levels the
  candidate does not target, mandatory languages they lack, work-authorization or citizenship
  requirements they don't meet, and everything the screening rules above rule out.
- "reason": one short sentence why
- "language": ISO code of the language the posting is written in ("en", "de", "fr", ...)
- "keywords": the 15-30 ATS keywords a recruiter's system would scan for in THIS posting:
  hard skills, tools, methods, domain terms, degree, and the job title itself. Use the
  posting's exact wording. Each as {{"term": "...", "weight": 3 (must-have) | 2 (important) |
  1 (nice-to-have), "aliases": ["other spellings/abbreviations, e.g. PIC for photonic
  integrated circuit"]}}. For a posting not written in {cv_lang}, give the term as written AND
  its {cv_lang} equivalents in "aliases" (the CV is in {cv_lang}).

Return ONLY a JSON array of these objects.

POSTINGS
{postings}
"""


def score(jobs: list[Job], *, profile: str, facts: str, model: str, rules: str = "",
          cv_lang: str = "English", batch: int = 6) -> None:
    for i in range(0, len(jobs), batch):
        chunk = jobs[i:i + batch]
        postings = "\n\n".join(
            f"=== id {n}\nTitle: {j.title}\nCompany: {j.company}\nLocation: {j.location}\n"
            f"Description:\n{(j.description or '(no description)')[:5000]}"
            for n, j in enumerate(chunk))
        try:
            res = ask_json(SCORE_PROMPT.format(profile=profile, facts=facts, postings=postings,
                                               rules=rules or "(none)", cv_lang=cv_lang),
                           model=model, timeout=900)
        except (LLMError, ValueError) as e:
            log.warning("scoring batch failed: %s", str(e)[:300])
            continue
        by_id = {int(r.get("id", -1)): r for r in res if isinstance(r, dict)}
        for n, j in enumerate(chunk):
            r = by_id.get(n)
            if not r:
                continue
            j.fit_score = float(r.get("score", 0))
            j.fit_reason = r.get("reason", "")
            j.language = r.get("language", "en")
            j.keywords = [k for k in r.get("keywords", []) if isinstance(k, dict) and k.get("term")]
        log.info("scored %d/%d", min(i + batch, len(jobs)), len(jobs))
