"""Company career boards with public JSON/XML APIs: Greenhouse, Lever, Ashby, Personio.

Each target company is listed in config.yaml under `company_boards`. Descriptions come with
the listing, so no detail fetch is needed.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

from ..log import log
from ..models import Job
from ..util import get, html_to_text


def _greenhouse(token, name):
    r = get(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", params={"content": "true"})
    if r is None:
        return []
    import html as _html
    return [Job(title=j["title"], company=name, location=(j.get("location") or {}).get("name", ""),
                url=j["absolute_url"], source="greenhouse", posted=(j.get("updated_at") or "")[:10],
                description=html_to_text(_html.unescape(j.get("content", ""))))
            for j in r.json().get("jobs", [])]


def _lever(token, name):
    r = get(f"https://api.lever.co/v0/postings/{token}", params={"mode": "json"})
    if r is None:
        return []
    out = []
    for j in r.json():
        cat = j.get("categories") or {}
        locs = cat.get("allLocations") or [cat.get("location", "")]
        desc = j.get("descriptionPlain", "") + "\n\n" + "\n\n".join(
            f"{l.get('text', '')}\n" + html_to_text(l.get("content", "")) for l in j.get("lists", []))
        desc += "\n\n" + (j.get("additionalPlain") or "")
        out.append(Job(title=j["text"], company=name, location=" / ".join(filter(None, locs)),
                       url=j["hostedUrl"], source="lever", description=desc.strip()))
    return out


def _ashby(token, name):
    r = get(f"https://api.ashbyhq.com/posting-api/job-board/{token}")
    if r is None:
        return []
    out = []
    for j in r.json().get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", [])]
        out.append(Job(title=j["title"], company=name, location=" / ".join(filter(None, locs)),
                       url=j.get("jobUrl", ""), source="ashby", posted=(j.get("publishedAt") or "")[:10],
                       description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", ""))))
    return out


def _personio(token, name):
    r = get(f"https://{token}.jobs.personio.de/xml", params={"language": "en"})
    if r is None or "<position>" not in r.text:
        return []
    root = ET.fromstring(r.content)
    out = []
    for p in root.findall("position"):
        desc = "\n\n".join(f"{(d.findtext('name') or '').strip()}\n" + html_to_text(d.findtext("value") or "")
                           for d in p.findall("jobDescriptions/jobDescription"))
        out.append(Job(title=p.findtext("name", ""), company=name, location=p.findtext("office", ""),
                       url=f"https://{token}.jobs.personio.de/job/{p.findtext('id')}", source="personio",
                       posted=(p.findtext("createdAt") or "")[:10], description=desc))
    return out


def _recruitee(token, name):
    r = get(f"https://{token}.recruitee.com/api/offers/")
    if r is None:
        return []
    return [Job(title=o.get("title", ""), company=name,
                location=", ".join(filter(None, (o.get("city"), o.get("country")))) or o.get("location", ""),
                url=o.get("careers_url", ""), source="recruitee", posted=(o.get("published_at") or "")[:10],
                description=html_to_text((o.get("description") or "") + (o.get("requirements") or "")))
            for o in r.json().get("offers", [])]


def _workable(token, name):
    r = get(f"https://apply.workable.com/api/v1/widget/accounts/{token}", params={"details": "true"})
    if r is None:
        return []
    return [Job(title=j.get("title", ""), company=name,
                location=", ".join(filter(None, (j.get("city"), j.get("country")))),
                url=j.get("url") or j.get("shortlink", ""), source="workable",
                posted=(j.get("published_on") or "")[:10], description=html_to_text(j.get("description") or ""))
            for j in r.json().get("jobs", [])]


KINDS = {"greenhouse": _greenhouse, "lever": _lever, "ashby": _ashby, "personio": _personio,
         "recruitee": _recruitee, "workable": _workable}


def search(boards):
    jobs = []
    for b in boards:
        try:
            got = KINDS[b["ats"]](b["token"], b["name"])
        except Exception as e:  # one broken board must not stop the run
            log.warning("board %s (%s) failed: %s", b["name"], b["ats"], e)
            continue
        jobs += got
        log.debug("board %-22s %3d postings", b["name"], len(got))
    log.info("company boards: %d postings from %d companies", len(jobs), len(boards))
    return jobs
