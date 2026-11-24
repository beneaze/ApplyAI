"""What has been sent: the apply queue, the applied log and the _Tracker.csv spreadsheet."""
from __future__ import annotations

import csv
import datetime as dt
import json
from pathlib import Path

STATUSES = ["applied", "skipped", "interview", "offer", "rejected", "withdrawn", "blocked"]
COLUMNS = ["Company", "Position", "Location", "Fit", "ATS", "Status", "Applied on", "Applied via",
           "Salary asked", "Posting URL", "Folder", "Notes"]


class Tracker:
    def __init__(self, ws):
        self.ws = ws
        self.path = ws.state / "applied.json"
        self.data: dict = json.loads(self.path.read_text()) if self.path.exists() else {}

    def save(self, store_data: dict):
        self.path.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        self.write_csv(store_data)

    def queue(self, store_data: dict) -> list[dict]:
        """Tailored applications that were not sent or skipped yet, best fit first."""
        out = []
        for key, r in store_data.items():
            if r.get("status") != "tailored" or not r.get("folder") or key in self.data:
                continue
            folder = Path(r["folder"])
            job = r.get("job") or {}
            out.append({"key": key, "company": r.get("company", ""), "title": r.get("title", ""),
                        "city": r.get("city", ""), "fit": r.get("fit"), "ats": r.get("ats"),
                        "url": r.get("url", ""), "apply_url": (job.get("extra") or {}).get("apply_url", ""),
                        "folder": str(folder), "found": r.get("first_seen", ""),
                        "cv": str(next(folder.glob("CV_*.pdf"), "")),
                        "letter": str(next(folder.glob("Cover_Letter_*.pdf"), ""))})
        return sorted(out, key=lambda x: (-(x["fit"] or 0), x["found"]))

    def mark(self, store_data: dict, folder: str, status: str, via: str = "", note: str = "",
             salary: str = "") -> dict:
        folder_p = str(Path(folder).expanduser().resolve())
        key = next((k for k, r in store_data.items() if r.get("folder") and
                    str(Path(r["folder"]).resolve()) == folder_p), None)
        if key is None:
            raise SystemExit(f"No tailored application in {folder}")
        r = store_data[key]
        rec = self.data.get(key, {})
        rec.update({"company": r.get("company", ""), "title": r.get("title", ""), "city": r.get("city", ""),
                    "url": r.get("url", ""), "folder": r["folder"], "fit": r.get("fit"), "ats": r.get("ats"),
                    "status": status})
        if status == "applied" and not rec.get("date"):
            rec["date"] = dt.date.today().isoformat()
        for k, v in (("via", via), ("note", note), ("salary", salary)):
            if v:
                rec[k] = v
        rec.setdefault("history", []).append([dt.date.today().isoformat(), status])
        self.data[key] = rec
        self.save(store_data)
        return rec

    def write_csv(self, store_data: dict):
        rows = []
        for key, r in store_data.items():
            if r.get("status") != "tailored":
                continue
            a = self.data.get(key, {})
            rows.append([r.get("company", ""), r.get("title", ""), r.get("city", ""), r.get("fit", ""),
                         r.get("ats", ""), a.get("status", "to apply"), a.get("date", ""), a.get("via", ""),
                         a.get("salary", ""), r.get("url", ""),
                         str(Path(r["folder"]).relative_to(self.ws.root)) if r.get("folder") and
                         Path(r["folder"]).is_relative_to(self.ws.root) else r.get("folder", ""),
                         a.get("note", "")])
        rows.sort(key=lambda x: (x[5] != "to apply", x[0]))
        with (self.ws.root / "_Tracker.csv").open("w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(COLUMNS)
            w.writerows(rows)
