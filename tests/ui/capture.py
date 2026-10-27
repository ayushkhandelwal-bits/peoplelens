"""Headless browser check of the running app: loads pages, reports Streamlit
exceptions and saves full-page screenshots.

    python tests/ui/capture.py http://localhost:8501 out_dir [page-path ...]
"""

import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

PAGES = ["", "talent-lifecycle", "predictive-intelligence", "planning-business-insights",
         "ask", "data-governance"]


def wait_ready(page, timeout=150):
    """Wait until Streamlit has finished the script run: no skeletons and a stable DOM."""
    t0, last, stable = time.time(), -1, 0
    time.sleep(2)
    while time.time() - t0 < timeout:
        skel = page.locator("[data-testid='stSkeleton']").count()
        size = page.evaluate("document.body.innerHTML.length")
        running = page.locator("[data-testid='stStatusWidget'] button").count()
        if skel == 0 and not running and size == last:
            stable += 1
            if stable >= 3:
                return True
        else:
            stable = 0
        last = size
        time.sleep(1)
    return False


def main():
    base, out = sys.argv[1], Path(sys.argv[2])
    pages = sys.argv[3:] or PAGES
    out.mkdir(parents=True, exist_ok=True)
    problems = 0
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport={"width": 1440, "height": int(__import__("os").environ.get("SHOT_H", "900"))}, device_scale_factor=1)
        page = ctx.new_page()
        for path in pages:
            url = f"{base}/{path}"
            page.goto(url)
            ok = wait_ready(page)
            excs = page.locator("[data-testid='stException']")
            errs = page.locator("[data-testid='stAlertContentError']")
            n_exc, n_err = excs.count(), errs.count()
            print(f"{path}: ready={ok} exceptions={n_exc} error_alerts={n_err}")
            for i in range(min(n_exc, 3)):
                print("   EXC:", excs.nth(i).inner_text()[:800])
            for i in range(min(n_err, 3)):
                print("   ERR:", errs.nth(i).inner_text()[:400])
            problems += n_exc
            name = (path or 'workforce-explorer').replace('/', '_')
            page.screenshot(path=str(out / f"{name}.png"), full_page=True)
            tabs = page.get_by_role("tab")
            for t in range(1, tabs.count()):
                tabs.nth(t).click()
                wait_ready(page, 60)
                excs = page.locator("[data-testid='stException']")
                if excs.count():
                    problems += excs.count()
                    print(f"   tab {t}: EXC", excs.first.inner_text()[:900])
                page.screenshot(path=str(out / f"{name}_tab{t}.png"), full_page=True)
        b.close()
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
