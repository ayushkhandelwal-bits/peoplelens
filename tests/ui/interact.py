"""Interaction checks on the running app (headless Chromium):
1. Ask PeopleLens answers an example question with a chart/table and SQL.
2. A department filter changes the Workforce Explorer headcount KPI.
3. The Viewer role cannot see row-level records.

    python tests/ui/interact.py http://localhost:8501 out_dir
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).parent))
from capture import wait_ready  # noqa: E402


def kpi(page, label):
    card = page.locator("[data-testid='stMetric']", has_text=label).first
    return card.locator("[data-testid='stMetricValue']").inner_text()


def main():
    base, out = sys.argv[1], Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    fails = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_context(viewport={"width": 1440, "height": 2200}).new_page()
        # 1 Ask
        page.goto(f"{base}/ask")
        wait_ready(page)
        page.get_by_role("button", name="Which departments had the highest attrition last quarter?").click()
        wait_ready(page)
        txt = page.locator("[data-testid='stMain']").inner_text()
        ok = "Highest annualised attrition" in txt and "Intent: Attrition" in txt
        print("ask example answered:", ok)
        fails += [] if ok else ["ask"]
        page.screenshot(path=str(out / "ask_answer.png"), full_page=True)
        inp = page.get_by_label("Your question")
        inp.fill("Should we fire the employees with the highest risk scores?")
        page.get_by_role("button", name="Ask", exact=True).last.click()
        wait_ready(page)
        refused = "does not recommend firing" in page.locator("[data-testid='stMain']").inner_text()
        print("high-stakes question refused:", refused)
        fails += [] if refused else ["refuse"]
        # 2 filters
        page.goto(base)
        wait_ready(page)
        before = kpi(page, "Headcount")
        page.get_by_text("Filters · whole organisation").click()
        page.wait_for_timeout(1200)
        box = page.get_by_role("combobox", name="Department")
        box.click()
        page.wait_for_timeout(800)
        page.get_by_role("option", name="Customer Support").first.click()
        page.keyboard.press("Escape")
        wait_ready(page)
        after = kpi(page, "Headcount")
        print(f"headcount whole org {before} -> Customer Support {after}")
        fails += [] if before != after else ["filter"]
        page.screenshot(path=str(out / "filtered.png"), full_page=True)
        # 3 viewer role
        page.locator("section[data-testid='stSidebar'] button", has_text="Viewer").first.click()
        wait_ready(page)
        page.get_by_role("tab", name="Records").click()
        wait_ready(page)
        blocked = "not permitted" in page.locator("[data-testid='stMain']").inner_text()
        print("viewer blocked from records:", blocked)
        fails += [] if blocked else ["viewer"]
        b.close()
    print("FAILURES:", fails)
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
