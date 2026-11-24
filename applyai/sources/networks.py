"""Startups from founder networks: Y Combinator, CDTM and Manage&More.

- YC: the public yc-oss dataset of every YC company (location, tags, "is hiring"). The bot
  keeps companies in your locations whose tags match `networks.yc_tags` and reads their public job pages on
  ycombinator.com (title, location, visa rule, full description).
- CDTM: all startups founded by CDTM alumni, from cdtm.com/startups.
- Manage&More: the startups listed on manageandmore.de/start-ups (their public selection,
  not all 260+).

For CDTM and Manage&More (Munich founder networks) Claude first picks the companies that could
hire for your profile. The bot then finds each one's job board (Ashby, Greenhouse, Lever, Personio,
Recruitee, Workable) by reading the company website's careers page, or by trying the usual board
names. Company lists are cached for 7 days, board lookups for 30 days.
"""
from __future__ import annotations

import datetime as dt
import html as _html
import json
import re
from collections import Counter
from pathlib import Path

import requests
from bs4 import BeautifulSoup

from ..llm import LLMError, ask_json
from ..log import log
from ..models import Job, canonical_company
from ..util import UA, get, html_to_text, normalize_location
from . import boards

LIST_TTL, BOARD_TTL = 7, 30

YC_DATA = "https://yc-oss.github.io/api/companies/all.json"
# default YC filter: hardware / deep tech. Override with networks.yc_tags (regex) in config.yaml
YC_TAGS = (r"hard tech|hardware|robot|aerospace|space|semiconductor|quantum|photonic|optic|sensor|drone|"
           r"lidar|manufactur|defense|medical device|iot|electronic|battery|chip|laser|fusion|nuclear|"
           r"industrial|satellite|3d print|autonomous|energy|climate|electric vehicle|automotive")

ATS_PATTERNS = [
    ("ashby", r"jobs\.ashbyhq\.com/([A-Za-z0-9._%-]+)"),
    ("greenhouse", r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)"),
    ("lever", r"jobs\.lever\.co/([A-Za-z0-9._-]+)"),
    ("personio", r"([a-z0-9-]+)\.jobs\.personio\.(?:de|com)"),
    ("recruitee", r"([a-z0-9-]+)\.recruitee\.com"),
    ("workable", r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
]
BAD_TOKENS = {"embed", "js", "api", "jobs", "www", "careers", "job_board", "static", "assets"}
SKIP_HOSTS = re.compile(r"cdtm|google|gstatic|luma|lu\.ma|spotify|pod\.link|instagram|youtube|linkedin|"
                        r"facebook|twitter|x\.com|vimeo|usercentrics|forms\.gle|apple\.com|svdcdn|w3\.org|notion\.|"
                        r"crunchbase|github|medium\.com|tiktok", re.I)

PREFILTER = """A candidate looks for jobs. Their profile:
{profile}

Below are startups founded by alumni of a Munich founder network. Pick the ones that plausibly
hire someone with this profile. Leave out agencies, consulting, VC funds and companies whose
field clearly does not match. When unsure (e.g. you don't know the company), include it.

Reply ONLY with a JSON array of the exact names you pick.

STARTUPS
{items}
"""


def _quiet(url, **kw):
    """One GET without retries or warnings, for probing board names and websites."""
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15, **kw)
        return r if r.status_code == 200 else None
    except requests.RequestException:
        return None


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", canonical_company(s).lower())


# ---------------------------------------------------------------- company lists

def _loc_rx(locations: list[dict]) -> re.Pattern:
    from ..util import _names
    names = [n for l in locations for n in _names(l)]
    return re.compile("|".join(re.escape(n) for n in names) or r"(?!x)x", re.I)


def yc_companies(locations: list[dict], tags: str = YC_TAGS) -> list[dict]:
    metro_rx, tag_rx = _loc_rx(locations), re.compile(tags, re.I)
    r = get(YC_DATA)
    if r is None:
        return []
    out = []
    for c in r.json():
        if not c.get("isHiring") or c.get("status") != "Active":
            continue
        if not metro_rx.search(c.get("all_locations") or ""):
            continue
        if not tag_rx.search(" ".join(c.get("tags") or []) + " " + (c.get("subindustry") or "")):
            continue
        out.append({"name": c["name"], "website": c.get("website") or "", "networks": [f"YC {c.get('batch', '')}".strip()],
                    "yc_slug": c["slug"], "locations": c.get("all_locations") or "", "about": c.get("one_liner") or ""})
    log.info("YC: %d hiring companies in your cities that match networks.yc_tags", len(out))
    return out


def cdtm_companies() -> list[dict]:
    page = get("https://cdtm.com/startups")
    if page is None:
        return []
    items: dict[str, dict] = {}
    # first page is embedded in the React server payload
    rsc = "".join(json.loads(f'"{c}"') for c in
                  re.findall(r'self\.__next_f\.push\(\[1,"(.*?)"\]\)</script>', page.text, re.S))
    m = re.search(r'"initialStartups":(\[.*?\]),"totalStartups"', rsc)
    for s in json.loads(m.group(1)) if m else []:
        items[s["slug"]] = s
    # the rest comes from the page's "load more" server action; its id changes on redeploy, so read it
    chunk = re.search(r'src="(/_next/static/chunks/app/startups/page-[^"]+\.js)"', page.text)
    js = get("https://cdtm.com" + chunk.group(1)) if chunk else None
    act = re.search(r'createServerReference\)\("([0-9a-f]{30,})"[^)]*"getStartupsPaged"', js.text) if js else None
    if act:
        try:
            r = requests.post("https://cdtm.com/startups", data="[1,1000,\"\"]", timeout=30,
                              headers={"User-Agent": UA, "Next-Action": act.group(1), "Accept": "text/x-component",
                                       "Content-Type": "text/plain;charset=UTF-8"})
            for s in json.loads(r.text.split("\n1:", 1)[1].strip()):
                items[s["slug"]] = s
        except (requests.RequestException, IndexError, ValueError) as e:
            log.warning("CDTM: full list failed (%s), using the first page only", e)
    else:
        log.warning("CDTM: could not find the list endpoint, using the first page only")
    out = [{"name": s["name"], "website": "", "networks": ["CDTM"], "cdtm_slug": s["slug"],
            "about": s.get("short_description") or ""} for s in items.values()]
    log.info("CDTM: %d startups", len(out))
    return out


def mm_companies() -> list[dict]:
    r = get("https://www.manageandmore.de/start-ups/")
    if r is None:
        return []
    soup, out = BeautifulSoup(r.text, "html.parser"), []
    for body in soup.select(".tile-body"):
        h, a = body.find("h2"), body.find("a", href=True)
        if not h or not a or "manageandmore" in a["href"]:
            continue
        text = a.get_text(" ", strip=True)
        if re.search(r"acquisition|pivot|liquidated", text, re.I):
            continue  # no longer an independent company
        out.append({"name": h.get_text(strip=True), "website": a["href"], "networks": ["Manage&More"], "about": ""})
    log.info("Manage&More: %d startups listed", len(out))
    return out


def _cdtm_website(slug: str) -> str:
    r = get(f"https://cdtm.com/startups/{slug}")
    if r is None:
        return ""
    for url in re.findall(r'https?://[A-Za-z0-9.-]+\.[a-z]{2,}[^"\\\s<]*', r.text):
        if not SKIP_HOSTS.search(url.split("/")[2]):
            return url
    return ""


def _prefilter(cands: list[dict], model: str, profile: str, batch: int = 50) -> list[dict]:
    keep: set[str] = set()
    for i in range(0, len(cands), batch):
        chunk = cands[i:i + batch]
        items = "\n".join(f"- {c['name']}" + (f" ({c['website']})" if c["website"] else "")
                          + (f": {c['about']}" if c["about"] else "") for c in chunk)
        try:
            keep |= {_norm(n) for n in ask_json(PREFILTER.format(items=items, profile=profile), model=model, timeout=600)
                     if isinstance(n, str)}
        except (LLMError, ValueError) as e:
            log.warning("network prefilter failed (%s); keeping this batch", e)
            keep |= {_norm(c["name"]) for c in chunk}
    return [c for c in cands if _norm(c["name"]) in keep]


# ---------------------------------------------------------------- job boards

def _find_board_in(text: str) -> tuple[str, str] | None:
    for kind, rx in ATS_PATTERNS:
        for tok in re.findall(rx, text):
            tok = tok.strip("/.").split("?")[0]
            if tok and tok.lower() not in BAD_TOKENS:
                return kind, tok
    return None


def resolve_board(name: str, website: str) -> dict | None:
    """Find a company's job board: careers page on its website first, then the usual board names."""
    if website:
        home = _quiet(website)
        dom = lambda u: ".".join(re.sub(r"^https?://", "", u).split("/")[0].split(".")[-2:]).lower()
        if home is not None and dom(home.url) != dom(website):
            log.debug("%s: website now redirects to %s - not using it", name, home.url)
            home = None  # dead startup whose domain now points elsewhere (e.g. a Notion page)
        if home is not None:
            hit = _find_board_in(home.text)
            if not hit:
                base = home.url.rstrip("/")
                links = re.findall(r'href="([^"]*(?:career|jobs|karriere|join|open-positions|work-with-us)[^"]*)"',
                                   home.text, re.I)
                seen = set()
                for l in links[:6] + [base + "/careers", base + "/jobs", base + "/karriere"]:
                    url = l if l.startswith("http") else base + "/" + l.lstrip("/")
                    if url in seen:
                        continue
                    seen.add(url)
                    page = _quiet(url)
                    if page is not None and (hit := _find_board_in(page.text)):
                        break
            if hit:
                return {"ats": hit[0], "token": hit[1]}
    # guess: the board name is usually the company name or the website's domain
    slugs = {_norm(name), canonical_company(name).lower().replace(" ", "-")}
    if website:
        slugs.add(re.sub(r"^www\.", "", website.split("/")[2] if "//" in website else website).split(".")[0])
    for s in [x for x in slugs if len(x) >= 5]:
        probes = [("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{s}", lambda r: r.json().get("jobs")),
                  ("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs", lambda r: r.json().get("jobs")),
                  ("lever", f"https://api.lever.co/v0/postings/{s}?mode=json", lambda r: r.json()),
                  ("personio", f"https://{s}.jobs.personio.de/xml", lambda r: "<position>" in r.text)]
        for kind, url, ok in probes:
            r = _quiet(url)
            try:
                if r is not None and ok(r):
                    return {"ats": kind, "token": s}
            except ValueError:
                continue
    return None


# ---------------------------------------------------------------- YC job pages

def _page_props(url: str) -> dict | None:
    r = get(url)
    m = re.search(r'data-page="([^"]+)"', r.text) if r is not None else None
    return json.loads(_html.unescape(m.group(1)))["props"] if m else None


def yc_jobs(c: dict, title_ok, locations: list[dict]) -> list[Job]:
    props = _page_props(f"https://www.ycombinator.com/companies/{c['yc_slug']}/jobs")
    out = []
    for p in (props or {}).get("jobPostings", []):
        if not title_ok(p.get("title", "")):
            continue
        loc = p.get("location") or ""
        if not any(normalize_location(x, locations) for x in re.split(r"[/;,]| or ", loc)):
            loc = f"{loc} / {c['locations'].split(';')[0]}"  # "HQ", "Remote", or a suburb we don't know
        head = "\n".join(f"{k}: {v}" for k, v in (("Visa", p.get("visa")), ("Experience", p.get("minExperience")),
                                                    ("Salary", p.get("salaryRange")), ("Equity", p.get("equityRange")))
                         if v)
        out.append(Job(title=p["title"], company=c["name"], location=loc,
                       url="https://www.ycombinator.com" + p["url"], source="yc",
                       extra={"network": c["networks"], "yc_head": head}))
    return out


def yc_fetch_detail(j: Job) -> None:
    """Full description of a YC posting (called only for the top-ranked new postings)."""
    job = (_page_props(j.url) or {}).get("job") or {}
    desc = (job.get("description") or "").strip()
    if desc:
        j.description = (j.extra.get("yc_head", "") + "\n\n" + desc).strip()


# ---------------------------------------------------------------- entry points

class Networks:
    def __init__(self, state_dir: Path, cfg: dict):
        self.path = state_dir / "networks.json"
        self.cfg = cfg
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self):
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))

    def companies(self) -> list[dict]:
        t = self.data.get("time")
        if t and (dt.datetime.now() - dt.datetime.fromisoformat(t)).days < LIST_TTL:
            return self.data["companies"]
        want = self.cfg.get("networks") or {}
        old = {_norm(c["name"]): c for c in self.data.get("companies", [])}
        munich = []
        if want.get("cdtm"):
            munich += cdtm_companies()
        if want.get("manage_and_more"):
            munich += mm_companies()
        merged: dict[str, dict] = {}
        for c in munich:  # e.g. Konux is in both
            k = _norm(c["name"])
            if k in merged:
                merged[k]["networks"] = sorted(set(merged[k]["networks"] + c["networks"]))
                merged[k]["website"] = merged[k]["website"] or c["website"]
            else:
                merged[k] = c
        cands = list(merged.values())
        if cands:
            cands = _prefilter(cands, self.cfg.get("tailoring", {}).get("scoring_model", "sonnet"),
                               self.cfg.get("candidate", {}).get("summary", ""))
            log.info("networks: %d CDTM / Manage&More startups could hire for your profile", len(cands))
        yc = yc_companies(self.cfg.get("locations", []), want.get("yc_tags") or YC_TAGS) if want.get("yc") else []
        comps = cands + yc
        for c in comps:  # keep board lookups from the last refresh
            prev = old.get(_norm(c["name"]))
            if prev:
                for k in ("board", "board_checked", "website"):
                    if prev.get(k) and not c.get(k):
                        c[k] = prev[k]
        self.data = {"time": dt.datetime.now().isoformat(), "companies": comps,
                     "all_names": {_norm(c["name"]): c["networks"] for c in munich + yc}}
        self._save()
        return comps

    def tag_of(self, company: str) -> list[str]:
        """Network labels of any company, also for postings found on LinkedIn etc."""
        names = self.data.get("all_names", {})
        return names.get(_norm(company), [])

    def search(self, title_ok) -> list[Job]:
        jobs: list[Job] = []
        today = dt.date.today()
        comps = self.companies()
        for c in comps:
            if c.get("cdtm_slug") and not c.get("website") and not c.get("board_checked"):
                c["website"] = _cdtm_website(c["cdtm_slug"])
        # a website or board shared by several startups is a placeholder (alumni page, dead domain), not theirs
        site_n = Counter(c["website"] for c in comps if c.get("website"))
        for c in comps:
            if c.get("website") and site_n[c["website"]] > 1:
                c["website"], c["board"], c["board_checked"] = "", None, None
        for c in comps:
            if c.get("yc_slug"):
                jobs += yc_jobs(c, title_ok, self.cfg.get("locations", []))
                continue
            checked = c.get("board_checked")
            if not checked or (today - dt.date.fromisoformat(checked)).days >= BOARD_TTL:
                c["board"] = resolve_board(c["name"], c.get("website", ""))
                c["board_checked"] = today.isoformat()
                log.info("  %s: %s", c["name"], f"{c['board']['ats']}/{c['board']['token']}" if c["board"] else "no job board found")
                self._save()
        board_n = Counter(json.dumps(c["board"], sort_keys=True) for c in comps if c.get("board"))
        for c in comps:
            if c.get("board") and board_n[json.dumps(c["board"], sort_keys=True)] > 2:
                log.info("  %s: board %s is shared by several startups - ignored", c["name"], c["board"])
                c["board"] = None
            if c.get("board") and not c.get("yc_slug"):
                got = boards.search([{"name": c["name"], **c["board"]}])
                for j in got:
                    j.extra["network"] = c["networks"]
                jobs += got
        log.info("networks: %d postings from YC / CDTM / Manage&More startups", len(jobs))
        return jobs
