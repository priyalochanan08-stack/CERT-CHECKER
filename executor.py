"""Executor: opens the page and captures evidence. It NEVER gives a verdict."""
import datetime
import json
import os
import time

from playwright.sync_api import sync_playwright

BLOCK_HINTS = ["captcha", "verify you are human", "are you a robot", "access denied",
               "log in to view", "sign in to view"]
BACKEND = "local-playwright"


def capture(url, outdir, timeout_ms=15000, retries=2):
    """Returns dict: page_state (OK|BLOCKED|TIMEOUT|ERROR), text, screenshot, final_url,
    http_status, attempts, error, retrieved_at. Never raises."""
    os.makedirs(outdir, exist_ok=True)
    res = {"page_state": "ERROR", "text": "", "final_url": url, "http_status": None,
           "attempts": 0, "error": None, "backend": BACKEND}
    args = ["--no-sandbox"] if os.environ.get("CERT_NO_SANDBOX") else []
    for attempt in range(1, retries + 2):
        res["attempts"] = attempt
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(args=args)
                page = browser.new_page(viewport={"width": 1100, "height": 800})
                resp = page.goto(url, wait_until="load", timeout=timeout_ms)
                res["http_status"] = resp.status if resp else None
                res["text"] = page.inner_text("body")
                res["final_url"] = page.url
                page.screenshot(path=os.path.join(outdir, "page.png"), full_page=True)
                browser.close()
            res["error"] = None
            low = res["text"].lower()
            res["page_state"] = "BLOCKED" if any(h in low for h in BLOCK_HINTS) else "OK"
            break
        except Exception as e:  # timeouts, connection refused, browser crash
            res["error"] = f"{type(e).__name__}: {str(e)[:200]}"
            res["page_state"] = "TIMEOUT" if "Timeout" in type(e).__name__ else "ERROR"
            time.sleep(1)
    res["retrieved_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    with open(os.path.join(outdir, "page.txt"), "w", encoding="utf-8") as f:
        f.write(res["text"])
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in res.items() if k != "text"}, f, indent=2)
    return res
