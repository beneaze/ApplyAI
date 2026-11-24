"""Computer-use source: Claude drives a real Chromium browser (Playwright MCP) to scan sites
that block plain HTTP scraping or have no API — Indeed, Glassdoor, Welcome to the Jungle,
Wellfound, and company career pages (Workday, SuccessFactors, custom sites).

Each target in config.yaml `browser_targets` gets its own short Claude session that searches,
opens promising postings, and returns them as JSON.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..llm import LLMError, ask, extract_json
from ..log import log
from ..models import Job

PLAYWRIGHT_MCP = "@playwright/mcp@0.0.84"
# Read-and-navigate tools only: no file upload, no arbitrary JS / code execution.
SAFE_TOOLS = [f"mcp__playwright__browser_{t}" for t in (
    "navigate", "navigate_back", "snapshot", "click", "type", "press_key", "wait_for", "tabs",
    "select_option", "hover", "handle_dialog", "take_screenshot", "find", "close", "resize")]

PROMPT = """You are a job-search agent operating a real web browser through the Playwright tools.

Target: {name}
Start URL: {url}
What to do on this site: {instructions}

Look for job postings in these locations: {locations}.
Search terms to try (use the site's own search box / filters; a few terms are enough):
{queries}

Candidate profile (look for roles that fit): {profile}

Rules:
- Only read public pages. Never sign in, create an account, upload files, or submit an
  application. If a site requires login to see postings, stop and return what you have.
- Cookie / consent banners: choose "reject" / "only necessary" where offered.
- Don't solve CAPTCHAs. If one appears, stop and return what you have.
- Prefer browser_snapshot (accessibility tree) over screenshots to read pages.
- Open each promising posting and copy its full description text (responsibilities +
  requirements). Return at most {max_jobs} postings, newest/most relevant first, posted within
  the last {days} days if the site shows dates.

When finished, reply with ONLY a JSON array, no prose:
[{{"title": "...", "company": "...", "location": "city, country", "url": "direct link to the
posting", "posted": "YYYY-MM-DD or empty", "description": "full plain-text description"}}]
"""


def _chrome_user_agent() -> str | None:
    """A normal desktop Chrome user agent for the installed version. Headless Chrome otherwise
    announces itself as "HeadlessChrome", which some job sites answer with a block page."""
    import platform
    import plistlib
    major = None
    plist = Path("/Applications/Google Chrome.app/Contents/Info.plist")
    if plist.exists():
        try:
            major = plistlib.loads(plist.read_bytes()).get("CFBundleShortVersionString", "").split(".")[0]
        except Exception:
            major = None
    if not major:
        return None
    os_part = ("Macintosh; Intel Mac OS X 10_15_7" if platform.system() == "Darwin"
               else "X11; Linux x86_64" if platform.system() == "Linux" else "Windows NT 10.0; Win64; x64")
    return (f"Mozilla/5.0 ({os_part}) AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{major}.0.0.0 Safari/537.36")


def _mcp_config(headless: bool, browser: str) -> Path:
    # --isolated: fresh throw-away profile, never your own Chrome profile / logins
    args = ["-y", PLAYWRIGHT_MCP, "--isolated", "--browser", browser]
    if headless:
        # no window at all: nothing pops up or steals focus while the bot browses
        args += ["--headless", "--viewport-size", "1366,900"]
        ua = _chrome_user_agent()
        if ua:
            args += ["--user-agent", ua]
    cfg = {"mcpServers": {"playwright": {"command": "npx", "args": args}}}
    f = Path(tempfile.gettempdir()) / "applyai_playwright_mcp.json"
    f.write_text(json.dumps(cfg))
    return f


def search(targets, *, queries, metros, profile, days=14, headless=True, model="sonnet",
           max_jobs=8, budget_usd=1.5, browser="chrome"):
    mcp = _mcp_config(headless, browser)
    jobs = []
    for t in targets:
        prompt = PROMPT.format(
            name=t["name"], url=t["url"], instructions=t.get("instructions", "Search for jobs."),
            locations=", ".join(t.get("metros", metros)),
            queries="\n".join(f"- {q}" for q in (t.get("queries") or queries)[:6]),
            profile=profile, max_jobs=t.get("max_jobs", max_jobs), days=days)
        log.info("browser agent: scanning %s ...", t["name"])
        try:
            reply = ask(prompt, model=model, mcp_config=mcp, allowed_tools=SAFE_TOOLS,
                        timeout=1500, max_budget_usd=t.get("budget_usd", budget_usd))
            items = extract_json(reply)
        except (LLMError, json.JSONDecodeError) as e:
            log.warning("browser agent %s failed: %s", t["name"], str(e)[:300])
            continue
        if not items:
            log.warning("browser agent %s returned nothing; agent said: %s", t["name"], reply[-400:].replace("\n", " "))
        for it in items if isinstance(items, list) else []:
            if not it.get("title") or not it.get("url"):
                continue
            jobs.append(Job(title=it["title"], company=it.get("company") or t.get("company", t["name"]),
                            location=it.get("location", ""), url=it["url"],
                            source=f"browser:{t['name']}", posted=it.get("posted", ""),
                            description=it.get("description", "")))
        log.info("browser agent %s: %d postings", t["name"], len(items) if isinstance(items, list) else 0)
    return jobs
