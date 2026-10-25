"""Independent verifier. Separate code path from executor.py and extract.py.

It reloads the saved artifacts from disk and re-derives everything on its own:
 (a) screenshot is a real PNG, (b) the page shows the ID that was entered,
 (c) values re-read with a different parser equal the stored ones,
 (d) re-running the rules on those values gives the stored decision.
"""
import json
import os

import config
import decide

_PNG = b"\x89PNG\r\n\x1a\n"


def _find(text, labels):
    wanted = {decide.norm(x) for x in labels}
    for line in text.splitlines():
        if ":" in line:
            head, _, tail = line.partition(":")
            if decide.norm(head) in wanted and tail.strip():
                return tail.strip()
    return None


def verify(execution, decision, claim, source):
    notes, ok = [], True

    def check(cond, msg):
        nonlocal ok
        notes.append(("PASS: " if cond else "FAIL: ") + msg)
        ok = ok and cond

    shot = os.path.join(config.EVIDENCE_DIR, execution["screenshot_path"] or "missing")
    check(os.path.isfile(shot) and open(shot, "rb").read(8) == _PNG, "screenshot exists and is a valid PNG")
    tpath = os.path.join(config.EVIDENCE_DIR, execution["text_path"] or "missing")
    if not os.path.isfile(tpath):
        check(False, "captured page text exists")
        return ok, notes
    text = open(tpath, encoding="utf-8").read()
    cid = claim["credential_id"].lower()
    seen_id = (_find(text, source["labels"]["id"]) or "").lower()
    check(cid in text.lower() or cid in (execution["final_url"] or "").lower(),
          "page or final URL shows the credential ID that was entered")
    if seen_id:
        check(seen_id == cid, f"ID printed on page ({seen_id}) equals the ID entered ({cid})")

    got = {k: _find(text, source["labels"][k]) for k in ("name", "course", "date")}
    stored = json.loads(decision["inputs_json"])
    result = decision["result"]
    if result == "NOT_FOUND":
        check(any(p in text.lower() for p in source["not_found_phrases"]) and not got["name"],
              "page really says no record and shows no name")
    elif result in ("VERIFIED", "MISMATCH"):
        check(all(got[k] == stored["extracted"][k] for k in got), "independently re-read values equal stored values")
        redo, _, _ = decide.decide({"name": claim["candidate_name"], "course": claim["claimed_course"],
                                    "date": claim["claimed_date"]}, got, "OK")
        check(redo == result, f"re-running the rules gives {result} (got {redo})")
    elif result == "UNCERTAIN":
        blocked = execution["page_state"] in ("BLOCKED", "TIMEOUT", "ERROR")
        check(blocked or any(v is None for v in got.values()),
              "UNCERTAIN is justified: page blocked/failed or a required field is missing")
    return ok, notes
