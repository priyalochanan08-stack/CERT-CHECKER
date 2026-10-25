"""Intake validation. Runs BEFORE the agent touches any source."""
import re

import config
from decide import parse_date

TRUE = {"on", "true", "yes", "1", "y", "checked"}


def validate(d):
    """Returns (clean_dict, errors). Missing consent is NOT an error: it becomes an UNCERTAIN result."""
    errors, clean = [], {}
    cid = (d.get("credential_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{4,64}", cid):
        errors.append("credential_id must be 4-64 characters: letters, digits, - or _")
    clean["credential_id"] = cid
    issuer = (d.get("issuer") or "").strip().lower()
    if issuer not in config.SOURCES:
        errors.append(f"issuer must be one of: {', '.join(config.SOURCES)}")
    clean["issuer"] = issuer
    for key, label in (("candidate_name", "candidate_name"), ("claimed_course", "claimed_course")):
        v = (d.get(key) or "").strip()
        if not v:
            errors.append(f"{label} must not be empty")
        clean[key] = v
    date = (d.get("claimed_date") or "").strip()
    if not parse_date(date):
        errors.append("claimed_date must be a real date, e.g. 2024-03-15")
    clean["claimed_date"] = date
    clean["consent"] = str(d.get("consent", "")).strip().lower() in TRUE
    return clean, errors
