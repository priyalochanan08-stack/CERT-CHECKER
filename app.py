"""Web UI (stdlib only). Run: python app.py  ->  http://localhost:8080"""
import csv
import html
import io
import json
import os
import re
import threading
import urllib.parse as up
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import db
import mock_issuer
import pipeline
import validation

E = html.escape
RUN_LOCK = threading.Lock()  # one browser job at a time
CSS = """
:root{--bg:#f7f7f5;--card:#fff;--ink:#1c1c1a;--mut:#6b6b66;--line:#e3e3de}
@media(prefers-color-scheme:dark){:root{--bg:#161614;--card:#21211e;--ink:#ecece8;--mut:#a1a19a;--line:#37372f}}
body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--ink);margin:0;padding:20px}
main{max-width:1100px;margin:auto}h1{margin:0 0 4px}h2{margin:0 0 10px;font-size:17px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px;margin:14px 0}
.mut{color:var(--mut)}table{border-collapse:collapse;width:100%}td,th{padding:8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
input,select,textarea{font:inherit;padding:7px;border:1px solid var(--line);border-radius:6px;background:var(--bg);color:var(--ink);width:100%;box-sizing:border-box}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:10px}
button{font:inherit;padding:7px 14px;border:1px solid var(--line);border-radius:6px;background:var(--ink);color:var(--bg);cursor:pointer}
button.s{background:transparent;color:var(--ink)}a{color:#2563eb}
.b{display:inline-block;padding:2px 9px;border-radius:99px;font-size:12px;font-weight:700;color:#fff;background:#6b7280}
.VERIFIED{background:#16a34a}.MISMATCH{background:#dc2626}.NOT_FOUND{background:#ea580c}.UNCERTAIN{background:#ca8a04}
.PENDING{background:#2563eb}.TAMPER_DETECTED,.VERIFICATION_FAILED,.FAILED{background:#7c3aed}.REJECTED{background:#6b7280}
.flash{background:#fef3c7;color:#78350f;padding:10px;border-radius:8px}img.shot{max-width:100%;border:1px solid var(--line);border-radius:6px}
img.th{width:90px;border:1px solid var(--line);border-radius:4px}pre{white-space:pre-wrap;font-size:13px}
.pin{background:rgba(202,138,4,.08)}
"""


def page(body, title="Certificate Authenticity Checker"):
    return (f"<!doctype html><html><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{E(title)}</title><style>{CSS}</style></head><body><main>{body}</main></body></html>")


def badge(s):
    return f'<span class="b {E(s)}">{E(s)}</span>'


def dashboard(flash=""):
    views = pipeline.all_views()
    pending = [v for v in views if v["plan"] and v["plan"]["status"] == "PENDING_APPROVAL"]
    out = ["<h1>Certificate Authenticity Checker</h1>",
           "<p class=mut>Plan &rarr; human approval &rarr; capture &rarr; rules &rarr; independent verification. "
           "Demo issuer: <b>MOCK ISSUER (demo data)</b>.</p>"]
    if flash:
        out.append(f"<p class=flash>{E(flash)}</p>")
    if pending:
        out.append("<div class=card><h2>Proposed actions waiting for your approval</h2><table>")
        for v in pending:
            pid = v["plan"]["plan_id"]
            out.append(f"<tr><td>{E(v['record']['record_id'])}</td><td>{E(pipeline.proposal_text(pid))}</td><td>"
                       f"<form method=post action=/approve style='display:inline'><input type=hidden name=plan_id value={pid}>"
                       f"<button>Approve &amp; run</button></form> "
                       f"<form method=post action=/reject style='display:inline'><input type=hidden name=plan_id value={pid}>"
                       f"<button class=s>Reject</button></form> "
                       f"<form method=post action=/tamper style='display:inline'><input type=hidden name=plan_id value={pid}>"
                       f"<button class=s title='Demo: approve, then alter the plan, then try to run it'>Demo: tamper</button></form>"
                       f"</td></tr>")
        out.append("</table><form method=post action=/approve><input type=hidden name=all value=1>"
                   f"<p><button>Approve all {len(pending)} &amp; run</button></p></form></div>")
    issuers = "".join(f"<option value={E(k)}>{E(v['display'])}</option>" for k, v in config.SOURCES.items())
    out.append(f"""<div class=card><h2>Check a credential</h2><form method=post action=/submit><div class=g>
<label>Credential ID<input name=credential_id value=GOOD1001 required></label>
<label>Issuer<select name=issuer>{issuers}</select></label>
<label>Candidate name<input name=candidate_name value="Jane Doe" required></label>
<label>Claimed course<input name=claimed_course value="Machine Learning" required></label>
<label>Claimed date<input name=claimed_date value=2024-03-15 required></label></div>
<p><label><input type=checkbox name=consent style='width:auto'> The candidate consented to this check</label></p>
<button>Propose check</button></form></div>
<div class=card><h2>Bulk check (paste CSV)</h2><form method=post action=/bulk>
<textarea name=csv rows=4>credential_id,issuer,candidate_name,claimed_course,claimed_date,consent
GOOD1002,mock,John Smith,Data Analysis,2024-05-20,yes
ZZZ999,mock,Nobody Here,Unknown Course,2024-01-01,yes</textarea>
<p class=mut>One approval screen for the whole batch. Invalid rows are reported with a reason.</p><button>Propose batch</button></form></div>""")
    out.append("<div class=card><h2>Results</h2>")
    if not views:
        out.append("<p class=mut>No checks yet.</p>")
    else:
        out.append("<table><tr><th>Record</th><th>Result</th><th>Reason</th><th>Evidence</th></tr>")
        for v in views:
            r, ex = v["record"], v["execution"]
            th = f"<img class=th src=/evidence/{ex['exec_id']}/page.png>" if ex and ex["screenshot_path"] else ""
            cls = " class=pin" if v["status"] in pipeline.UNRESOLVED else ""
            out.append(f"<tr{cls}><td><a href=/record/{r['record_id']}>{r['record_id']}</a><br><span class=mut>{E(r['credential_id'])}"
                       f"<br>{E(r['candidate_name'])}</span></td><td>{badge(v['status'])}</td><td>{E(v['reason'])}</td><td>{th}</td></tr>")
        out.append("</table><p><a href=/export.zip>Download evidence bundle (.zip)</a></p>")
    out.append("</div>")
    return page("".join(out))


def record_page(rid):
    v = pipeline.view(rid)
    if not v:
        return None
    r, d, ex, ver = v["record"], v["decision"], v["execution"], v["verification"]
    o = [f"<p><a href=/>&larr; back</a></p><h1>{r['record_id']} {badge(v['status'])}</h1><p>{E(v['reason'])}</p>"]
    ins = json.loads(d["inputs_json"]) if d else {}
    got = ins.get("extracted") or {}
    o.append("<div class=card><h2>Claimed vs observed</h2><table><tr><th></th><th>Claimed</th><th>Issuer page shows</th></tr>"
             f"<tr><td>Name</td><td>{E(r['candidate_name'])}</td><td>{E(str(got.get('name')))}</td></tr>"
             f"<tr><td>Course</td><td>{E(r['claimed_course'])}</td><td>{E(str(got.get('course')))}</td></tr>"
             f"<tr><td>Date</td><td>{E(r['claimed_date'])}</td><td>{E(str(got.get('date')))}</td></tr></table>")
    if ins.get("scores"):
        o.append(f"<p class=mut>Scores: {ins['scores']} &middot; thresholds: {ins['thresholds']}</p>")
    o.append("</div>")
    if ex:
        o.append(f"<div class=card><h2>Source evidence</h2><p>Source <b>{E(ex['source_id'])}</b> &middot; "
                 f"<a href='{E(ex['final_url'])}'>{E(ex['final_url'])}</a> &middot; retrieved {E(ex['retrieved_at'] or '')} "
                 f"&middot; backend {E(ex['backend'] or '')} &middot; attempts {ex['attempts']}</p>"
                 f"<img class=shot src=/evidence/{ex['exec_id']}/page.png>"
                 f"<p><a href=/evidence/{ex['exec_id']}/page.txt>captured page text</a></p></div>")
    if ver:
        o.append("<div class=card><h2>Independent verification: " + ("passed" if ver["passed"] else "FAILED") + "</h2><pre>"
                 + E("\n".join(json.loads(ver["notes_json"]))) + "</pre></div>")
    o.append("<div class=card><h2>Step log</h2><table>" + "".join(
        f"<tr><td class=mut>{E(s['ts'])}</td><td>{E(s['step'])}</td><td>{E(s['detail'] or '')}</td></tr>" for s in v["steps"])
        + "</table></div>")
    return page("".join(o), rid)


def export_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(["record_id", "credential_id", "claimed_name", "claimed_course", "claimed_date", "status", "reason"])
        for v in pipeline.all_views():
            r = v["record"]
            w.writerow([r["record_id"], r["credential_id"], r["candidate_name"], r["claimed_course"],
                        r["claimed_date"], v["status"], v["reason"]])
        z.writestr("results.csv", out.getvalue())
        if os.path.isdir(config.EVIDENCE_DIR):
            for root, _, files in os.walk(config.EVIDENCE_DIR):
                for f in files:
                    p = os.path.join(root, f)
                    z.write(p, os.path.join("evidence", os.path.relpath(p, config.EVIDENCE_DIR)))
        rep = os.path.join(config.BASE, "scenario_report.md")
        if os.path.isfile(rep):
            z.write(rep, "scenario_report.md")
    return buf.getvalue()


class H(BaseHTTPRequestHandler):
    def send(self, body, code=200, ctype="text/html; charset=utf-8", extra=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, msg=""):
        self.send_response(303)
        self.send_header("Location", "/" + (("?msg=" + up.quote(msg)) if msg else ""))
        self.end_headers()

    def do_GET(self):
        u = up.urlparse(self.path)
        if u.path == "/":
            return self.send(dashboard(up.parse_qs(u.query).get("msg", [""])[0]))
        m = re.fullmatch(r"/record/(REC-\d+)", u.path)
        if m:
            p = record_page(m.group(1))
            return self.send(p) if p else self.send("not found", 404)
        m = re.fullmatch(r"/evidence/(EXE-\d+)/(page\.png|page\.txt)", u.path)
        if m:
            f = os.path.join(config.EVIDENCE_DIR, m.group(1), m.group(2))
            if os.path.isfile(f):
                ct = "image/png" if f.endswith(".png") else "text/plain; charset=utf-8"
                return self.send(open(f, "rb").read(), ctype=ct)
            return self.send("not found", 404)
        if u.path == "/export.zip":
            return self.send(export_zip(), ctype="application/zip",
                             extra={"Content-Disposition": "attachment; filename=evidence_bundle.zip"})
        self.send("not found", 404)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        f = {k: v[0] for k, v in up.parse_qs(self.rfile.read(n).decode("utf-8")).items()}
        if self.path == "/submit":
            clean, errs = validation.validate(f)
            if errs:
                return self.redirect("Not submitted: " + "; ".join(errs))
            pipeline.submit_record(clean)
            return self.redirect("Proposal created. Approve it below to run the check.")
        if self.path == "/bulk":
            ok, bad = 0, []
            for i, row in enumerate(csv.DictReader(io.StringIO(f.get("csv", ""))), start=1):
                clean, errs = validation.validate(row)
                if errs:
                    bad.append(f"row {i}: {'; '.join(errs)}")
                else:
                    pipeline.submit_record(clean)
                    ok += 1
            return self.redirect(f"{ok} row(s) proposed. " + (" | ".join(bad) if bad else ""))
        if self.path == "/approve":
            ids = ([r["plan_id"] for r in db.q("SELECT plan_id FROM plans WHERE status='PENDING_APPROVAL'")]
                   if f.get("all") else [f.get("plan_id", "")])
            with RUN_LOCK:
                for pid in ids:
                    if pipeline.approve(pid):
                        pipeline.execute(pid)
            return self.redirect(f"Ran {len(ids)} approved check(s).")
        if self.path == "/reject":
            pipeline.reject(f.get("plan_id", ""))
            return self.redirect("Rejected. Nothing was run.")
        if self.path == "/tamper":
            with RUN_LOCK:
                pipeline.demo_tamper(f.get("plan_id", ""))
            return self.redirect("Demo: the plan was altered after approval and the executor refused to run it.")
        self.send("not found", 404)

    def log_message(self, *a):
        pass


def main():
    db.init()
    if mock_issuer.start_background():
        print(f"Mock issuer (demo data) started on port {config.MOCK_PORT}")
    print(f"App: http://localhost:{config.APP_PORT}   (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", config.APP_PORT), H).serve_forever()


if __name__ == "__main__":
    main()
