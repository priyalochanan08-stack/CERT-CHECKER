"""Orchestrates the agent loop: intake -> consent gate -> proposal -> approval -> execute -> extract
-> decide -> independent verify. Every step is logged. Retries never execute twice."""
import hashlib
import json
import os
import sqlite3

import config
import db
import decide
import executor
import extract
import verifier

UNRESOLVED = {"UNCERTAIN", "FAILED", "PENDING", "TAMPER_DETECTED", "VERIFICATION_FAILED", "REJECTED"}


def sha(s):
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def submit_record(clean):
    """clean = output of validation.validate. Returns record_id."""
    ts = db.now()
    rid = db.insert("records", "REC", "record_id", credential_id=clean["credential_id"],
                    issuer=clean["issuer"], candidate_name=clean["candidate_name"],
                    claimed_course=clean["claimed_course"], claimed_date=clean["claimed_date"],
                    consent=int(clean["consent"]), consent_ts=ts if clean["consent"] else None, created_at=ts)
    db.step(rid, "intake", f"validated {clean['credential_id']} @ {clean['issuer']}")
    if not clean["consent"]:
        reason = "Consent missing: source was not queried"
        did = db.insert("decisions", "DEC", "decision_id", record_id=rid, exec_id=None, result="UNCERTAIN",
                        reason=reason, inputs_json=json.dumps({"consent": False}), created_at=db.now())
        db.step(rid, "consent_gate", f"blocked, no source queried -> UNCERTAIN ({did})")
        return rid
    src = config.SOURCES[clean["issuer"]]
    payload = {"action": "open_url_and_capture", "record_id": rid, "source_id": src["source_id"],
               "url": src["verify_url"].replace("{id}", clean["credential_id"]),
               "credential_id": clean["credential_id"], "plan_version": 1}
    pid = db.insert("plans", "PLN", "plan_id", record_id=rid, payload_json=json.dumps(payload, sort_keys=True),
                    approved_hash=None, status="PENDING_APPROVAL", approved_at=None, created_at=db.now())
    db.step(rid, "proposed", f"{pid}: {proposal_text(pid)}")
    return rid


def proposal_text(plan_id):
    p = json.loads(db.q("SELECT payload_json FROM plans WHERE plan_id=?", (plan_id,), one=True)["payload_json"])
    return f"Open {p['url']}, enter ID {p['credential_id']}, capture a screenshot and the page text."


def approve(plan_id):
    plan = db.q("SELECT * FROM plans WHERE plan_id=?", (plan_id,), one=True)
    if not plan or plan["status"] != "PENDING_APPROVAL":
        return False
    db.update("plans", "plan_id", plan_id, status="APPROVED", approved_at=db.now(),
              approved_hash=sha(plan["payload_json"]))
    db.step(plan["record_id"], "approved", f"{plan_id} approved, hash {sha(plan['payload_json'])[:12]}")
    return True


def reject(plan_id):
    plan = db.q("SELECT * FROM plans WHERE plan_id=?", (plan_id,), one=True)
    if plan and plan["status"] == "PENDING_APPROVAL":
        db.update("plans", "plan_id", plan_id, status="REJECTED")
        db.step(plan["record_id"], "rejected", f"{plan_id} rejected by the user; nothing was run")


def execute(plan_id):
    """Runs an APPROVED plan. Returns exec_id, or None if refused."""
    plan = db.q("SELECT * FROM plans WHERE plan_id=?", (plan_id,), one=True)
    rid = plan["record_id"]
    if plan["status"] not in ("APPROVED", "EXECUTED"):
        return None
    if sha(plan["payload_json"]) != plan["approved_hash"]:  # tamper detection
        db.update("plans", "plan_id", plan_id, status="TAMPER_DETECTED")
        db.step(rid, "tamper_detected", "plan changed after approval; refused to run")
        return None
    p = json.loads(plan["payload_json"])
    idem = sha(f"{rid}|{p['url']}|{p['credential_id']}|{p['plan_version']}")
    try:
        eid = db.insert("executions", "EXE", "exec_id", plan_id=plan_id, idem_key=idem, status="RUNNING",
                        page_state=None, source_id=p["source_id"], attempts=0)
    except sqlite3.IntegrityError:  # retry of an already-executed plan
        old = db.q("SELECT exec_id FROM executions WHERE idem_key=?", (idem,), one=True)["exec_id"]
        db.step(rid, "duplicate_prevented", f"retry returned stored result {old}; nothing re-executed")
        return old

    rec = db.q("SELECT * FROM records WHERE record_id=?", (rid,), one=True)
    src = config.SOURCES[rec["issuer"]]
    cap = executor.capture(p["url"], os.path.join(config.EVIDENCE_DIR, eid))
    db.update("executions", "exec_id", eid, status="DONE" if cap["page_state"] in ("OK", "BLOCKED") else "FAILED",
              page_state=cap["page_state"], screenshot_path=f"{eid}/page.png", text_path=f"{eid}/page.txt",
              final_url=cap["final_url"], http_status=cap["http_status"], retrieved_at=cap["retrieved_at"],
              attempts=cap["attempts"], backend=cap["backend"], error=cap["error"])
    db.step(rid, "captured", f"{eid}: state={cap['page_state']}, attempts={cap['attempts']}, "
                             f"http={cap['http_status']}, backend={cap['backend']}")

    values, no_record = extract.extract(cap["text"], src)
    state = "NO_RECORD" if (cap["page_state"] == "OK" and no_record) else cap["page_state"]
    claim = {"name": rec["candidate_name"], "course": rec["claimed_course"], "date": rec["claimed_date"]}
    result, reason, details = decide.decide(claim, values, state)
    details["source_id"], details["url"], details["retrieved_at"] = p["source_id"], p["url"], cap["retrieved_at"]
    did = db.insert("decisions", "DEC", "decision_id", record_id=rid, exec_id=eid, result=result, reason=reason,
                    inputs_json=json.dumps(details), created_at=db.now())
    db.step(rid, "decided", f"{did}: {result}. {reason}")

    execution = db.q("SELECT * FROM executions WHERE exec_id=?", (eid,), one=True)
    decision = db.q("SELECT * FROM decisions WHERE decision_id=?", (did,), one=True)
    claim_full = dict(rec)
    ok, notes = verifier.verify(execution, decision, claim_full, src)
    db.insert("verifications", "VER", "ver_id", exec_id=eid, decision_id=did, passed=int(ok),
              notes_json=json.dumps(notes), created_at=db.now())
    db.step(rid, "verified", "independent verifier: " + ("PASSED" if ok else "FAILED -> VERIFICATION_FAILED"))
    db.update("plans", "plan_id", plan_id, status="EXECUTED")
    return eid


def demo_tamper(plan_id):
    """DEMO ONLY: approve, silently change the plan, then try to run it. Must be refused."""
    approve(plan_id)
    plan = db.q("SELECT payload_json FROM plans WHERE plan_id=?", (plan_id,), one=True)
    p = json.loads(plan["payload_json"])
    p["url"] = p["url"].replace("localhost", "evil.example")
    db.update("plans", "plan_id", plan_id, payload_json=json.dumps(p, sort_keys=True))
    return execute(plan_id)


def view(rid):
    """Everything about one record, with the status shown in the results table."""
    rec = db.q("SELECT * FROM records WHERE record_id=?", (rid,), one=True)
    if not rec:
        return None
    plan = db.q("SELECT * FROM plans WHERE record_id=? ORDER BY plan_id DESC", (rid,), one=True)
    dec = db.q("SELECT * FROM decisions WHERE record_id=? ORDER BY decision_id DESC", (rid,), one=True)
    ex = db.q("SELECT * FROM executions WHERE exec_id=?", (dec["exec_id"],), one=True) if dec and dec["exec_id"] else None
    ver = db.q("SELECT * FROM verifications WHERE decision_id=?", (dec["decision_id"],), one=True) if dec else None
    if dec:
        if ver and not ver["passed"]:
            status, reason = "VERIFICATION_FAILED", "Independent verifier disagreed with the decision (see evidence)"
        else:
            status, reason = dec["result"], dec["reason"]
    elif plan:
        status = {"PENDING_APPROVAL": "PENDING", "APPROVED": "PENDING", "REJECTED": "REJECTED",
                  "TAMPER_DETECTED": "TAMPER_DETECTED"}.get(plan["status"], "PENDING")
        reason = {"PENDING": "Waiting for human approval", "REJECTED": "Rejected by the user; nothing was run",
                  "TAMPER_DETECTED": "Plan changed after approval; execution refused"}[status]
    else:
        status, reason = "PENDING", ""
    steps = db.q("SELECT * FROM steps WHERE record_id=? ORDER BY step_id", (rid,))
    return {"record": rec, "plan": plan, "decision": dec, "execution": ex, "verification": ver,
            "steps": steps, "status": status, "reason": reason}


def all_views():
    views = [view(r["record_id"]) for r in db.q("SELECT record_id FROM records ORDER BY record_id")]
    views.sort(key=lambda v: (v["status"] not in UNRESOLVED, v["record"]["record_id"]))
    return views
