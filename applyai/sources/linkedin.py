"""LinkedIn public (guest) job search — no login, the same endpoints the logged-out site uses."""
from __future__ import annotations

import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..log import log
from ..models import Job
from ..util import get, html_to_text

SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
DETAIL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}"

def search(queries, locations, *, days=14, pages=2):
    """`locations`: config.yaml entries; `linkedin` overrides the search string LinkedIn gets."""
    jobs = []
    for l in locations:
        metro = l["name"]
        loc = l.get("linkedin") or f"{l['name']}, {l.get('country', '')}".strip(", ")
        before = len(jobs)
        for q in queries:
            for page in range(pages):
                r = get(SEARCH, params={"keywords": q, "location": loc, "distance": 25,
                                        "f_TPR": f"r{days * 86400}", "start": page * 10})
                if r is None or not r.text.strip():
                    break
                found = _parse_cards(r.text)
                jobs += found
                if len(found) < 10:
                    break
        log.info("linkedin %-24s %d cards", metro, len(jobs) - before)
    return jobs


def _parse_cards(html):
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("div.base-card, li"):
        urn = card.get("data-entity-urn") or ""
        m = re.search(r"jobPosting:(\d+)", urn)
        if not m:
            continue
        jid = m.group(1)
        t = card.select_one(".base-search-card__title")
        c = card.select_one(".base-search-card__subtitle")
        l = card.select_one(".job-search-card__location")
        d = card.select_one("time")
        if not t:
            continue
        out.append(Job(
            title=t.get_text(" ", strip=True), company=c.get_text(" ", strip=True) if c else "",
            location=l.get_text(" ", strip=True) if l else "",
            url=f"https://www.linkedin.com/jobs/view/{jid}/", source="linkedin",
            posted=d.get("datetime", "") if d else "", extra={"linkedin_id": jid}))
    return out


def fetch_detail(job: Job) -> None:
    r = get(DETAIL.format(id=job.extra["linkedin_id"]))
    if r is None:
        return
    soup = BeautifulSoup(r.text, "html.parser")
    desc = soup.select_one(".show-more-less-html__markup")
    if desc:
        job.description = html_to_text(str(desc))
    crit = [e.get_text(" ", strip=True) for e in soup.select(".description__job-criteria-item")]
    if crit:
        job.extra["criteria"] = crit
    apply = soup.select_one("a.apply-button, a[data-tracking-control-name*='apply']")
    if apply and apply.get("href", "").startswith("http"):
        job.extra["apply_url"] = apply["href"]
