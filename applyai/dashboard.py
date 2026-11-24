"""The workspace dashboard: one self-contained HTML file (`_Dashboard.html`).

It opens offline in any browser and links straight to each application's CV, cover letter,
Job.md and folder (relative links, so it keeps working if the workspace moves). It is rebuilt
after every run, tailor, mark and ratings update, or with `applyai dashboard`.

Everything shown comes from the workspace: the posting store (.applyai/state/seen.json), the
applied log (.applyai/state/applied.json) and each posting's description (for stated salary
ranges). The page has no external requests.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, urlparse

from . import salary as sal

PORTALS = [("greenhouse", "Greenhouse"), ("lever.co", "Lever"), ("ashbyhq", "Ashby"), ("personio", "Personio"),
           ("recruitee", "Recruitee"), ("workable", "Workable"), ("myworkdayjobs", "Workday"),
           ("successfactors", "SuccessFactors"), ("smartrecruiters", "SmartRecruiters"), ("linkedin", "LinkedIn"),
           ("ycombinator", "YC Work at a Startup"), ("indeed", "Indeed"), ("stepstone", "StepStone"),
           ("jobs.ch", "jobs.ch"), ("join.com", "JOIN"), ("softgarden", "softgarden"), ("bamboohr", "BambooHR"),
           ("teamtailor", "Teamtailor"), ("concludis", "Concludis"), ("taleo", "Taleo"), ("icims", "iCIMS")]


def portal_of(url: str) -> str:
    host = urlparse(url or "").netloc.lower()
    for needle, name in PORTALS:
        if needle in host or needle in (url or "").lower():
            return name
    return re.sub(r"^(www|jobs|careers|career|karriere)\.", "", host) or ""


def _iso(d: str) -> str:
    """'11.11.2026' or '2026-11-11...' -> '2026-11-11'."""
    if not d:
        return ""
    m = re.match(r"(\d{1,2})\.(\d{1,2})\.(\d{4})", d)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    return d[:10] if re.match(r"\d{4}-\d{2}-\d{2}", d) else ""


def _link(root: Path, p: Path | None) -> str:
    if not p:
        return ""
    try:
        rel = os.path.relpath(p, root)
    except ValueError:
        return Path(p).as_uri()
    return "/".join(quote(part) for part in Path(rel).parts)


def _rating(rec: dict | None, site: str) -> dict | None:
    r = (rec or {}).get(site)
    return {"score": r["score"], "reviews": r.get("reviews"), "url": r.get("url", "")} if r and r.get("score") else None


def normalize_status(s: str) -> str:
    s = (s or "").strip().lower()
    if s.startswith("needs") or "outage" in s or s == "blocked":
        return "blocked"
    return {"applied": "applied", "interview": "interview", "offer": "offer", "rejected": "rejected",
            "withdrawn": "withdrawn", "skipped": "skipped", "closed": "closed"}.get(s, s or "to apply")


def build_data(ws, store_data: dict, applied: dict, cfg: dict) -> dict:
    root = ws.root
    min_fit = cfg.get("min_fit_score", 6)
    postings, apps = [], []
    for key, r in store_data.items():
        job = r.get("job") or {}
        postings.append({"d": r.get("first_seen", ""), "fit": r.get("fit"), "st": r.get("status", ""),
                         "city": r.get("city", ""), "src": (job.get("source") or "").split(":")[0]})
        if r.get("status") not in ("tailored", "failed"):
            continue
        folder = Path(r["folder"]) if r.get("folder") else None
        a = applied.get(key, {})
        country = job.get("country", "")
        posted = sal.find(job.get("description", ""))
        ask = sal.parse_ask(a.get("salary", ""), sal.COUNTRY_CURRENCY.get(country, ""))
        url = r.get("url", "")
        apply_url = (job.get("extra") or {}).get("apply_url", "")
        docs = {}
        if folder and folder.exists():
            docs = {"cv": _link(root, next(folder.glob("CV_*.pdf"), None)),
                    "letter": _link(root, next(folder.glob("Cover_Letter_*.pdf"), None)),
                    "job": _link(root, folder / "Job.md" if (folder / "Job.md").exists() else None),
                    "folder": _link(root, folder)}
        status = "failed" if r.get("status") == "failed" else normalize_status(a.get("status", ""))
        apps.append({
            "key": key, "company": r.get("company", ""), "title": r.get("title", ""), "city": r.get("city", ""),
            "country": country, "fit": r.get("fit"), "ats": r.get("ats"), "status": status,
            "found": r.get("first_seen", ""), "posted": _iso(job.get("posted", "")),
            "sent": _iso(a.get("date", "")) if status not in ("to apply", "failed") else "",
            "via": a.get("via") or (portal_of(apply_url or url) if status not in ("to apply", "failed") else ""),
            "note": a.get("note", "") or r.get("error", ""), "url": url, "apply_url": apply_url,
            "salary": posted, "ask": ask, "ask_text": a.get("salary", ""),
            "kununu": _rating(r.get("rating"), "kununu"), "glassdoor": _rating(r.get("rating"), "glassdoor"),
            "rating_note": ((r.get("rating") or {}).get("summary") or "")[:200],
            "network": r.get("network") or [], "source": (job.get("source") or "").split(":")[0],
            "docs": docs,
        })
    return {"generated": dt.datetime.now().isoformat(timespec="minutes"), "name": cfg.get("me", {}).get("name", ""),
            "min_fit": min_fit, "postings": postings, "apps": apps, "runs": _runs(ws)}


def _runs(ws) -> list[dict]:
    """Run history: runs.json, plus older runs read from the log files (first line / last error)."""
    f = ws.state / "runs.json"
    try:
        runs = json.loads(f.read_text()) if f.exists() else []
    except ValueError:
        runs = []
    seen = {r["at"][:16] for r in runs}
    for log in sorted(ws.logs.glob("*_run.log")):
        m = re.match(r"(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})_run", log.name)
        if not m or f"{m.group(1)}T{m.group(2)}:{m.group(3)}" in seen:
            continue
        text = log.read_text(errors="replace")[-4000:]
        done = re.search(r"done: (\d+) new application folders", text)
        err = re.findall(r"ERROR\s+(.*)", text)
        runs.append({"at": f"{m.group(1)}T{m.group(2)}:{m.group(3)}", "command": "run", "ok": bool(done),
                     "new": int(done.group(1)) if done else 0,
                     "error": "" if done else (err[-1][:300] if err else "stopped before the end"), "scheduled": None})
    return sorted(runs, key=lambda r: r["at"])[-120:]


def write(ws, store_data: dict, applied: dict, cfg: dict) -> Path:
    data = build_data(ws, store_data, applied, cfg)
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out = ws.root / "_Dashboard.html"
    out.write_text(TEMPLATE.replace("/*__DATA__*/null", blob), encoding="utf-8")
    return out


TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ApplyAI Dashboard</title>
<style>
:root {
  color-scheme: light;
  --page: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink-2: #52514e; --muted: #898781;
  --grid: #e1e0d9; --axis: #c3c2b7; --ring: rgba(11,11,11,0.10); --wash: rgba(11,11,11,0.04);
  --s1: #2a78d6; --s2: #eb6834; --rest: #c3c2b7;
  --good: #0ca30c; --warning: #fab219; --serious: #ec835a; --critical: #d03b3b; --good-text: #006300;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
    --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10); --wash: rgba(255,255,255,0.05);
    --s1: #3987e5; --s2: #d95926; --rest: #4a4a46; --good-text: #0ca30c;
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --page: #0d0d0d; --surface: #1a1a19; --ink: #f0efec; --ink-2: #c3c2b7; --muted: #898781;
  --grid: #2c2c2a; --axis: #383835; --ring: rgba(255,255,255,0.10); --wash: rgba(255,255,255,0.05);
  --s1: #3987e5; --s2: #d95926; --rest: #4a4a46; --good-text: #0ca30c;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--page); color: var(--ink); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width: 1280px; margin: 0 auto; padding: 20px 16px 48px; }
header { display: flex; flex-wrap: wrap; align-items: baseline; gap: 4px 16px; margin-bottom: 14px; }
h1 { font-size: 20px; margin: 0; font-weight: 650; }
h2 { font-size: 13px; font-weight: 600; margin: 0 0 2px; }
.sub { color: var(--muted); font-size: 12px; }
.filters { display: flex; flex-wrap: wrap; gap: 8px; margin: 0 0 16px; align-items: center; }
.filters select, .filters input, .filters button { font: inherit; font-size: 13px; color: var(--ink); background: var(--surface);
  border: 1px solid var(--ring); border-radius: 8px; padding: 6px 10px; }
.filters input { min-width: 200px; }
.filters button { cursor: pointer; }
.seg { display: inline-flex; border: 1px solid var(--ring); border-radius: 8px; overflow: hidden; background: var(--surface); }
.seg button { border: 0; border-radius: 0; padding: 6px 10px; background: transparent; }
.seg button[aria-pressed="true"] { background: var(--wash); font-weight: 650; }
.card { background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 14px 16px; }
.kpis { display: grid; grid-template-columns: minmax(200px, 1.2fr) repeat(auto-fit, minmax(118px, 1fr)); gap: 10px; margin-bottom: 12px; }
.hero .v { font-size: 52px; font-weight: 650; line-height: 1.05; }
.tile .l, .hero .l { color: var(--ink-2); font-size: 12px; }
.tile .v { font-size: 24px; font-weight: 620; margin-top: 2px; }
.tile .d, .hero .d { color: var(--muted); font-size: 12px; margin-top: 2px; }
.runs { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 14px; margin-bottom: 12px; padding: 10px 16px; }
.runs .days { display: flex; gap: 3px; }
.runs .day { width: 14px; height: 14px; border-radius: 3px; background: var(--grid); }
.runs .day.ok { background: var(--good); }
.runs .day.fail { background: var(--critical); }
.runs .msg { font-size: 12px; color: var(--ink-2); }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 12px; margin-bottom: 12px; }
.chart svg { display: block; width: 100%; height: auto; overflow: visible; }
.legend { display: flex; gap: 14px; flex-wrap: wrap; color: var(--ink-2); font-size: 12px; margin: 4px 0 6px; }
.legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; vertical-align: -1px; }
.legend i.diamond { transform: rotate(45deg) scale(.8); border-radius: 1px; }
svg text { fill: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
svg .lbl { fill: var(--ink-2); }
svg .val { fill: var(--ink); font-weight: 600; }
.hit { fill: transparent; cursor: default; }
.hit:focus { outline: 2px solid var(--axis); outline-offset: -2px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th { text-align: left; font-weight: 600; color: var(--ink-2); font-size: 12px; padding: 8px 8px; border-bottom: 1px solid var(--axis);
  white-space: nowrap; position: sticky; top: 0; background: var(--surface); }
th[data-k] { cursor: pointer; user-select: none; }
th[data-k]:hover { color: var(--ink); }
th .arrow { color: var(--muted); }
td { padding: 6px 8px; border-bottom: 1px solid var(--grid); vertical-align: top; }
td.nw, td.num { white-space: nowrap; }
td.pos { min-width: 220px; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:hover { background: var(--wash); }
.small { font-size: 12px; color: var(--muted); }
.wrap { overflow-x: auto; }
a { color: inherit; text-decoration-color: var(--axis); text-underline-offset: 2px; }
a:hover { text-decoration-color: var(--ink); }
.docs a { margin-right: 8px; white-space: nowrap; }
.pill { display: inline-flex; align-items: center; gap: 6px; white-space: nowrap; font-size: 12px; }
.pill .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; background: var(--muted); }
.tag { display: inline-block; font-size: 11px; color: var(--ink-2); border: 1px solid var(--ring); border-radius: 6px; padding: 0 5px; margin-left: 4px; }
.warn { color: var(--critical); }
#tip { position: fixed; pointer-events: none; z-index: 10; background: var(--surface); color: var(--ink); border: 1px solid var(--ring);
  border-radius: 8px; padding: 8px 10px; font-size: 12px; box-shadow: 0 4px 16px rgba(0,0,0,.12); display: none; max-width: 280px; }
#tip .row { display: flex; gap: 8px; align-items: center; }
#tip .row b { font-weight: 650; }
#tip .key { width: 12px; height: 2px; display: inline-block; }
#tip .t { color: var(--ink-2); margin-bottom: 4px; }
.empty { color: var(--muted); padding: 24px 0; text-align: center; }
@media (max-width: 640px) {
  .kpis { grid-template-columns: repeat(2, 1fr); }
  .hero { grid-column: span 2; }
  .grid { grid-template-columns: 1fr; }
  .filters input { min-width: 0; flex: 1; }
}
</style>
</head>
<body>
<main>
  <header>
    <h1>ApplyAI Dashboard</h1>
    <span class="sub" id="gen"></span>
  </header>
  <div class="filters" role="toolbar" aria-label="Filters">
    <div class="seg" id="period" role="group" aria-label="Period">
      <button data-d="0" aria-pressed="true">All time</button>
      <button data-d="7">7 days</button>
      <button data-d="30">30 days</button>
      <button data-d="90">90 days</button>
    </div>
    <select id="city" aria-label="City"></select>
    <select id="status" aria-label="Status"></select>
    <input id="q" type="search" placeholder="Search company or position" aria-label="Search">
  </div>
  <section class="kpis" id="kpis"></section>
  <section class="card runs" id="runs"></section>
  <section class="grid">
    <div class="card chart"><h2>Postings per week</h2><div class="sub">Screened by Claude, split by fit</div><div id="c-weekly"></div></div>
    <div class="card chart"><h2>Applications sent per week</h2><div class="sub">By date sent</div><div id="c-sent"></div></div>
  </section>
  <section class="grid">
    <div class="card chart"><h2>Pipeline</h2><div class="sub">From screened posting to offer</div><div id="c-funnel"></div></div>
    <div class="card chart"><h2>Salary ranges in postings</h2><div class="sub">Yearly, as stated in the posting · only postings that state one</div><div id="c-salary"></div></div>
  </section>
  <section class="grid">
    <div class="card chart"><h2>Employer ratings</h2><div class="sub">kununu score of tailored applications (Glassdoor where kununu has none)</div><div id="c-rating"></div></div>
    <div class="card"><h2>By city</h2><div class="sub">Counts in the selected period</div><div class="wrap" id="t-city"></div></div>
  </section>
  <section class="card">
    <h2>Applications</h2>
    <div class="sub" id="t-count"></div>
    <div class="wrap"><table id="apps"></table></div>
  </section>
</main>
<div id="tip" role="tooltip"></div>
<script>
const DATA = /*__DATA__*/null;
const $ = s => document.querySelector(s);
const NS = "http://www.w3.org/2000/svg";
function el(tag, attrs = {}, kids = []) {
  const n = tag.startsWith("svg:") ? document.createElementNS(NS, tag.slice(4)) : document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "text") n.textContent = v; else if (k.startsWith("on")) n.addEventListener(k.slice(2), v); else n.setAttribute(k, v);
  }
  for (const c of [].concat(kids)) if (c !== null && c !== undefined) n.append(c.nodeType ? c : document.createTextNode(String(c)));
  return n;
}
const short = (t, n = 18) => t.length > n ? t.slice(0, n - 1) + "…" : t;
const shortDate = s => day(s).toLocaleDateString("en-US", { month: "short", day: "numeric" });
const plural = (n, one, many) => `${fmtInt(n)} ${n === 1 ? one : many}`;
const fmtInt = n => (n ?? 0).toLocaleString("en-US");
const fmtK = n => n >= 1e6 ? (n / 1e6).toFixed(1) + "M" : n >= 1e3 ? Math.round(n / 1e3) + "K" : String(n);
const median = a => { const s = a.filter(x => x != null).sort((x, y) => x - y); if (!s.length) return null; const m = s.length >> 1; return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2; };
const day = s => s ? new Date(s + "T00:00:00") : null;
const ymd = d => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
function weekStart(s) { const d = day(s); if (!d) return ""; d.setDate(d.getDate() - (d.getDay() + 6) % 7); return ymd(d); }
const SYM = { USD: "$", EUR: "€", GBP: "£", CHF: "CHF " };
const money = (v, c) => (SYM[c] || c + " ") + fmtK(v);

const STATUS = {
  "to apply": { label: "To apply", color: "var(--warning)", icon: "○" },
  applied: { label: "Applied", color: "var(--s1)", icon: "→" },
  interview: { label: "Interview", color: "var(--good)", icon: "●" },
  offer: { label: "Offer", color: "var(--good)", icon: "★" },
  rejected: { label: "Rejected", color: "var(--critical)", icon: "✕" },
  blocked: { label: "Needs you", color: "var(--serious)", icon: "!" },
  failed: { label: "Failed", color: "var(--critical)", icon: "✕" },
  skipped: { label: "Skipped", color: "var(--muted)", icon: "–" },
  withdrawn: { label: "Withdrawn", color: "var(--muted)", icon: "–" },
  closed: { label: "Closed", color: "var(--muted)", icon: "–" },
};
const SENT = new Set(["applied", "interview", "offer", "rejected", "withdrawn"]);

// ---------- tooltip
const tip = $("#tip");
function showTip(ev, title, rows) {
  tip.replaceChildren(el("div", { class: "t", text: title }),
    ...rows.map(([key, value, label]) => el("div", { class: "row" }, [key ? el("span", { class: "key", style: `background:${key}` }) : null, el("b", { text: value }), el("span", { text: label })])));
  tip.style.display = "block";
  const r = tip.getBoundingClientRect();
  let x = ev.clientX + 14, y = ev.clientY + 14;
  if (x + r.width > innerWidth - 8) x = ev.clientX - r.width - 14;
  if (y + r.height > innerHeight - 8) y = ev.clientY - r.height - 14;
  tip.style.left = x + "px"; tip.style.top = y + "px";
}
const hideTip = () => { tip.style.display = "none"; };
function hover(node, fn) {
  node.setAttribute("tabindex", "0");
  node.addEventListener("pointermove", e => fn(e));
  node.addEventListener("focus", () => { const b = node.getBoundingClientRect(); fn({ clientX: b.right, clientY: b.top }); });
  node.addEventListener("pointerleave", hideTip); node.addEventListener("blur", hideTip);
}

// ---------- filters
const state = { days: 0, city: "", status: "", q: "", sort: "found", dir: -1 };
const cities = [...new Set(DATA.apps.map(a => a.city).concat(DATA.postings.map(p => p.city)).filter(Boolean))].sort();
$("#city").append(el("option", { value: "", text: "All cities" }), ...cities.map(c => el("option", { value: c, text: c })));
$("#status").append(el("option", { value: "", text: "All statuses" }),
  ...Object.entries(STATUS).filter(([k]) => DATA.apps.some(a => a.status === k)).map(([k, s]) => el("option", { value: k, text: s.label })));
document.querySelectorAll("#period button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll("#period button").forEach(x => x.setAttribute("aria-pressed", x === b));
  state.days = +b.dataset.d; render();
}));
$("#city").addEventListener("change", e => { state.city = e.target.value; render(); });
$("#status").addEventListener("change", e => { state.status = e.target.value; render(); });
$("#q").addEventListener("input", e => { state.q = e.target.value.toLowerCase(); render(); });

function inPeriod(d) {
  if (!state.days) return true;
  if (!d) return false;
  return (Date.now() - day(d).getTime()) / 864e5 <= state.days;
}
function slice() {
  const posts = DATA.postings.filter(p => (!state.city || p.city === state.city) && inPeriod(p.d));
  const appsAll = DATA.apps.filter(a => (!state.city || a.city === state.city) && inPeriod(a.sent || a.found));
  const apps = appsAll.filter(a => (!state.status || a.status === state.status) &&
    (!state.q || (a.company + " " + a.title).toLowerCase().includes(state.q)));
  return { posts, appsAll, apps };
}

// ---------- KPI tiles
function tiles({ posts, appsAll }) {
  const sent = appsAll.filter(a => SENT.has(a.status));
  const replied = sent.filter(a => ["interview", "offer", "rejected"].includes(a.status));
  const good = posts.filter(p => p.fit != null && p.fit >= DATA.min_fit);
  const waiting = appsAll.filter(a => a.status === "to apply").length;
  const needs = appsAll.filter(a => a.status === "blocked").length;
  const ratings = appsAll.map(a => (a.kununu || a.glassdoor || {}).score).filter(Boolean);
  const pct = (a, b) => b ? Math.round(100 * a / b) + "%" : "–";
  const t = (l, v, d) => el("div", { class: "card tile" }, [el("div", { class: "l", text: l }), el("div", { class: "v", text: v }), d ? el("div", { class: "d", text: d }) : null]);
  $("#kpis").replaceChildren(
    el("div", { class: "card hero" }, [el("div", { class: "l", text: "Applications sent" }), el("div", { class: "v", text: fmtInt(sent.length) }),
      el("div", { class: "d", text: `${waiting} ready to send · ${needs} need you` })]),
    t("Postings screened", fmtInt(posts.length), `${fmtInt(good.length)} good fits (${pct(good.length, posts.length)})`),
    t("Tailored", fmtInt(appsAll.filter(a => a.status !== "failed").length), "CV + letter, one page"),
    t("Interviews", fmtInt(sent.filter(a => a.status === "interview" || a.status === "offer").length), plural(sent.filter(a => a.status === "offer").length, "offer", "offers")),
    t("Response rate", pct(replied.length, sent.length), `${plural(replied.length, "reply", "replies")} to ${sent.length}`),
    t("Median fit · ATS", `${median(appsAll.map(a => a.fit))?.toFixed(0) ?? "–"} · ${median(appsAll.map(a => a.ats))?.toFixed(0) ?? "–"}`, "fit /10 · ATS /100"),
    t("Median rating", ratings.length ? median(ratings).toFixed(1) : "–", `${plural(ratings.length, "employer", "employers")} rated`),
  );
}

// ---------- column chart (stacked)
function columns(target, rows, series, opts = {}) {
  const box = $(target); box.replaceChildren();
  if (!rows.length) { box.append(el("div", { class: "empty", text: "No data in this period" })); return; }
  if (series.length > 1) box.append(el("div", { class: "legend" }, series.map(s => el("span", {}, [el("i", { style: `background:${s.color}` }), s.label]))));
  const W = 560, H = 190, L = 34, R = 8, T = 12, B = 22;
  const max = Math.max(1, ...rows.map(r => series.reduce((a, s) => a + (r[s.key] || 0), 0)));
  const step = niceStep(max), top = Math.ceil(max / step) * step;
  const y = v => T + (H - T - B) * (1 - v / top);
  const bw = Math.min(24, (W - L - R) / rows.length * 0.66);
  const svg = el("svg:svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "" });
  for (let v = 0; v <= top; v += step) {
    svg.append(el("svg:line", { x1: L, x2: W - R, y1: y(v), y2: y(v), stroke: v ? "var(--grid)" : "var(--axis)", "stroke-width": 1 }));
    svg.append(el("svg:text", { x: L - 6, y: y(v) + 4, "text-anchor": "end", text: fmtK(v) }));
  }
  const slot = (W - L - R) / rows.length;
  const every = Math.ceil(rows.length / 8);
  rows.forEach((r, i) => {
    const cx = L + slot * (i + 0.5);
    let acc = 0;
    const g = el("svg:g");
    series.forEach((s, si) => {
      const v = r[s.key] || 0; if (!v) return;
      const yb = y(acc) - (acc > 0 ? 2 : 0);  // 2px surface gap between stacked segments
      acc += v;
      const yt = y(acc);
      const isTop = !series.slice(si + 1).some(x => r[x.key]);
      g.append(el("svg:path", { class: "mark", fill: s.color, d: barPath(cx - bw / 2, yt, bw, Math.max(0, yb - yt), isTop ? 4 : 0) }));
    });
    const hit = el("svg:rect", { class: "hit", x: cx - slot / 2, y: T, width: slot, height: H - T - B });
    hover(hit, e => showTip(e, opts.title(r), series.map(s => [s.color, fmtInt(r[s.key] || 0), s.label])));
    svg.append(g, hit);
    if (i % every === 0) svg.append(el("svg:text", { x: cx, y: H - 6, "text-anchor": "middle", text: opts.tick(r) }));
  });
  box.append(svg);
}
function barPath(x, y, w, h, r) {
  if (h <= 0) return "";
  r = Math.min(r, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}
function niceStep(max) { const raw = max / 4, p = 10 ** Math.floor(Math.log10(raw)), n = raw / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }

function weeksBetween(dates) {
  const ws = dates.filter(Boolean).map(weekStart).sort();
  if (!ws.length) return [];
  const out = []; const d = day(ws[0]); const end = day(weekStart(ymd(new Date())));
  while (d <= end) { out.push(ymd(d)); d.setDate(d.getDate() + 7); }
  return out;
}
const wkLabel = w => day(w).toLocaleDateString("en-US", { month: "short", day: "numeric" });

function weekly({ posts, appsAll }) {
  const weeks = weeksBetween(posts.map(p => p.d));
  const by = Object.fromEntries(weeks.map(w => [w, { w, good: 0, rest: 0 }]));
  posts.forEach(p => { const r = by[weekStart(p.d)]; if (r) (p.fit != null && p.fit >= DATA.min_fit ? r.good++ : r.rest++); });
  columns("#c-weekly", weeks.map(w => by[w]), [
    { key: "good", label: `Good fit (≥ ${DATA.min_fit}/10)`, color: "var(--s1)" },
    { key: "rest", label: "Screened out", color: "var(--rest)" }],
    { title: r => "Week of " + wkLabel(r.w), tick: r => wkLabel(r.w), aria: "Postings screened per week" });
  const sent = appsAll.filter(a => SENT.has(a.status) && a.sent);
  const sw = weeksBetween(sent.map(a => a.sent));
  const sb = Object.fromEntries(sw.map(w => [w, { w, n: 0 }]));
  sent.forEach(a => { const r = sb[weekStart(a.sent)]; if (r) r.n++; });
  columns("#c-sent", sw.map(w => sb[w]), [{ key: "n", label: "Applications sent", color: "var(--s1)" }],
    { title: r => "Week of " + wkLabel(r.w), tick: r => wkLabel(r.w), aria: "Applications sent per week" });
}

// ---------- horizontal bars
function hbars(target, rows, opts = {}) {
  const box = $(target); box.replaceChildren();
  if (!rows.some(r => r.v)) { box.append(el("div", { class: "empty", text: "No data in this period" })); return; }
  const W = 560, rowH = 26, L = 110, R = 60, H = rows.length * rowH + 4;
  const max = Math.max(1, ...rows.map(r => r.v));
  const svg = el("svg:svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opts.aria || "" });
  rows.forEach((r, i) => {
    const yc = i * rowH + rowH / 2 + 2, w = (W - L - R) * r.v / max;
    svg.append(el("svg:text", { class: "lbl", x: L - 8, y: yc + 4, "text-anchor": "end", text: r.label }));
    const mark = el("svg:path", { class: "mark", fill: r.color || "var(--s1)", d: hbarPath(L, yc - 6, Math.max(w, r.v ? 2 : 0), 12, 4) });
    const hit = el("svg:rect", { class: "hit", x: 0, y: yc - rowH / 2, width: W, height: rowH });
    hover(hit, e => showTip(e, r.label, [[r.color || "var(--s1)", fmtInt(r.v), r.note || ""]]));
    svg.append(mark, el("svg:text", { class: "val", x: L + w + 6, y: yc + 4, text: fmtInt(r.v) + (r.suffix || "") }), hit);
  });
  box.append(svg);
}
function hbarPath(x, y, w, h, r) {
  if (w <= 0) return ""; r = Math.min(r, w, h / 2);
  return `M${x},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h - r}Q${x + w},${y + h} ${x + w - r},${y + h}H${x}Z`;
}

function funnel({ posts, appsAll }) {
  const sent = appsAll.filter(a => SENT.has(a.status));
  const pct = (a, b) => b ? ` · ${Math.round(100 * a / b)}%` : "";
  const good = posts.filter(p => p.fit != null && p.fit >= DATA.min_fit).length;
  const tail = appsAll.filter(a => a.status !== "failed").length;
  const replied = sent.filter(a => ["interview", "offer", "rejected"].includes(a.status)).length;
  const inter = sent.filter(a => ["interview", "offer"].includes(a.status)).length;
  const offer = sent.filter(a => a.status === "offer").length;
  hbars("#c-funnel", [
    { label: "Screened", v: posts.length },
    { label: "Good fit", v: good, note: "of screened" + pct(good, posts.length) },
    { label: "Tailored", v: tail },
    { label: "Sent", v: sent.length, note: "of tailored" + pct(sent.length, tail) },
    { label: "Got a reply", v: replied, note: "of sent" + pct(replied, sent.length) },
    { label: "Interview", v: inter, note: "of sent" + pct(inter, sent.length) },
    { label: "Offer", v: offer },
  ], { aria: "Pipeline from screened posting to offer" });
}

// ---------- salary ranges (one shared axis per currency)
function salaries({ appsAll }) {
  const box = $("#c-salary"); box.replaceChildren();
  const withS = appsAll.filter(a => a.salary);
  if (!withS.length) { box.append(el("div", { class: "empty", text: "No posting in this period states a salary" })); return; }
  box.append(el("div", { class: "legend" }, [el("span", {}, [el("i", { style: "background:var(--s1)" }), "Posted range (each line one posting)"]),
    el("span", {}, [el("i", { class: "diamond", style: "background:var(--s2)" }), "Your ask"])]));
  const byCur = {};
  withS.forEach(a => (byCur[a.salary.currency] ||= []).push(a));
  for (const [cur, list] of Object.entries(byCur)) {
    const groups = {}; list.forEach(a => (groups[a.city || a.country] ||= []).push(a));
    const asks = appsAll.filter(a => a.ask && a.ask.currency === cur);
    const lo = Math.min(...list.map(a => a.salary.min), ...asks.map(a => a.ask.min)), hi = Math.max(...list.map(a => a.salary.max), ...asks.map(a => a.ask.max));
    const step = niceStep(hi - lo || hi), a0 = Math.floor(lo / step) * step, a1 = Math.ceil(hi / step) * step;
    const W = 560, L = 150, R = 16, rowH = 34, names = Object.keys(groups), H = names.length * rowH + 24;
    const x = v => L + (W - L - R) * (v - a0) / (a1 - a0 || 1);
    const svg = el("svg:svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `Salary ranges in ${cur}` });
    for (let v = a0; v <= a1 + 1; v += step) {
      svg.append(el("svg:line", { x1: x(v), x2: x(v), y1: 0, y2: H - 18, stroke: "var(--grid)", "stroke-width": 1 }));
      svg.append(el("svg:text", { x: x(v), y: H - 4, "text-anchor": "middle", text: money(v, cur) }));
    }
    names.forEach((n, i) => {
      const g = groups[n], yc = i * rowH + rowH / 2;
      svg.append(el("svg:text", { class: "lbl", x: L - 8, y: yc + 4, "text-anchor": "end", text: `${short(n)} (${g.length})` }, [el("svg:title", { text: n })]));
      g.forEach((a, k) => {
        const yy = yc - 8 + (g.length > 1 ? 16 * k / (g.length - 1) : 8);
        const line = el("svg:line", { class: "mark", x1: x(a.salary.min), x2: Math.max(x(a.salary.max), x(a.salary.min) + 3), y1: yy, y2: yy, stroke: "var(--s1)", "stroke-width": 2, "stroke-linecap": "round", opacity: 0.75 });
        const hit = el("svg:rect", { class: "hit", x: x(a.salary.min) - 6, y: yy - 6, width: Math.max(12, x(a.salary.max) - x(a.salary.min) + 12), height: 12 });
        hover(hit, e => showTip(e, `${a.company} · ${a.title}`, [["var(--s1)", `${money(a.salary.min, cur)}–${money(a.salary.max, cur)}`, `posted: “${a.salary.text}”`]]
          .concat(a.ask ? [["var(--s2)", money(a.ask.min, cur), "your ask"]] : [])));
        svg.append(line, hit);
      });
      const ask = median(asks.filter(a => (a.city || a.country) === n).map(a => a.ask.min));
      if (ask) {
        const d = el("svg:rect", { x: x(ask) - 5, y: yc - 5, width: 10, height: 10, fill: "var(--s2)", stroke: "var(--surface)", "stroke-width": 2, transform: `rotate(45 ${x(ask)} ${yc})` });
        const hit = el("svg:rect", { class: "hit", x: x(ask) - 12, y: yc - 12, width: 24, height: 24 });
        hover(hit, e => showTip(e, n, [["var(--s2)", money(ask, cur), "your ask (median)"]]));
        svg.append(d, hit);
      }
    });
    box.append(el("div", { class: "small", text: cur }), svg);
  }
}

function ratingChart({ appsAll }) {
  const bins = [["< 3.0", 0, 3], ["3.0–3.4", 3, 3.5], ["3.5–3.9", 3.5, 4], ["4.0–4.4", 4, 4.5], ["4.5–5.0", 4.5, 5.01]];
  const rated = appsAll.map(a => ({ a, s: (a.kununu || a.glassdoor || {}).score })).filter(r => r.s);
  hbars("#c-rating", bins.map(([label, lo, hi]) => {
    const hit = rated.filter(r => r.s >= lo && r.s < hi);
    return { label, v: hit.length, note: hit.slice(0, 4).map(r => r.a.company).join(", ") + (hit.length > 4 ? " …" : "") };
  }).concat([{ label: "Not rated", v: appsAll.length - rated.length, color: "var(--rest)" }]), { aria: "Employer rating distribution" });
}

function cityTable({ posts, appsAll }) {
  const rows = {};
  const r = c => (rows[c] ||= { c, screened: 0, good: 0, tailored: 0, sent: 0, inter: 0, sal: [] });
  posts.forEach(p => { const x = r(p.city || "–"); x.screened++; if (p.fit != null && p.fit >= DATA.min_fit) x.good++; });
  appsAll.forEach(a => { const x = r(a.city || "–"); if (a.status !== "failed") x.tailored++; if (SENT.has(a.status)) x.sent++;
    if (["interview", "offer"].includes(a.status)) x.inter++; if (a.salary) x.sal.push(a.salary); });
  const list = Object.values(rows).sort((a, b) => b.tailored - a.tailored || b.screened - a.screened);
  const t = el("table", {}, [el("thead", {}, el("tr", {}, ["City", "Screened", "Good fit", "Tailored", "Sent", "Interviews", "Median salary"].map((h, i) => el("th", { class: i ? "num" : "", text: h })))),
    el("tbody", {}, list.map(x => {
      const cur = x.sal[0]?.currency; const mid = median(x.sal.filter(s => s.currency === cur).map(s => (s.min + s.max) / 2));
      return el("tr", {}, [el("td", { class: "nw", text: x.c.replace("San Francisco Bay Area", "SF Bay Area") }), ...[x.screened, x.good, x.tailored, x.sent, x.inter].map(v => el("td", { class: "num", text: fmtInt(v) })),
        el("td", { class: "num", text: mid ? `${money(mid, cur)} (${x.sal.length})` : "–" })]);
    }))]);
  $("#t-city").replaceChildren(t);
}

// ---------- applications table
const COLS = [
  ["status", "Status"], ["sent", "Sent"], ["via", "Where"], ["company", "Company"], ["title", "Position"], ["city", "City"],
  ["fit", "Fit", 1], ["ats", "ATS", 1], ["rating", "Rating", 1], ["salary", "Posted salary", 1], ["ask", "Your ask", 1], [null, "Documents"]];
function sortVal(a, k) {
  if (k === "rating") return (a.kununu || a.glassdoor || {}).score ?? -1;
  if (k === "salary") return a.salary ? (a.salary.min + a.salary.max) / 2 : -1;
  if (k === "ask") return a.ask ? a.ask.min : -1;
  if (k === "sent") return a.sent || a.found || "";
  return a[k] ?? "";
}
function appsTable({ apps }) {
  const sorted = [...apps].sort((a, b) => { const x = sortVal(a, state.sort), y = sortVal(b, state.sort); return (x > y ? 1 : x < y ? -1 : 0) * state.dir; });
  $("#t-count").textContent = `${apps.length} applications · click a column to sort · links open the files in this folder`;
  const head = el("tr", {}, COLS.map(([k, h, num]) => {
    const th = el("th", { class: num ? "num" : "", "data-k": k || null, scope: "col" }, [h, k === state.sort ? el("span", { class: "arrow", text: state.dir > 0 ? " ↑" : " ↓" }) : null]);
    if (k) th.addEventListener("click", () => { state.dir = state.sort === k ? -state.dir : (num ? -1 : 1); state.sort = k; render(); });
    return th;
  }));
  const body = sorted.map(a => {
    const s = STATUS[a.status] || STATUS["to apply"];
    const rating = a.kununu ? ["kununu", a.kununu] : a.glassdoor ? ["Glassdoor", a.glassdoor] : null;
    const docs = el("td", { class: "docs" }, [
      a.docs.cv ? el("a", { href: a.docs.cv, text: "CV" }) : null, a.docs.letter ? el("a", { href: a.docs.letter, text: "Letter" }) : null,
      a.docs.job ? el("a", { href: a.docs.job, text: "Job.md" }) : null, a.docs.folder ? el("a", { href: a.docs.folder, text: "Folder" }) : null]);
    return el("tr", {}, [
      el("td", {}, el("span", { class: "pill", title: a.note || null }, [el("span", { class: "dot", style: `background:${s.color}` }), `${s.icon} ${s.label}`])),
      el("td", { class: "small nw", title: a.sent ? "sent " + a.sent : a.found ? "found " + a.found : null, text: a.sent ? shortDate(a.sent) : a.found ? "found " + shortDate(a.found) : "" }),
      el("td", { class: "small nw", text: a.via || "" }),
      el("td", {}, [a.company, ...a.network.map(n => el("span", { class: "tag", text: n.replace(/YC (Winter|Summer|Spring|Fall) \d\d(\d\d)/, (m, s, y) => `YC ${s[0]}${y}`) }))]),
      el("td", { class: "pos" }, el("a", { href: a.apply_url || a.url, target: "_blank", rel: "noopener", text: a.title })),
      el("td", { class: "small nw", text: a.city.replace("San Francisco Bay Area", "SF Bay Area") }),
      el("td", { class: "num", text: a.fit != null ? a.fit.toFixed(0) : "–" }),
      el("td", { class: "num", text: a.ats ?? "–" }),
      el("td", { class: "num" }, rating ? el("a", { href: rating[1].url, target: "_blank", rel: "noopener",
        title: `${rating[0]}${rating[1].reviews ? `, ${rating[1].reviews} reviews` : ""}${a.rating_note ? " · " + a.rating_note : ""}`,
        class: rating[1].score < 3.5 ? "warn" : null, text: (rating[1].score < 3.5 ? "⚠ " : "") + rating[1].score.toFixed(1) + (rating[0] === "Glassdoor" ? " GD" : "") }) : "–"),
      el("td", { class: "num small", title: a.salary?.text || null, text: a.salary ? `${money(a.salary.min, a.salary.currency)}–${fmtK(a.salary.max)}` : "–" }),
      el("td", { class: "num small", text: a.ask ? money(a.ask.min, a.ask.currency) : (a.ask_text || "–") }),
      docs]);
  });
  $("#apps").replaceChildren(el("thead", {}, head), el("tbody", {}, body.length ? body : el("tr", {}, el("td", { colspan: COLS.length, class: "empty", text: "No applications match the filters" }))));
}

// ---------- run health: one square per day, last 28 days
function runStrip() {
  const box = $("#runs"); const runs = DATA.runs || [];
  if (!runs.length) { box.replaceChildren(el("span", { class: "msg", text: "No runs recorded yet." })); return; }
  const byDay = {};
  runs.forEach(r => { const d = r.at.slice(0, 10); (byDay[d] ||= []).push(r); });
  const days = []; const t = new Date();
  for (let i = 27; i >= 0; i--) { const d = new Date(t); d.setDate(t.getDate() - i); days.push(ymd(d)); }
  const strip = el("div", { class: "days", role: "img", "aria-label": "Runs in the last 28 days" }, days.map(d => {
    const rs = byDay[d] || []; const ok = rs.some(r => r.ok); const cls = rs.length ? (ok ? "day ok" : "day fail") : "day";
    const sq = el("span", { class: cls });
    hover(sq, e => showTip(e, day(d).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" }),
      rs.length ? rs.map(r => [null, r.ok ? `✓ ${r.new} new` : "✕ failed", r.ok ? r.at.slice(11) : (r.error || "").slice(0, 90)]) : [[null, "–", "no run"]]));
    return sq;
  }));
  const last = runs[runs.length - 1], lastOk = [...runs].reverse().find(r => r.ok);
  const fails = days.filter(d => (byDay[d] || []).length && !(byDay[d] || []).some(r => r.ok)).length;
  const msg = last.ok ? `Last run ${last.at.replace("T", " ")}: ✓ ${plural(last.new, "new application", "new applications")}`
    : `✕ Last run ${last.at.replace("T", " ")} failed: ${last.error}${lastOk ? ` · last good run ${lastOk.at.slice(0, 10)}` : ""}`;
  box.replaceChildren(el("h2", { text: "Runs" }), strip,
    el("span", { class: "legend", style: "margin:0" }, [el("span", {}, [el("i", { style: "background:var(--good)" }), "✓ ran"]), el("span", {}, [el("i", { style: "background:var(--critical)" }), "✕ failed"]), el("span", {}, [el("i", { style: "background:var(--grid)" }), "no run"])]),
    el("span", { class: "msg" + (last.ok ? "" : " warn"), text: msg + (fails ? ` · ${fails} failed days in the last 4 weeks` : "") }));
}

function render() {
  const s = slice();
  tiles(s); weekly(s); funnel(s); salaries(s); ratingChart(s); cityTable(s); appsTable(s);
}
runStrip();
$("#gen").textContent = `${DATA.name ? DATA.name + " · " : ""}updated ${DATA.generated.replace("T", " ")}`;
render();
</script>
</body>
</html>
"""
