from __future__ import annotations

import datetime as dt
from pathlib import Path

from . import ratings
from .models import Job, canonical_company
from .util import safe_name


def job_folder(root: Path, job: Job) -> Path:
    place = root / safe_name(job.country or "Other", 40)
    if job.city and job.city != job.country:
        place = place / safe_name(job.city, 60)
    return place / safe_name(canonical_company(job.company), 60) / safe_name(job.title, 90)


def short_net(net: list[str]) -> str:
    """'YC Winter 2024' -> 'YC W24'."""
    import re
    return ", ".join(re.sub(r"YC (Winter|Summer|Spring|Fall) \d{2}(\d{2})", lambda m: f"YC {m.group(1)[0]}{m.group(2)}", n)
                     for n in net or [])


def network_line(net: list[str]) -> str:
    kind = "a Y Combinator company" if any(n.startswith("YC") for n in net) else "founded by alumni"
    return f"- **Founder network:** {short_net(net)} ({kind})"


def write_job_md(folder: Path, job: Job, result: dict | None, rating: dict | None = None):
    apply = job.extra.get("apply_url")
    lines = [
        f"# {job.title}",
        f"**{job.company}** — {job.location}",
        "",
        f"- **Posting:** {job.url}",
    ]
    if apply and apply != job.url:
        lines.append(f"- **Apply:** {apply}")
    lines += [
        f"- **Source:** {job.source}" + (f" · posted {job.posted}" if job.posted else ""),
        f"- **Found:** {dt.date.today().isoformat()}",
        f"- **Fit score:** {job.fit_score:.0f}/10 — {job.fit_reason}" if job.fit_score is not None else "- **Fit score:** –",
        ratings.md_line(rating),
    ]
    if job.extra.get("network"):
        lines.append(network_line(job.extra["network"]))
    if result:
        lines += [
            f"- **ATS score of tailored CV:** {result['ats']}/100 (details in `ATS_Report.md`)",
            "",
            "## Files",
            f"- `{result['cv_pdf']}` — tailored CV (1 page, LaTeX source next to it)",
            f"- `{result['letter_pdf']}` — tailored cover letter (1 page)",
            "- `ATS_Report.md` — keyword match, missing keywords, parsing check",
        ]
        if result.get("notes"):
            lines += ["", "## What was tailored", result["notes"]]
    lines += [
        "",
        "## Before you send",
        "- [ ] Read CV + letter once; ApplyAI only uses your fact bank, but check the tone",
        "- [ ] Add a contact person to the letter if you know one",
        "- [ ] Send it yourself via the link above, or with `/applyai:apply` (you approve every submission)",
        "",
        "## Job description",
        "",
        job.description.strip() or "_(no description could be fetched — open the link)_",
    ]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "Job.md").write_text("\n".join(lines) + "\n")


def write_overview(root: Path, store_data: dict):
    rows = [r for r in store_data.values() if r.get("status") == "tailored"]
    rows.sort(key=lambda r: (r.get("first_seen", ""), r.get("fit", 0)), reverse=True)
    out = ["# ApplyAI — overview", "",
           f"Updated {dt.datetime.now():%Y-%m-%d %H:%M}. {len(rows)} tailored applications.", "",
           "Rating: k = kununu, GD = Glassdoor (out of 5, from search results; details and link in each Job.md). * = fewer than 10 reviews, not reliable.", "",
           "| Found | Fit | ATS | Rating | Network | City | Company | Position | Folder |", "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        rel = Path(r["folder"]).relative_to(root) if r.get("folder") else ""
        link = str(rel).replace(" ", "%20")
        out.append(f"| {r.get('first_seen', '')} | {r.get('fit', '')} | {r.get('ats', '')} | {ratings.short(r.get('rating'))} | {short_net(r.get('network'))} | {r.get('city', '')} "
                   f"| {r.get('company', '')} | [{r.get('title', '')}]({r.get('url', '')}) | [open]({link}/Job.md) |")
    (root / "_Overview.md").write_text("\n".join(out) + "\n")
