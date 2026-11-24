"""Find the salary range a posting states, as yearly amounts.

Handles the usual ways postings write pay: "$120,000 - $160,000", "$120K–$160K / year",
"€60.000 bis 75.000 € brutto", "EUR 60,000–75,000", "CHF 100'000 - 120'000", "£45k",
"$55 - $70 per hour". Hourly and monthly amounts are turned into yearly ones (2,080 h,
12 months). Returns None when nothing plausible is found. This is a heuristic: the
dashboard shows the matched text so the user can check it.
"""
from __future__ import annotations

import re

CUR = {"$": "USD", "usd": "USD", "us$": "USD", "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR",
       "chf": "CHF", "fr.": "CHF", "£": "GBP", "gbp": "GBP", "sek": "SEK", "dkk": "DKK", "nok": "NOK",
       "pln": "PLN", "cad": "CAD", "c$": "CAD", "aud": "AUD", "a$": "AUD"}
_CUR_RX = r"(?:US\$|C\$|A\$|\$|€|£|USD|EUR|CHF|GBP|SEK|DKK|NOK|PLN|CAD|AUD|Euros?|Fr\.)"
_NUM = r"\d{1,3}(?:[.,' ’ ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d+)?"
_K = r"\s?(?:k|K|thousand|Tsd\.?|TEUR)?"
_NUM_K = rf"(?:{_NUM}){_K}"
_SEP = r"\s*(?:-|–|—|to|bis|and|und|à|a)\s*"
PATTERNS = [
    # $120,000 - $160,000 | €60k – €75k | CHF 100'000 – 120'000 | USD 150k-180k
    re.compile(rf"(?P<c1>{_CUR_RX})\s?(?P<a>{_NUM_K}){_SEP}(?:{_CUR_RX})?\s?(?P<b>{_NUM_K})", re.I),
    # 60.000 - 75.000 € | 60k-75k EUR
    re.compile(rf"(?P<a>{_NUM_K})\s?(?:{_CUR_RX})?{_SEP}(?P<b>{_NUM_K})\s?(?P<c2>{_CUR_RX})", re.I),
    # single value: $150,000 | 65.000 € | EUR 70k
    re.compile(rf"(?P<c1>{_CUR_RX})\s?(?P<a>{_NUM_K})(?![\d.,'])", re.I),
    re.compile(rf"(?P<a>{_NUM_K})\s?(?P<c2>{_CUR_RX})", re.I),
]
HOURLY = re.compile(r"per hour|/\s?h(?:ou)?r\b|hourly|pro stunde|stundenlohn|an hour", re.I)
MONTHLY = re.compile(r"per month|/\s?mo(?:nth)?\b|monthly|monatlich|pro monat|im monat|mtl\.", re.I)
CONTEXT = re.compile(r"salary|compensation|pay|base|gehalt|vergütung|lohn|range|brutto|gross|salaire|"
                     r"jahres|annual|per year|/yr|/year|ote|ctc|stipend", re.I)


def _num(s: str) -> float:
    s = s.strip()
    k = bool(re.search(r"(k|thousand|tsd\.?|teur)$", s, re.I))
    s = re.sub(r"\s?(k|K|thousand|Tsd\.?|TEUR)$", "", s).strip()
    # thousands separators vs decimals: "60.000" / "60,000" / "100'000" -> thousands
    if re.fullmatch(r"\d{1,3}(?:[.,' ’ ]\d{3})+", s):
        v = float(re.sub(r"[.,' ’ ]", "", s))
    elif re.fullmatch(r"\d{1,3}(?:[.,' ’ ]\d{3})+[.,]\d{1,2}", s):
        v = float(re.sub(r"[.,' ’ ]", "", s[:-3]) + "." + s[-2:])
    else:
        v = float(s.replace(",", "."))
    return v * 1000 if k else v


def find(text: str) -> dict | None:
    """{"currency", "min", "max", "period": "year", "text"} with yearly amounts, or None."""
    if not text:
        return None
    best = None
    for rx in PATTERNS:
        for m in rx.finditer(text):
            cur_raw = (m.groupdict().get("c1") or m.groupdict().get("c2") or "").lower()
            cur = CUR.get(cur_raw)
            if not cur:
                continue
            try:
                a = _num(m.group("a"))
                b = _num(m.group("b")) if m.groupdict().get("b") else a
            except ValueError:
                continue
            if "k" in m.group(0).lower() and a < 1000 <= b:  # "$120k - 160,000"
                a *= 1000
            if b < a:
                a, b = b, a
            window = text[max(0, m.start() - 120): m.end() + 60]
            if HOURLY.search(window) and b < 1000:
                a, b = a * 2080, b * 2080
            elif MONTHLY.search(window) and b < 30000:
                a, b = a * 12, b * 12
            if not (12000 <= a <= 1_500_000 and 12000 <= b <= 1_500_000) or b > a * 3:
                continue  # a funding round, a price, a team size ... not a salary
            ctx = bool(CONTEXT.search(window))
            score = (2 if ctx else 0) + (1 if a != b else 0)
            if best is None or score > best[0]:
                best = (score, {"currency": cur, "min": round(a), "max": round(b), "period": "year",
                                "text": re.sub(r"\s+", " ", m.group(0)).strip()[:60]})
        if best and best[0] >= 2:
            break
    if not best or best[0] < 1 and not re.search(r"salary|gehalt|compensation", text, re.I):
        return None
    return best[1]


def parse_ask(text: str, default_currency: str = "") -> dict | None:
    """The salary the user gave in a form ("EUR 70k", "105000 CHF", "140,000")."""
    if not text:
        return None
    hit = find(text + " salary")
    if hit:
        return hit
    m = re.search(_NUM_K, text)
    if m and default_currency:
        try:
            v = _num(m.group(0))
        except ValueError:
            return None
        if 12000 <= v <= 1_500_000:
            return {"currency": default_currency, "min": round(v), "max": round(v), "period": "year", "text": text[:60]}
    return None


COUNTRY_CURRENCY = {"USA": "USD", "Germany": "EUR", "Austria": "EUR", "France": "EUR", "Netherlands": "EUR",
                    "Spain": "EUR", "Italy": "EUR", "Ireland": "EUR", "Belgium": "EUR", "Finland": "EUR",
                    "Switzerland": "CHF", "United Kingdom": "GBP", "UK": "GBP", "Sweden": "SEK",
                    "Denmark": "DKK", "Norway": "NOK", "Poland": "PLN", "Canada": "CAD", "Australia": "AUD"}
