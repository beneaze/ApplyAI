"""ApplyAI: find jobs that fit you, and get a tailored one-page CV and cover letter for each.

Daily use
    applyai run                    scan -> screen -> tailor (default)
    applyai scan                   scan + screen only, print the shortlist, write no folders
    applyai pending                tailor the stored shortlist without scanning again
    applyai tailor URL             tailor for one posting you found yourself
    applyai status                 what is new, what waits to be sent
    applyai queue [--json]         tailored applications not sent yet
    applyai mark FOLDER STATUS     log applied / skipped / interview / offer / rejected
    applyai dashboard [--open]     rebuild _Dashboard.html (stats, salaries, ratings, links to documents)
Setup
    applyai init [PATH]            create a workspace (default ~/Job Applications)
    applyai doctor                 check tools, Claude login and profile
    applyai import-cv FILE         print a CV (PDF / DOCX / TEX / MD) as text
    applyai context                digest of earlier Claude conversations (for the fact bank)
    applyai compile-base           compile profile/cv_base.tex and report pages / fill / ATS parse
    applyai find-board COMPANY     find a company's Greenhouse / Lever / Ashby / ... job board
    applyai schedule HH:MM         daily unattended run (macOS launchd / Linux cron); --remove
    applyai save-token             store a `claude setup-token` token for unattended runs
Maintenance
    applyai ats FOLDER             re-check a CV after you edited its .tex
    applyai rerender [FOLDER]      re-fit tailored CVs after changing resume.cls / rules
    applyai ratings [refresh]      kununu / Glassdoor ratings for every application
"""
from __future__ import annotations

import argparse
import datetime as dt
import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from . import ats, dashboard, latex, match, output, ratings, schedule
from .llm import LLMError, ask
from .log import log, setup
from .models import Job
from .sources import boards, browser_agent, jobsch, linkedin, networks, stepstone
from .store import Store
from .tailor import Profile, Tailor
from .tracker import STATUSES, Tracker
from .util import get, html_to_text, local_name, normalize_location
from .workspace import EXAMPLES, POINTER, TOKEN_FILE, Workspace

DETAIL = {"linkedin": linkedin.fetch_detail, "jobs.ch": jobsch.fetch_detail,
          "stepstone": stepstone.fetch_detail, "yc": networks.yc_fetch_detail}
NEEDS_CLAUDE = ("run", "scan", "pending", "tailor", "ratings", "rerender")


def load_cfg(ws: Workspace) -> dict:
    if not ws.exists():
        sys.exit(f"No ApplyAI workspace at {ws.root}. Run `applyai init` (or /applyai:setup in Claude Code).")
    cfg = yaml.safe_load(ws.config.read_text()) or {}
    for k in ("candidate", "tailoring", "sources", "me"):
        cfg[k] = cfg.get(k) or {}
    t = cfg["tailoring"]
    for k, v in {"scoring_model": "sonnet", "writer_model": "opus", "editor_model": "sonnet",
                 "factcheck_model": "sonnet"}.items():
        t.setdefault(k, v)
    cfg["locations"] = cfg.get("locations") or []
    return cfg


def _loc_names(cfg) -> list[str]:
    return [l["name"] for l in cfg["locations"]] + (["Remote"] if cfg.get("remote") else [])


def collect(ws: Workspace, cfg, only=None) -> list[Job]:
    src, days, locs = cfg["sources"], cfg.get("days_back", 14), cfg["locations"]
    on = lambda k: src.get(k) and (not only or k in only)
    queries = cfg.get("queries", [])
    jobs: list[Job] = []
    if on("linkedin"):
        jobs += linkedin.search(queries, locs, days=days)
    swiss = [local_name(l) for l in locs if l.get("country") == "Switzerland"]
    if on("jobs_ch") and swiss:
        jobs += jobsch.search(queries, places=swiss, days=days)
    german = [local_name(l) for l in locs if l.get("country") == "Germany"]
    if on("stepstone") and german:
        jobs += stepstone.search(cfg.get("queries_local", []) + queries[:6], cities=german, days=days)
    if on("company_boards"):
        jobs += boards.search(cfg.get("company_boards", []))
    if on("browser_agent"):
        jobs += browser_jobs(ws, cfg, days)
    nets = networks.Networks(ws.state, cfg)
    if on("networks"):
        inc = re.compile("|".join(cfg.get("title_include") or [".*"]), re.I)
        exc = re.compile("|".join(cfg["title_exclude"]), re.I) if cfg.get("title_exclude") else None
        jobs += nets.search(lambda t: bool(inc.search(t)) and not (exc and exc.search(t)))
    for j in jobs:  # founder-network tag also for postings found on LinkedIn, boards, ...
        if not j.extra.get("network"):
            tag = nets.tag_of(j.company)
            if tag:
                j.extra["network"] = tag
    return jobs


def browser_jobs(ws: Workspace, cfg, days) -> list[Job]:
    """Computer-use scan in a headless browser, cached for 20 h so a same-day re-run doesn't browse again."""
    cache = ws.state / "browser_cache.json"
    if cache.exists():
        c = json.loads(cache.read_text())
        age = dt.datetime.now() - dt.datetime.fromisoformat(c["time"])
        if age < dt.timedelta(hours=20):
            log.info("browser agent: reusing %d postings scanned %s ago", len(c["jobs"]), str(age).split(".")[0])
            return [Job.from_dict(d) for d in c["jobs"]]
    jobs = browser_agent.search(cfg.get("browser_targets", []), queries=cfg.get("queries", []),
                                metros=_loc_names(cfg), profile=cfg["candidate"].get("summary", ""), days=days,
                                headless=cfg.get("browser_headless", True),
                                model=cfg.get("browser_model", "sonnet"),
                                browser=cfg.get("browser", "chrome"))
    cache.write_text(json.dumps({"time": dt.datetime.now().isoformat(),
                                 "jobs": [j.to_dict() for j in jobs]}, ensure_ascii=False))
    return jobs


def localize(jobs: list[Job], cfg) -> list[Job]:
    out = []
    for j in jobs:
        # multi-location postings: take the first location that is one of ours
        for part in re.split(r"\s*/\s*|;|\s+or\s+|\|", j.location) or [j.location]:
            loc = normalize_location(part, cfg["locations"], cfg.get("remote", False))
            if loc:
                j.country, j.city = loc
                out.append(j)
                break
    return out


def dedupe(jobs: list[Job]) -> list[Job]:
    # prefer sources that carry a full description (company boards / browser) over cards
    rank = lambda j: (bool(j.description), j.source != "linkedin")
    best: dict[str, Job] = {}
    for j in jobs:
        if j.key not in best or rank(j) > rank(best[j.key]):
            best[j.key] = j
    return list(best.values())


def _score(jobs, cfg, profile: Profile):
    c = cfg["candidate"]
    match.score(jobs, profile=c.get("summary", ""), facts=profile.facts, model=cfg["tailoring"]["scoring_model"],
                rules=c.get("screening_rules", ""), cv_lang=cfg["tailoring"].get("cv_language", "English"))


def screen(ws: Workspace, cfg, store: Store, only=None) -> list[Job]:
    raw = collect(ws, cfg, only)
    log.info("collected %d postings", len(raw))
    jobs = dedupe(localize(raw, cfg))
    log.info("%d in %s after de-duplication", len(jobs), " / ".join(_loc_names(cfg)))
    jobs = match.title_filter(jobs, cfg.get("title_include") or [".*"], cfg.get("title_exclude") or [])
    log.info("%d pass the title filter", len(jobs))
    new = [j for j in jobs if not store.seen(j.key)]
    log.info("%d are new", len(new))
    cap = cfg.get("max_score_per_run", 120)
    # startup titles are generic ("Founding Engineer"): read YC postings before ranking
    yc_new = [j for j in new if j.source == "yc" and not j.description]
    if yc_new:
        log.info("reading %d new YC postings before ranking", len(yc_new))
        for j in yc_new:
            networks.yc_fetch_detail(j)
    # rank on what we have, fetch full descriptions for the best, re-rank with them
    new = match.rank(new, cfg)[: int(cap * 1.5)]
    log.info("fetching descriptions for %d top-ranked postings", len(new))
    fails: dict[str, int] = {}
    for j in new:
        if j.description or j.source not in DETAIL:
            continue
        if fails.get(j.source, 0) >= 3:
            continue  # source is blocking us right now: skip, retry next run
        DETAIL[j.source](j)
        fails[j.source] = 0 if j.description else fails.get(j.source, 0) + 1
        if fails[j.source] == 3:
            log.warning("%s keeps refusing detail pages - skipping its remaining postings this run", j.source)
    # without a description the screening would be guesswork; those stay unseen for next run
    new = match.rank([j for j in new if j.description], cfg)[:cap]
    log.info("%d postings go to Claude for screening", len(new))
    _score(new, cfg, Profile(ws.profile, cfg))
    floor = cfg.get("min_fit_score", 6)
    for j in new:
        if j.fit_score is None:
            continue  # scoring failed; retry next run
        if j.fit_score < floor:
            store.put(j.key, status="rejected", fit=j.fit_score, title=j.title, company=j.company,
                      city=j.city, url=j.url, reason=j.fit_reason,
                      job=j.to_dict() if j.fit_score >= 4 else None)
        else:
            store.put(j.key, status="shortlisted", fit=j.fit_score, title=j.title, company=j.company,
                      city=j.city, url=j.url, job=j.to_dict())
    # a lowered min_fit_score promotes earlier near-misses without re-screening them
    for k, r in store.data.items():
        if r.get("status") == "rejected" and r.get("job") and r.get("fit", 0) >= floor:
            store.put(k, status="shortlisted")
    return shortlist(store)


def shortlist(store: Store) -> list[Job]:
    """Everything screened in but not tailored yet (also left over from earlier runs)."""
    return sorted([Job.from_dict(r["job"]) for r in store.data.values()
                   if r.get("status") == "shortlisted" and r.get("job")], key=lambda j: -(j.fit_score or 0))


def tailor_jobs(ws: Workspace, cfg, store: Store, jobs: list[Job]):
    profile = Profile(ws.profile, cfg)
    t = Tailor(profile, cfg)
    rt = ratings.Ratings(ws.state / "ratings.json", cfg["tailoring"]["scoring_model"]) \
        if cfg.get("ratings", True) else None
    done = 0
    cap = cfg.get("max_tailor_per_run", 10)
    for i, j in enumerate(jobs[:cap], 1):
        folder = output.job_folder(ws.root, j)
        log.info("[%d/%d] %s — %s (%s) fit %.0f", i, min(len(jobs), cap), j.company, j.title, j.city, j.fit_score or 0)
        try:
            res = t.run(j, folder)
            rating = rt.get(j.company, j.country, j.location) if rt else None
        except (LLMError, latex.LatexError, ValueError, KeyError) as e:
            log.error("  failed: %s", str(e)[:400])
            output.write_job_md(folder, j, None)
            store.put(j.key, status="failed", fit=j.fit_score, title=j.title, company=j.company,
                      city=j.city, url=j.url, folder=str(folder), error=str(e)[:400])
            continue
        output.write_job_md(folder, j, res, rating)
        (folder / "_job.json").write_text(json.dumps(j.to_dict(), indent=1, ensure_ascii=False))
        store.put(j.key, status="tailored", fit=j.fit_score, ats=res["ats"], title=j.title,
                  company=j.company, city=j.city, url=j.url, folder=str(folder), rating=rating,
                  network=j.extra.get("network") or [], job=j.to_dict())
        log.info("  -> %s (ATS %d)", folder.relative_to(ws.root), res["ats"])
        done += 1
    output.write_overview(ws.root, store.data)
    Tracker(ws).write_csv(store.data)
    refresh_dashboard(ws, cfg, store)
    return done


def refresh_dashboard(ws: Workspace, cfg, store: Store) -> Path:
    return dashboard.write(ws, store.data, Tracker(ws).data, cfg)


def _open(path: Path):
    opener = "open" if platform.system() == "Darwin" else "xdg-open"
    if shutil.which(opener):
        subprocess.run([opener, str(path)], capture_output=True)


def claude_check() -> tuple[bool, str]:
    """Cheap check before a run, so a dead login fails in seconds, not after 100 calls.
    Returns (ok, reason) with a reason a person can act on."""
    try:
        return ("OK" in ask("Reply with exactly: OK", model="haiku", timeout=120)), ""
    except LLMError as e:
        msg = str(e)
        log.debug("auth check failed: %s", msg)
        if re.search(r"ENOTFOUND|ECONNREFUSED|ETIMEDOUT|reach the API|network|timed out", msg, re.I):
            return False, "no internet connection (or the Claude API is unreachable)"
        if re.search(r"revoked|expired|authenticate|log ?in|401|invalid.*token", msg, re.I):
            return False, ("Claude login expired or revoked: run `claude setup-token` in a terminal, "
                           "then `applyai save-token`")
        if re.search(r"rate|limit|429|quota|usage", msg, re.I):
            return False, "Claude usage limit reached: try again later"
        return False, "Claude CLI failed: " + msg[:200]


def claude_ok() -> bool:
    return claude_check()[0]


def record_run(ws: Workspace, command: str, ok: bool, new: int = 0, error: str = ""):
    """Append to .applyai/state/runs.json (the dashboard shows the recent history)."""
    f = ws.state / "runs.json"
    try:
        runs = json.loads(f.read_text()) if f.exists() else []
    except ValueError:
        runs = []
    runs.append({"at": dt.datetime.now().isoformat(timespec="minutes"), "command": command, "ok": ok,
                 "new": new, "error": error[:300], "scheduled": bool(os.environ.get("APPLYAI_NOTIFY"))})
    f.write_text(json.dumps(runs[-400:], indent=0))


def rerender(ws: Workspace, cfg, store: Store, only: Path | None = None):
    """Re-apply the rules + one-page fit to tailored CVs without rewriting their content."""
    from .tailor import _validate_body
    profile = Profile(ws.profile, cfg)
    t = Tailor(profile, cfg)
    folders = [only] if only else [Path(r["folder"]) for r in store.data.values()
                                    if r.get("status") == "tailored" and r.get("folder")]
    for folder in folders:
        cv = next(folder.glob("CV_*.tex"), None)
        if not cv or not (folder / "_job.json").exists():
            continue
        j = Job.from_dict(json.loads((folder / "_job.json").read_text()))
        tex = cv.read_text()
        m = re.search(r"\\headline\{(.*)\}", tex)
        t.headline = m.group(1).replace("\\&", "&") if m else ""
        body = _validate_body(tex.split("\\begin{document}", 1)[1].rsplit("\\end{document}", 1)[0], t.base, cfg)
        shutil.copy(profile.resume_cls, folder / "resume.cls")
        try:
            body, pdf, _, _ = t._fit_cv(body, j, folder, cv.stem)
        except (LLMError, latex.LatexError) as e:
            log.error("%s: %s", folder.name, e)
            continue
        rep = t._ats(pdf, j)
        old = (folder / "ATS_Report.md").read_text() if (folder / "ATS_Report.md").exists() else ""
        skipped = old[old.find("## Keywords skipped"):old.find("\nLayout:")] if "## Keywords skipped" in old else ""
        (folder / "ATS_Report.md").write_text(rep.markdown(j.title) + ("\n" + skipped if skipped else "")
                                              + f"\nLayout: your design. CV pages: {latex.pages(pdf)}.\n")
        md = folder / "Job.md"
        if md.exists():
            md.write_text(re.sub(r"ATS score of tailored CV:\*\* \d+/100",
                                 f"ATS score of tailored CV:** {rep.score:.0f}/100", md.read_text()))
        for k, r in store.data.items():
            if r.get("folder") == str(folder):
                store.put(k, ats=round(rep.score))
        log.info("re-rendered %s/%s (ATS %.0f, %d page)", j.company, j.title, rep.score, latex.pages(pdf))
    output.write_overview(ws.root, store.data)
    refresh_dashboard(ws, cfg, store)


def refresh_ratings(ws: Workspace, cfg, store: Store, force: bool = False):
    """Look up kununu / Glassdoor for every tailored application and update Job.md + overview."""
    rt = ratings.Ratings(ws.state / "ratings.json", cfg["tailoring"]["scoring_model"])
    if force:
        rt.data = {}
    nets = networks.Networks(ws.state, cfg)
    for k, r in list(store.data.items()):
        if r.get("status") != "tailored" or not r.get("folder"):
            continue
        job = r.get("job") or {}
        rec = rt.get(r["company"], job.get("country", ""), job.get("location", r.get("city", "")))
        net = r.get("network") or nets.tag_of(r["company"])
        store.put(k, rating=rec, network=net)
        md = Path(r["folder"]) / "Job.md"
        if md.exists():
            text = re.sub(r"\n- \*\*(?:Employer rating|Founder network):\*\*.*?(?=\n- \*\*|\n\n)", "", md.read_text(), flags=re.S)
            add = ratings.md_line(rec) + (f"\n{output.network_line(net)}" if net else "")
            text = re.sub(r"(\n- \*\*Fit score:\*\*[^\n]*)", lambda m: m.group(1) + "\n" + add, text, count=1)
            md.write_text(text)
    output.write_overview(ws.root, store.data)
    refresh_dashboard(ws, cfg, store)


def job_from_url(url: str) -> Job:
    m = re.search(r"linkedin\.com/jobs/view/(?:[^/]*-)?(\d+)", url)
    if m:
        j = Job(title="", company="", location="", url=url, source="linkedin", extra={"linkedin_id": m.group(1)})
        r = get(linkedin.DETAIL.format(id=m.group(1)))
        if r is not None:
            from bs4 import BeautifulSoup
            s = BeautifulSoup(r.text, "html.parser")
            pick = lambda sel: (s.select_one(sel).get_text(" ", strip=True) if s.select_one(sel) else "")
            j.title, j.company = pick(".top-card-layout__title"), pick(".topcard__org-name-link")
            j.location = pick(".topcard__flavor--bullet")
            linkedin.fetch_detail(j)
        return j
    r = get(url)
    text = html_to_text(r.text)[:15000] if r is not None else ""
    if len(text) < 500:
        raise SystemExit("Could not read the page — save the job description to a file and pass --desc-file.")
    info = ask("From this job posting page, return ONLY JSON {\"title\":..., \"company\":..., "
               "\"location\": \"city, country\", \"description\": \"the full job description text\"}"
               "\n\n" + text, model="sonnet")
    from .llm import extract_json
    d = extract_json(info)
    return Job(title=d["title"], company=d["company"], location=d["location"], url=url,
               source="manual", description=d["description"])


# ------------------------------------------------------------------ setup helpers

def cmd_init(path: str | None, example: bool):
    root = Path(path).expanduser().resolve() if path else Workspace().root
    ws = Workspace(root)
    ws.profile.mkdir(parents=True, exist_ok=True)
    _ = ws.state, ws.logs  # creates them
    if not ws.config.exists():
        shutil.copy(EXAMPLES / "config.yaml", ws.config)
    for f in (EXAMPLES / "profile").iterdir():
        dst = ws.profile / f.name
        if not dst.exists() and (example or f.name in ("cv_rules.md", "answers.md")):
            shutil.copy(f, dst)
    POINTER.parent.mkdir(parents=True, exist_ok=True)
    POINTER.write_text(str(root) + "\n")
    print(f"Workspace: {root}")
    print("Profile:   " + ("example persona copied (Alex Morgan) — replace it with your own" if example
                           else "empty — run /applyai:setup in Claude Code to fill it"))


def cmd_doctor(ws: Workspace) -> int:
    ok = True
    tools = {"claude": "Claude Code CLI: https://claude.com/claude-code",
             "pdflatex": "TeX: macOS `brew install --cask mactex-no-gui`, Ubuntu `sudo apt install texlive-latex-extra texlive-fonts-recommended`",
             "pdftotext": "poppler: macOS `brew install poppler`, Ubuntu `sudo apt install poppler-utils`",
             "pdfinfo": "poppler (see above)",
             "npx": "Node.js (only for the browser agent): macOS `brew install node`, Ubuntu `sudo apt install nodejs npm`"}
    print(f"ApplyAI {_version()} · Python {platform.python_version()} · {platform.system()}")
    for t, hint in tools.items():
        have = shutil.which(t)
        print(f"  {'✓' if have else '✗'} {t:10s} {have or hint}")
        ok &= bool(have) or t == "npx"
    if shutil.which("kpsewhich"):
        missing = [p for p in ("tabularx", "titlesec", "enumitem", "cmap", "lmodern", "babel", "geometry", "hyperref")
                   if subprocess.run(["kpsewhich", f"{p}.sty"], capture_output=True).returncode != 0]
        if missing:
            ok = False
            print(f"  ✗ LaTeX packages missing: {', '.join(missing)} (install the full TeX distribution)")
    auth, why = claude_check() if shutil.which("claude") else (False, "claude CLI not installed")
    print(f"  {'✓' if auth else '✗'} Claude login {'OK' if auth else 'FAILED: ' + why}")
    ok &= auth
    print(f"\nWorkspace: {ws.root} {'(found)' if ws.exists() else '(not created yet: applyai init)'}")
    if ws.exists():
        miss = ws.missing_profile()
        print("  profile complete" if not miss else f"  profile missing: {', '.join(miss)} (run /applyai:setup)")
        ok &= not miss
    return 0 if ok else 2


def _version() -> str:
    try:
        return json.loads((Path(__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json").read_text())["version"]
    except (OSError, KeyError, ValueError):
        return "dev"


def cmd_compile_base(ws: Workspace, cfg: dict) -> int:
    """Compile the base CV the way tailored ones are compiled and report what the setup needs."""
    profile = Profile(ws.profile, cfg)
    out = ws.state / "base_preview"
    out.mkdir(parents=True, exist_ok=True)
    tex = out / "cv_base.tex"
    shutil.copy(ws.profile / "cv_base.tex", tex)
    shutil.copy(profile.resume_cls, out / "resume.cls")
    for extra in ("photo.jpg", "signature.png"):
        if (ws.profile / extra).exists():
            shutil.copy(ws.profile / extra, out / extra)
    try:
        pdf = latex.compile_pdf(tex)
    except latex.LatexError as e:
        print(f"LaTeX error: {e}")
        return 2
    shutil.copy(pdf, ws.profile / "cv_base.pdf")
    me = cfg.get("me", {})
    warn, *_ = ats.parse_check(pdf, name=me.get("name", ""), email=me.get("email", ""),
                               phone_digits=re.sub(r"\D", "", me.get("phone_cv", "")))
    report = {"pdf": str(ws.profile / "cv_base.pdf"), "pages": latex.pages(pdf),
              "overflow_lines": latex.overflow_lines(pdf), "page_fill": round(latex.page_fill(pdf), 3),
              "one_word_last_lines": latex.runts(pdf), "ats_parse_warnings": warn}
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0 if report["pages"] == 1 else 1


def cmd_status(ws: Workspace, store: Store, as_json: bool):
    tr = Tracker(ws)
    counts: dict[str, int] = {}
    for r in store.data.values():
        counts[r.get("status", "?")] = counts.get(r.get("status", "?"), 0) + 1
    sent: dict[str, int] = {}
    for a in tr.data.values():
        sent[a.get("status", "?")] = sent.get(a.get("status", "?"), 0) + 1
    queue = tr.queue(store.data)
    last = sorted(ws.logs.glob("*_run.log"))[-1:]
    info = {"workspace": str(ws.root), "postings_seen": len(store.data), "by_status": counts,
            "sent": sent, "waiting_to_send": len(queue), "shortlisted_not_tailored": counts.get("shortlisted", 0),
            "last_run_log": str(last[0]) if last else None}
    if as_json:
        print(json.dumps(info, indent=1))
        return
    print(f"Workspace {ws.root}")
    print(f"  seen {len(store.data)} postings: " + (", ".join(f"{v} {k}" for k, v in sorted(counts.items())) or "none yet"))
    print("  sent: " + (", ".join(f"{v} {k}" for k, v in sent.items()) or "nothing logged yet"))
    print(f"  {len(queue)} tailored applications wait to be sent (applyai queue)")
    if last:
        print(f"  last run: {last[0].name}")


def cmd_save_token():
    tok = getpass.getpass("Paste the token from `claude setup-token` (input hidden): ").strip()
    m = re.search(r"sk-ant-[A-Za-z0-9_-]+", tok)
    if not m:
        sys.exit("That doesn't look like a token (it starts with sk-ant-). Nothing saved.")
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(m.group(0) + "\n")
    TOKEN_FILE.chmod(0o600)
    print(f"Saved to {TOKEN_FILE}. Test it with: applyai doctor")


def _load_token():
    if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") and TOKEN_FILE.exists():
        m = re.search(r"sk-ant-[A-Za-z0-9_-]+", TOKEN_FILE.read_text())
        if m:
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = m.group(0)


def _stay_awake():
    """macOS: don't let idle sleep interrupt a long run."""
    if platform.system() == "Darwin" and shutil.which("caffeinate"):
        try:
            subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())])
        except OSError:
            pass


# ------------------------------------------------------------------ main

COMMANDS = ["run", "scan", "pending", "tailor", "ats", "rerender", "check", "doctor", "ratings", "init",
            "import-cv", "context", "compile-base", "find-board", "status", "queue", "mark", "schedule",
            "save-token", "where", "dashboard"]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="applyai", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", nargs="?", default="run", choices=COMMANDS)
    ap.add_argument("target", nargs="?", help="URL for 'tailor', folder for 'ats' / 'mark', path for 'init' / 'import-cv'")
    ap.add_argument("extra", nargs="?", help="status for 'mark' (" + ", ".join(STATUSES) + ")")
    ap.add_argument("--only", help="comma list of sources: linkedin,jobs_ch,stepstone,company_boards,networks,browser_agent")
    ap.add_argument("--limit", type=int, help="max applications to tailor this run")
    ap.add_argument("--desc-file", help="job description text file (for 'tailor')")
    ap.add_argument("--title"), ap.add_argument("--company"), ap.add_argument("--location")
    ap.add_argument("--example", action="store_true", help="init: copy the example persona")
    ap.add_argument("--json", action="store_true", help="machine-readable output (status, queue)")
    ap.add_argument("--via", default="", help="mark: where it was sent (company site, LinkedIn, email, ...)")
    ap.add_argument("--note", default="", help="mark: free note")
    ap.add_argument("--salary", default="", help="mark: salary expectation given")
    ap.add_argument("--website", default="", help="find-board: company website")
    ap.add_argument("--export", help="context: claude.ai export (.zip, folder or conversations.json)")
    ap.add_argument("--terms", default="", help="context: comma list of words that matter (skills, employers)")
    ap.add_argument("--no-code", action="store_true", help="context: skip Claude Code transcripts")
    ap.add_argument("--max-chars", type=int, default=60000, help="context: size of the digest")
    ap.add_argument("--remove", action="store_true", help="schedule: remove the daily run")
    ap.add_argument("--open", action="store_true", help="dashboard: open it in the browser")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args(argv)
    ws = Workspace()

    # commands that work without a workspace or a login
    if a.command == "init":
        return cmd_init(a.target, a.example)
    if a.command == "where":
        print(ws.root)
        return
    if a.command == "save-token":
        return cmd_save_token()
    if a.command == "import-cv":
        from .cvtext import read_cv
        if not a.target:
            sys.exit("usage: applyai import-cv FILE")
        print(read_cv(Path(a.target)))
        return
    if a.command == "find-board":
        if not a.target:
            sys.exit("usage: applyai find-board COMPANY [--website URL]")
        hit = networks.resolve_board(a.target, a.website)
        if hit:
            n = len(boards.search([{"name": a.target, **hit}]))
            print(json.dumps({"name": a.target, **hit, "open_postings": n}))
        else:
            print(json.dumps({"name": a.target, "board": None,
                              "hint": "no public Greenhouse/Lever/Ashby/Personio/Recruitee/Workable board found; "
                                      "add the careers page as a browser_target instead"}))
        return
    if a.command == "context":
        from .context import digest
        claude_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
        text, stats = digest(claude_dir=claude_dir, export=Path(a.export).expanduser() if a.export else None,
                             terms=[t.strip() for t in a.terms.split(",") if t.strip()],
                             skip=[ws.root, Path(__file__).resolve().parent.parent],
                             max_chars=a.max_chars, include_code=not a.no_code)
        out = (ws.state if ws.exists() else Path.cwd()) / "context_digest.md"
        out.write_text(text)
        print(json.dumps({"digest": str(out), **stats, "chars": len(text)}))
        return

    _load_token()
    setup(a.verbose, ws.logs / f"{dt.datetime.now():%Y-%m-%d_%H%M}_{a.command}.log" if ws.exists() else None)
    if a.command in ("check", "doctor"):
        sys.exit(cmd_doctor(ws))
    if a.command == "schedule":
        print(schedule.remove() if a.remove else schedule.install(a.target or "07:30", ws.root))
        return

    cfg = load_cfg(ws)
    store = Store(ws.state / "seen.json")
    if a.command == "status":
        return cmd_status(ws, store, a.json)
    if a.command == "queue":
        q = Tracker(ws).queue(store.data)
        if a.json:
            print(json.dumps(q, indent=1, ensure_ascii=False))
            return
        for i, r in enumerate(q, 1):
            print(f"{i:3d}. fit {r['fit'] or 0:.0f} ATS {r['ats'] or 0:3d}  {r['company']} — {r['title']} ({r['city']})\n"
                  f"      {r['apply_url'] or r['url']}\n      {r['folder']}")
        if not q:
            print("Nothing waiting. Run `applyai run` or check `applyai status`.")
        return
    if a.command == "mark":
        if not a.target or a.extra not in STATUSES:
            sys.exit(f"usage: applyai mark FOLDER {{{'|'.join(STATUSES)}}} [--via ...] [--note ...] [--salary ...]")
        rec = Tracker(ws).mark(store.data, a.target, a.extra, a.via, a.note, a.salary)
        refresh_dashboard(ws, cfg, store)
        print(f"{rec['company']} — {rec['title']}: {rec['status']}")
        return
    if a.command == "dashboard":
        out = refresh_dashboard(ws, cfg, store)
        print(out)
        if a.open:
            _open(out)
        return
    if a.command == "compile-base":
        sys.exit(cmd_compile_base(ws, cfg))

    miss = ws.missing_profile()
    if miss:
        sys.exit(f"Profile incomplete ({', '.join(miss)} missing in {ws.profile}). Run /applyai:setup in Claude Code.")
    if not cfg["locations"] and not cfg.get("remote") and a.command in ("run", "scan"):
        sys.exit("config.yaml has no `locations` and remote is off: nothing to search for.")
    if a.limit is not None:
        cfg["max_tailor_per_run"] = a.limit
    only = set(a.only.split(",")) if a.only else None
    if a.command in NEEDS_CLAUDE:
        ok, why = claude_check()
        if not ok:
            log.error("Stopping: %s", why)
            if a.command in ("run", "pending"):
                record_run(ws, a.command, False, error=why)
                refresh_dashboard(ws, cfg, store)
            if os.environ.get("APPLYAI_NOTIFY"):
                schedule.notify("ApplyAI run failed", why[:120])
            sys.exit(2)
        _stay_awake()

    if a.command in ("run", "scan"):
        sl = screen(ws, cfg, store, only)
        log.info("shortlist: %d postings with fit >= %s", len(sl), cfg.get("min_fit_score", 6))
        for j in sl:
            log.info("  %4.1f  %-22s %-30s %s", j.fit_score, j.city[:22], j.company[:30], j.title)
        n = tailor_jobs(ws, cfg, store, sl) if a.command == "run" and sl else 0
        if a.command == "run":
            log.info("done: %d new application folders under %s", n, ws.root)
            record_run(ws, "run", True, n)
            refresh_dashboard(ws, cfg, store)
            if os.environ.get("APPLYAI_NOTIFY"):
                schedule.notify("ApplyAI", f"{n} new applications ready" if n else "Run finished, nothing new")
        return

    if a.command == "pending":
        sl = shortlist(store)
        log.info("%d shortlisted postings waiting", len(sl))
        n = tailor_jobs(ws, cfg, store, sl)
        log.info("done: %d new application folders under %s", n, ws.root)
        record_run(ws, "pending", True, n)
        return

    if a.command == "tailor":
        if not a.target:
            sys.exit("usage: applyai tailor URL [--desc-file F --title T --company C --location L]")
        if a.desc_file:
            j = Job(title=a.title or "Position", company=a.company or "Company",
                    location=a.location or cfg["me"].get("city", ""), url=a.target, source="manual",
                    description=Path(a.desc_file).expanduser().read_text())
        else:
            j = job_from_url(a.target)
        for k in ("title", "company", "location"):
            if getattr(a, k):
                setattr(j, k, getattr(a, k))
        j.country, j.city = normalize_location(j.location, cfg["locations"], True) or \
            ("Other", j.location.split(",")[0].strip() or "Unknown")
        _score([j], cfg, Profile(ws.profile, cfg))
        log.info("fit %.0f/10: %s", j.fit_score or 0, j.fit_reason)
        cfg["max_tailor_per_run"] = 1
        tailor_jobs(ws, cfg, store, [j])
        return

    if a.command == "ratings":
        return refresh_ratings(ws, cfg, store, force=a.target == "refresh")
    if a.command == "rerender":
        return rerender(ws, cfg, store, Path(a.target).expanduser().resolve() if a.target else None)
    if a.command == "ats":
        folder = Path(a.target or ".").expanduser().resolve()
        j = Job.from_dict(json.loads((folder / "_job.json").read_text()))
        cv = next(folder.glob("CV_*.tex"))
        pdf = latex.compile_pdf(cv)
        me = cfg["me"]
        rep = ats.score(pdf, job_title=j.title, keywords=j.keywords, name=me["name"], email=me["email"],
                        phone_digits=re.sub(r"\D", "", me.get("phone_cv", "")))
        (folder / "ATS_Report.md").write_text(rep.markdown(j.title) + f"\nCV pages: {latex.pages(pdf)}\n")
        print(rep.markdown(j.title))
        print(f"CV pages: {latex.pages(pdf)}")


if __name__ == "__main__":
    main()
