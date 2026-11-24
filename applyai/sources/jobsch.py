"""jobs.ch public search API (Swiss cities)."""
from __future__ import annotations

from ..log import log
from ..models import Job
from ..util import get, html_to_text

SEARCH = "https://www.jobs.ch/api/v1/public/search"
DETAIL = "https://www.jobs.ch/api/v1/public/search/job/{id}"


def search(queries, *, places=("Zürich",), days=14, pages=2):
    jobs = []
    for place, q in ((p, q) for p in places for q in queries):
        docs = []
        for page in range(1, pages + 1):  # API max page size is 20 (larger -> HTTP 422)
            r = get(SEARCH, params={"query": q, "location": place, "rows": 20, "page": page})
            if r is None:
                break
            data = r.json()
            docs += data.get("documents", [])
            if page >= data.get("num_pages", 1):
                break
        for d in docs:
            if d.get("age", 0) > days:
                continue
            jobs.append(Job(
                title=d.get("title", ""), company=d.get("company_name", ""),
                location=d.get("place", "") + ", Switzerland",
                url=d["_links"]["detail_en"]["href"], source="jobs.ch",
                posted=(d.get("publication_date") or "")[:10], extra={"jobsch_id": d["job_id"]}))
    log.info("jobs.ch: %d postings", len(jobs))
    return jobs


def fetch_detail(job: Job) -> None:
    r = get(DETAIL.format(id=job.extra["jobsch_id"]))
    if r is None:
        return
    d = r.json()
    html = d.get("template") or d.get("template_text") or ""
    job.description = html_to_text(html) if html else d.get("preview", "")
    if d.get("application_url"):
        job.extra["apply_url"] = d["application_url"]
