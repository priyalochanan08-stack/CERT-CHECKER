"""Deterministic decision engine. No AI, no page badges: only claim vs extracted values.

Thresholds: name token_sort_ratio >= 90, course token_set_ratio >= 85, date exact
(month-level if the issuer shows only month and year). Uses rapidfuzz if installed,
otherwise a difflib fallback with the same scoring idea.
"""
import datetime
import re
import unicodedata
from difflib import SequenceMatcher

try:  # optional, faster and the reference implementation
    from rapidfuzz import fuzz as _rf
except Exception:  # pragma: no cover
    _rf = None

NAME_MIN = 90
COURSE_MIN = 85


def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", s.lower())).strip()


def _ratio(a, b):
    return 100.0 * SequenceMatcher(None, a, b).ratio() if a and b else 0.0


def token_sort_ratio(a, b):
    a, b = norm(a), norm(b)
    if _rf:
        return _rf.token_sort_ratio(a, b)
    return _ratio(" ".join(sorted(a.split())), " ".join(sorted(b.split())))


def token_set_ratio(a, b):
    a, b = norm(a), norm(b)
    if _rf:
        return _rf.token_set_ratio(a, b)
    t1, t2 = set(a.split()), set(b.split())
    if not t1 or not t2:
        return 0.0
    inter = " ".join(sorted(t1 & t2))
    c1 = (inter + " " + " ".join(sorted(t1 - t2))).strip()
    c2 = (inter + " " + " ".join(sorted(t2 - t1))).strip()
    return max(_ratio(inter, c1), _ratio(inter, c2), _ratio(c1, c2))


_FULL = ["%Y-%m-%d", "%Y/%m/%d", "%d %B %Y", "%d %b %Y", "%B %d, %Y", "%b %d, %Y", "%d-%m-%Y", "%d/%m/%Y"]
_MONTH = ["%B %Y", "%b %Y", "%Y-%m"]


def parse_date(s):
    """Returns (year, month, day or None) or None if it cannot be parsed."""
    s = re.sub(r"\s+", " ", (s or "").strip().replace(",", ", ").replace(",  ", ", "))
    for f in _FULL:
        try:
            d = datetime.datetime.strptime(s, f)
            return (d.year, d.month, d.day)
        except ValueError:
            pass
    for f in _MONTH:
        try:
            d = datetime.datetime.strptime(s, f)
            return (d.year, d.month, None)
        except ValueError:
            pass
    return None


def dates_match(a, b):
    pa, pb = parse_date(a), parse_date(b)
    if not pa or not pb:
        return False
    return pa[:2] == pb[:2] and (pa[2] is None or pb[2] is None or pa[2] == pb[2])


def decide(claim, extracted, page_state):
    """claim: {name, course, date}. extracted: {name, course, date}. page_state: OK|NO_RECORD|BLOCKED|TIMEOUT|ERROR.
    Returns (result, reason, details)."""
    details = {"thresholds": {"name": NAME_MIN, "course": COURSE_MIN, "date": "exact"},
               "page_state": page_state, "claim": claim, "extracted": extracted}
    if page_state == "NO_RECORD":
        return "NOT_FOUND", "Issuer page reports no record for this credential ID", details
    if page_state in ("BLOCKED", "TIMEOUT", "ERROR"):
        return "UNCERTAIN", f"Required evidence missing: page state is {page_state}", details
    missing = [k for k in ("name", "course", "date") if not (extracted or {}).get(k)]
    if missing:
        return "UNCERTAIN", f"Required evidence missing: could not read {', '.join(missing)} from the page", details
    if not parse_date(extracted["date"]) or not parse_date(claim["date"]):
        return "UNCERTAIN", f"Date could not be parsed (page says '{extracted['date']}')", details
    name_score = token_sort_ratio(claim["name"], extracted["name"])
    course_score = token_set_ratio(claim["course"], extracted["course"])
    ok = {"name": name_score >= NAME_MIN, "course": course_score >= COURSE_MIN,
          "date": dates_match(claim["date"], extracted["date"])}
    details["scores"] = {"name": round(name_score, 1), "course": round(course_score, 1)}
    details["matches"] = ok
    if all(ok.values()):
        return "VERIFIED", "Name, course and date all match the issuer record", details
    bad = [f"{k} (claimed '{claim[k]}', issuer shows '{extracted[k]}')" for k, v in ok.items() if not v]
    return "MISMATCH", "Does not match the issuer record: " + "; ".join(bad), details
