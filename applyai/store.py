"""Remembers every posting the bot has seen, so each job is scored and tailored only once."""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text()) if path.exists() else {}
        self._rekey()

    def _rekey(self):
        """Re-derive keys with the current de-duplication rule; merge duplicates (best status wins)."""
        from .models import job_key
        order = {"tailored": 3, "failed": 2, "shortlisted": 1, "rejected": 0}
        new: dict = {}
        for rec in self.data.values():
            k = job_key(rec.get("title", ""), rec.get("company", ""), rec.get("city", ""))
            if k not in new or order.get(rec.get("status"), 0) > order.get(new[k].get("status"), 0):
                new[k] = rec
        if len(new) != len(self.data) or set(new) != set(self.data):
            self.data = new
            self.save()

    def seen(self, key: str) -> bool:
        return key in self.data

    def get(self, key: str) -> dict | None:
        return self.data.get(key)

    def put(self, key: str, **fields):
        rec = self.data.setdefault(key, {"first_seen": dt.date.today().isoformat()})
        rec.update(fields)
        self.save()

    def save(self):
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=1, ensure_ascii=False))
        tmp.replace(self.path)
