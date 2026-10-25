"""Reads Name / Course / Date / Certificate ID from captured page text using the source's labels."""
import re


def extract(text, source):
    out = {"name": None, "course": None, "date": None, "id": None}
    for key, labels in source["labels"].items():
        alt = "|".join(re.escape(x) for x in sorted(labels, key=len, reverse=True))
        pat = re.compile(rf"^\s*(?:{alt})\s*[:\-\u2013]\s*(.+?)\s*$", re.I)
        for line in text.splitlines():
            m = pat.match(line)
            if m:
                out[key] = m.group(1)
                break
    phrase_seen = any(p in text.lower() for p in source["not_found_phrases"])
    no_record = phrase_seen and not out["name"]  # a stray phrase never hides a real record
    return out, no_record
