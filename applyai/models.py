from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, field


LEGAL = r"\b(?:se|gmbh|ag|kg|kgaa|co|inc|corp|corporation|ltd|llc|plc|sa|sarl|bv|nv|group|holding)\b\.?"
FILLER = r"\b(?:for|the|of|and|in|a|an|w|m|d|f|x|gn|all|genders?)\b"


def canonical_company(company: str) -> str:
    """'TOPTICA Photonics SE' -> 'TOPTICA Photonics' (folder names and de-duplication)."""
    c = re.sub(r"[,&]|\+", " ", company)
    c = re.sub(LEGAL, " ", c, flags=re.I)
    return re.sub(r"\s+", " ", c).strip(" .-") or company


def job_key(title: str, company: str, city: str) -> str:
    """Stable identity used for de-duplication across sources."""
    t = re.sub(r"\(.*?\)|[•|].*$", " ", title.lower())  # gender tags, "(PIC)", "• 80-100%"
    t = re.sub(FILLER, " ", t)
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())
    return hashlib.sha1(f"{norm(canonical_company(company))}|{norm(t)}|{norm(city)}".encode()).hexdigest()[:16]


@dataclass
class Job:
    title: str
    company: str
    location: str              # raw location string from the source
    url: str
    source: str                # linkedin, jobs.ch, stepstone, greenhouse, browser-agent, ...
    description: str = ""      # plain text
    posted: str = ""           # ISO date or free text
    country: str = ""          # normalized country (from config `locations`)
    city: str = ""             # normalized location name from config `locations`
    extra: dict = field(default_factory=dict)
    # filled by the matcher
    fit_score: float | None = None
    fit_reason: str = ""
    keywords: list = field(default_factory=list)  # [{"term":..., "weight":1-3, "aliases":[...]}]
    language: str = "en"

    @property
    def key(self) -> str:
        return job_key(self.title, self.company, self.city)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, d):
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
