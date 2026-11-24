"""Build a dashboard from made-up data, for screenshots and for trying the page without a
workspace. Every name, company and number here is invented.

    python docs/demo_dashboard.py [OUT_DIR]      -> OUT_DIR/_Dashboard.html (default: a temp folder)
"""
from __future__ import annotations

import datetime as dt
import json
import random
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from applyai import dashboard  # noqa: E402

random.seed(7)
TODAY = dt.date.today()
CITIES = {"Berlin": "Germany", "Munich": "Germany", "Zurich": "Switzerland"}
COMPANIES = ["Acme Robotics", "Example Motion GmbH", "Demo Dynamics", "Sample Sensors AG", "Placeholder Labs",
             "Fictive Automation", "Mock Mechatronics", "Specimen Drives", "Prototype Systems", "Dummy Devices AG",
             "Imaginary Instruments", "Model Machines", "Sandbox Robotics", "Template Technik", "Draft Dynamics GmbH",
             "Pilot Photonics", "Trial Test Systems", "Mockup Motors", "Stand-in Sensorik", "Proxy Precision"]
TITLES = ["Robotics Test Engineer", "Validation Engineer, Robot Arms", "Mechatronics Engineer", "HIL Test Engineer",
          "Systems Integration Engineer", "Calibration Engineer", "Test Automation Engineer (Python)",
          "Robotics Hardware Engineer", "Sensor Validation Engineer", "End-of-Line Test Engineer"]
VIA = ["Company site", "Greenhouse", "Personio", "Lever", "LinkedIn", "Ashby"]
SALARY = {"Germany": ("€", 58, 78), "Switzerland": ("CHF ", 92, 125)}


def day(n: int) -> str:
    return (TODAY - dt.timedelta(days=n)).isoformat()


def build(out: Path) -> Path:
    store, applied = {}, {}
    # screened postings over the last 6 weeks, more in the first (initial) scan
    for i in range(640):
        age = int(random.triangular(0, 42, 38)) if i < 380 else random.randint(0, 41)
        fit = max(0, min(10, round(random.gauss(4.2, 1.8))))
        city = random.choices(list(CITIES), weights=[5, 4, 2])[0]
        store[f"p{i}"] = {"status": "rejected" if fit < 6 else "shortlisted", "first_seen": day(age), "fit": fit,
                          "city": city, "title": "x", "company": "x", "job": {"source": "linkedin"}}
    # tailored applications
    outcomes = ["applied"] * 22 + ["interview"] * 4 + ["offer"] * 1 + ["rejected"] * 7 + ["blocked"] * 2 + ["skipped"]
    n_apps = len(outcomes) + 6  # 6 still to apply
    for i in range(n_apps):
        company, title = COMPANIES[i % len(COMPANIES)], TITLES[(i * 3) % len(TITLES)]
        city = list(CITIES)[i % 3] if i % 5 else "Munich"
        country = CITIES[city]
        found = random.randint(0, 40)
        folder = out / country / city / company / title
        folder.mkdir(parents=True, exist_ok=True)
        stem = company.replace(" ", "_")
        for f in (f"CV_Alex_Morgan_{stem}.pdf", f"Cover_Letter_Alex_Morgan_{stem}.pdf", "Job.md"):
            (folder / f).write_text("demo")
        desc = ""
        if random.random() < 0.45:
            sym, lo, hi = SALARY[country]
            a = random.randint(lo, hi - 8)
            desc = f"Salary range: {sym}{a},000 - {sym}{a + random.randint(6, 18)},000 per year"
            if sym.strip() == "CHF":
                desc = f"Salary range: CHF {a}'000 - {a + random.randint(6, 18)}'000"
        kun = round(random.uniform(3.1, 4.8), 1) if random.random() < 0.85 else None
        key = f"a{i}"
        store[key] = {
            "status": "tailored", "first_seen": day(found), "fit": random.choice([6, 7, 7, 8, 8, 9]),
            "ats": random.randint(68, 92), "title": title, "company": company, "city": city,
            "url": f"https://example.com/jobs/{i}", "folder": str(folder),
            "network": ["YC W25"] if i % 11 == 3 else [],
            "rating": {"kununu": {"score": kun, "reviews": random.randint(12, 240), "url": "https://example.com/rating"} if kun else None,
                       "glassdoor": None, "summary": "Demo data"},
            "job": {"country": country, "description": desc, "source": random.choice(["greenhouse", "personio", "linkedin", "lever"])},
        }
        if i < len(outcomes):
            sent = max(0, found - random.randint(0, 3))
            applied[key] = {"status": outcomes[i], "date": day(sent), "via": random.choice(VIA),
                            "salary": ("EUR 68k" if country == "Germany" else "CHF 110000") if i % 2 == 0 else ""}
    ws = SimpleNamespace(root=out, state=out / ".applyai" / "state", logs=out / ".applyai" / "logs")
    ws.state.mkdir(parents=True, exist_ok=True)
    ws.logs.mkdir(parents=True, exist_ok=True)
    runs = []
    for n in range(27, -1, -1):
        if n in (19, 20):
            runs.append({"at": day(n) + "T07:30", "command": "run", "ok": False, "new": 0,
                         "error": "Claude login expired or revoked: run `claude setup-token`, then `applyai save-token`"})
        elif n != 12:
            runs.append({"at": day(n) + "T07:30", "command": "run", "ok": True, "new": random.randint(0, 3), "error": ""})
    (ws.state / "runs.json").write_text(json.dumps(runs))
    return dashboard.write(ws, store, applied, {"me": {"name": "Alex Morgan (demo data)"}, "min_fit_score": 6})


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="applyai-demo-"))
    print(build(target))
