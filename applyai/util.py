from __future__ import annotations

import random
import re
import time
import unicodedata

import requests
from bs4 import BeautifulSoup

from .log import log

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")

_session = requests.Session()
_session.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,de;q=0.8"})


def get(url, *, params=None, retries=3, sleep=(0.8, 2.0), **kw):
    """GET with polite random delay and backoff on 429/5xx. Returns Response or None."""
    for attempt in range(retries):
        time.sleep(random.uniform(*sleep))
        try:
            r = _session.get(url, params=params, timeout=25, **kw)
        except requests.RequestException as e:
            log.debug("GET %s failed: %s", url, e)
            continue
        if r.status_code == 200:
            return r
        if r.status_code in (403, 429, 500, 502, 503) and attempt < retries - 1:
            time.sleep(8 * (attempt + 1))  # temporary rate limit / bot block: back off
            continue
        log.warning("GET %s -> HTTP %s", url.split("?")[0], r.status_code)
        return None
    log.warning("GET %s failed after %d tries", url.split("?")[0], retries)
    return None


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "html.parser")
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "ul", "ol"]):
        tag.insert_after("\n")
    text = soup.get_text()
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def safe_name(s: str, maxlen: int = 80) -> str:
    """Folder-safe name that keeps umlauts but drops slashes, gender tags and clutter."""
    s = unicodedata.normalize("NFC", s or "Unknown")
    s = re.sub(r"\((?:[mwfdx]\s*/\s*)+[mwfdx]\)|\(all genders?\)|\(gn\)|\(f/m/x\)", "", s, flags=re.I)
    s = re.sub(r'[/\\:*?"<>|]+', "-", s)
    s = re.sub(r"\s+", " ", s).strip(" .-")
    return s[:maxlen].rstrip(" .-") or "Unknown"


def file_slug(s: str, maxlen: int = 40) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:maxlen].strip("_") or "Job"


# --- location normalization ------------------------------------------------------------
# Built-in town lists for a few metros. Any other city works too: list its suburbs / nearby
# towns under `aliases` in config.yaml (the setup does this for you).
PRESETS = {
    "munich": [
        "münchen", "muenchen", "garching", "gilching", "ottobrunn", "taufkirchen",
        "unterschleißheim", "unterschleissheim", "martinsried", "planegg", "gräfelfing",
        "graefelfing", "krailling", "oberpfaffenhofen", "weßling", "wessling", "feldkirchen",
        "ismaning", "oberschleißheim", "germering", "starnberg", "aschheim", "unterföhring",
        "hallbergmoos", "neubiberg", "brunnthal", "puchheim", "pullach", "grünwald", "oberhaching",
        "kirchheim bei münchen", "poing", "freising", "dachau", "fürstenfeldbruck", "eching",
        "neuried", "gauting", "vaterstetten"],
    "zurich": [
        "zürich", "zuerich", "dübendorf", "duebendorf", "rüschlikon", "rueschlikon", "wallisellen",
        "kloten", "schlieren", "dietikon", "opfikon", "glattbrugg", "winterthur", "thalwil",
        "adliswil", "uster", "regensdorf", "horgen", "küsnacht", "stäfa", "volketswil", "urdorf",
        "bülach"],
    "san francisco bay area": [
        "san francisco", "bay area", "south san francisco", "oakland", "berkeley", "emeryville",
        "palo alto", "mountain view", "menlo park", "sunnyvale", "santa clara", "san jose",
        "redwood city", "san mateo", "fremont", "milpitas", "hayward", "burlingame", "san carlos",
        "foster city", "cupertino", "los altos", "newark, ca", "pleasanton", "livermore", "alameda",
        "san bruno", "san leandro", "union city", "los gatos", "campbell", "belmont",
        "half moon bay"],
    "berlin": ["berlin", "potsdam", "adlershof", "schönefeld", "teltow"],
    "london": ["london", "greater london", "croydon", "canary wharf", "hammersmith"],
    "new york": ["new york", "nyc", "manhattan", "brooklyn", "queens", "jersey city", "hoboken"],
    "boston": ["boston", "cambridge, ma", "somerville", "waltham", "woburn", "burlington, ma",
               "lexington, ma", "bedford, ma", "watertown", "medford"],
    "seattle": ["seattle", "redmond", "bellevue", "kirkland", "bothell", "everett"],
    "paris": ["paris", "île-de-france", "ile-de-france", "saclay", "palaiseau", "boulogne-billancourt"],
    "amsterdam": ["amsterdam", "amstelveen", "haarlem", "schiphol"],
}
_GUARDS = [r"munich.*(north dakota|, nd)", r"baden-w(ü|ue)rttemberg", r"cambridge.*(uk|united kingdom|england)",
           r"london.*(ontario|, on\b|canada)", r"paris.*(texas|, tx)"]


PRESET_LOCAL = {"munich": "München", "zurich": "Zürich", "cologne": "Köln", "vienna": "Wien",
                "geneva": "Genève", "nuremberg": "Nürnberg"}


def local_name(loc: dict) -> str:
    """City name in the local language (StepStone / jobs.ch search by it)."""
    return loc.get("local_name") or PRESET_LOCAL.get(loc["name"].lower(), loc["name"])


def _names(loc: dict) -> list[str]:
    base = [loc["name"].lower()] + [a.lower() for a in loc.get("aliases", [])]
    if loc.get("local_name"):
        base.append(loc["local_name"].lower())
    return base + PRESETS.get(loc["name"].lower(), [])


def normalize_location(raw: str, locations: list[dict] | None = None,
                       remote: bool = False) -> tuple[str, str] | None:
    """Map a raw location string to (country, location name) for the configured locations.
    `locations` is config.yaml `locations`; `remote: true` also keeps remote postings."""
    s = (raw or "").lower()
    if not s:
        return None
    if any(re.search(g, s) for g in _GUARDS):
        return None
    for loc in locations or []:
        for n in _names(loc):
            if re.search(rf"(?<![a-zäöüé]){re.escape(n)}(?![a-zäöüé])", s):
                return loc.get("country", ""), loc["name"]
    if remote and re.search(r"\bremote\b|home.?office|anywhere|work from home", s):
        return "Remote", "Remote"
    return None

