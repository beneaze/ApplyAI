"""StepStone.de search result pages (German cities)."""
from __future__ import annotations

import json
import re
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..log import log
from ..models import Job
from ..util import get, html_to_text

BASE = "https://www.stepstone.de"


def city_slug(name: str) -> str:
    """'München' -> 'muenchen' (StepStone URL form)."""
    s = name.strip().lower()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        s = s.replace(a, b)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def search(queries, *, cities=("München",), days=14):
    jobs = []
    age = 7 if days <= 7 else 14 if days <= 14 else 30
    for city, q in ((c, q) for c in cities for q in queries):
        slug = quote(re.sub(r"\s+", "-", q.strip().lower()))
        r = get(f"{BASE}/jobs/{slug}/in-{city_slug(city)}", params={"radius": 30, "ag": f"age_{age}"})
        if r is None:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.select('article[data-at="job-item"]'):
            t = a.select_one('a[data-at="job-item-title"]')
            if not t:
                continue
            c = a.select_one('[data-at="job-item-company-name"]')
            l = a.select_one('[data-at="job-item-location"]')
            href = t.get("href", "")
            jobs.append(Job(
                title=t.get_text(" ", strip=True), company=c.get_text(" ", strip=True) if c else "",
                location=l.get_text(" ", strip=True) if l else city,
                url=href if href.startswith("http") else BASE + href, source="stepstone"))
    log.info("stepstone: %d postings", len(jobs))
    return jobs


def fetch_detail(job: Job) -> None:
    r = get(job.url, retries=1)  # StepStone blocks bursts of detail requests; fail fast
    if r is None:
        return
    soup = BeautifulSoup(r.text, "html.parser")
    # StepStone ships the posting as JSON-LD (schema.org JobPosting)
    for s in soup.select('script[type="application/ld+json"]'):
        try:
            d = json.loads(s.string or "")
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and d.get("@type") == "JobPosting":
            job.description = html_to_text(d.get("description", ""))
            job.posted = (d.get("datePosted") or "")[:10]
            return
    body = soup.select_one('[data-at="job-ad-content"], main')
    if body:
        job.description = html_to_text(str(body))[:12000]
