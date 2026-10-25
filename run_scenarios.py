"""Runs the labelled scenarios against the mock issuer and writes scenario_report.md.
Expected values are written down BEFORE running; any difference is reported, never hidden.
Replace/extend SCENARIOS with the official test cases when you have them."""
import json
import os
import shutil
import sys

import config

RUN_DIR = os.path.join(config.BASE, "scenario_run")
shutil.rmtree(RUN_DIR, ignore_errors=True)
os.makedirs(RUN_DIR)
config.EVIDENCE_DIR = os.path.join(RUN_DIR, "evidence")
config.DB_PATH = os.path.join(RUN_DIR, "scenarios.db")

import db  # noqa: E402
import mock_issuer  # noqa: E402
import pipeline  # noqa: E402
import validation  # noqa: E402

JANE = dict(credential_id="GOOD1001", issuer="mock", candidate_name="Jane Doe",
            claimed_course="Machine Learning", claimed_date="2024-03-15", consent="yes")
SCENARIOS = [
    ("T1 Valid credential", dict(JANE), "VERIFIED", "ID, name, course and date all match the issuer page"),
    ("T2 Mismatch", dict(JANE, candidate_name="Jane Roe"), "MISMATCH", "Valid ID but the claimed name is wrong"),
    ("T3 Not found", dict(JANE, credential_id="ZZZ999"), "NOT_FOUND", "ID does not exist at the issuer"),
    ("T4 Missing evidence (no consent)", dict(JANE, consent=""), "UNCERTAIN", "Consent unchecked: source must not be queried"),
    ("X1 Blocked page (captcha)", dict(JANE, credential_id="BLOCK001"), "UNCERTAIN", "Issuer shows a captcha: must not guess"),
    ("X2 Badge trap", dict(JANE, credential_id="TRAP2002"), "MISMATCH", "Page shows a green VERIFIED badge but another person's name"),
]


def main():
    db.init(config.DB_PATH)
    srv = mock_issuer.start_background()
    rows, lines = [], []
    for title, form, expected, why in SCENARIOS:
        clean, errs = validation.validate(form)
        assert not errs, errs
        rid = pipeline.submit_record(clean)
        v = pipeline.view(rid)
        queried = False
        if v["plan"]:
            queried = True
            pipeline.approve(v["plan"]["plan_id"])
            pipeline.execute(v["plan"]["plan_id"])
            v = pipeline.view(rid)
        actual = v["status"]
        ev = v["execution"]
        ver = json.loads(v["verification"]["notes_json"]) if v["verification"] else ["no verification (source was not queried)"]
        note = "" if actual == expected else "DIFFERENT FROM EXPECTED - investigate before presenting as success."
        rows.append((title, expected, actual, "PASS" if actual == expected else "DIFF"))
        lines += [f"## {title}", f"- Purpose: {why}", f"- Input: `{json.dumps({k: form[k] for k in form if k != 'consent'})}`, consent={'yes' if clean['consent'] else 'NO'}",
                  f"- Record: {rid}, source queried: {'yes' if queried else 'no'}",
                  f"- Expected: **{expected}**", f"- Actual: **{actual}** ({v['reason']})",
                  f"- Evidence: {('evidence/' + ev['exec_id'] + '/page.png, page.txt') if ev else 'none (no page was opened)'}",
                  "- Verification: " + "; ".join(ver), f"- Result: **{'PASS' if actual == expected else 'DIFF'}** {note}", ""]
    out = ["# Scenario report", "",
           "Issuer: **MOCK ISSUER (demo data)**. Scenarios T1-T4 mirror valid / mismatch / not found / missing evidence; "
           "X1-X2 are extra robustness checks. If you have official test cases, replace SCENARIOS and rerun.", "",
           "| Run | Expected | Actual | Result |", "|---|---|---|---|"]
    out += [f"| {a} | {b} | {c} | {d} |" for a, b, c, d in rows] + [""] + lines
    with open(os.path.join(config.BASE, "scenario_report.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(out))
    for a, b, c, d in rows:
        print(f"{d}  {a}: expected {b}, got {c}")
    if srv:
        srv.shutdown()
    return 0 if all(r[3] == "PASS" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())
