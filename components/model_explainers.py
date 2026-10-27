"""Plain-English "How this works" panels for the Predictive Intelligence tabs."""

from __future__ import annotations

import html

import streamlit as st

EXPLAIN = {
    "forecast": dict(
        title="Workforce forecast",
        question="How many people will we have in each of the next 6 months, and how far from plan will we be?",
        inputs="Monthly headcount, hires and exits since Oct 2023; offers already accepted with a future joining "
               "date; the approved plan (budget) headcount.",
        method="Six methods compete, including **SARIMA**. The **flow model** is used: it works like a bank balance: "
               "next month = this month − expected exits + expected hires. Exits use each calendar month's typical "
               "exit rate (rescaled to the last 12 months); hires use the accepted-offer pipeline, then the recent "
               "hiring pace.",
        tested="**Rolling backtest:** pretend it is Sep 2025, Dec 2025 and Mar 2026, forecast 6 months using only "
               "data up to then, and compare with what actually happened. A challenger replaces the flow model only "
               "if it cuts the average miss (MAE) by more than 5% — SARIMA came within 2.5%, a statistical tie.",
        read="The dashed line is the most likely path; the shaded band is where the actual figure should fall about "
             "8 times in 10. Department bars show the expected gap to plan at the horizon.",
        flow="forecast",
    ),
    "attrition": dict(
        title="Attrition risk",
        question="Which groups are more likely to resign in the next 6 months, and what is that associated with?",
        inputs="For every employee at each quarter-end: tenure, level, contract, latest engagement / manager-support "
               "/ career-growth scores, pay vs peers, last rating, months since promotion, manager change, team size, "
               "training, department attrition, business unit, location, hiring source.",
        method="**Logistic regression** turns these inputs into a probability of leaving within 6 months. "
               "Gradient boosting was also trained; it was not meaningfully better, so the simpler model — whose "
               "drivers can be read directly — was kept.",
        tested="**Time split:** trained on quarter-ends Mar 2024–Sep 2025, tested on everyone employed on "
               "31 Mar 2026 against who actually resigned by 30 Sep 2026. Only information known on each date is "
               "used (no leakage).",
        read="**ROC-AUC 0.5 = guessing, 1.0 = perfect.** ~0.64 means the model ranks risk better than chance but "
             "misses many individual leavers. Use it for team-level priorities, never for decisions about a person.",
        flow="attrition",
    ),
    "drivers": dict(
        title="Attrition drivers — what-if",
        question="If we improved engagement, closed pay gaps or promoted long-waiting people, how many fewer would "
                 "leave — and what would that do to headcount and cost?",
        inputs="The current workforce (score date) with each person's model inputs, the trained attrition model, "
               "current salaries (for pay-gap cost) and the scenario engine's baseline.",
        method="Your slider changes are applied to the model's **inputs** for everyone in scope, the same trained "
               "model re-scores them, and the change in expected leavers is converted into a change in annual "
               "attrition that feeds the headcount projection.",
        tested="Uses the validated attrition model; tests check that the baseline reproduces current scores and that "
               "better engagement never increases expected leavers.",
        read="Treat results as **direction and rough size**. The model learns associations from history, not proven "
             "causes — a real intervention may do more or less.",
        flow=None,
    ),
    "ttf": dict(
        title="Hiring & time to fill",
        question="How long will a new role take to fill, so we can open it early enough to meet the plan?",
        inputs="Every requisition: job family, level, opening date, filled date — or still open / cancelled.",
        method="**Kaplan-Meier survival curves** estimate the share of roles still unfilled after each number of "
               "days, counting still-open roles correctly. Point-prediction models (segment median, gradient "
               "boosting) were also tested.",
        tested="**Chronological hold-out:** trained on roles opened before 30 Jun 2025, tested on 725 roles opened in "
               "the next six months.",
        read="P50 = half of roles are filled by this day; P80 = 8 in 10 are. No point model beat the simple median, "
             "so planning uses these ranges: open a role P80 days + notice period before you need the person.",
        flow="ttf",
    ),
    "scenarios": dict(
        title="Scenario explorer",
        question="What happens to headcount and cost if attrition, hiring pace, hiring delays or pay change?",
        inputs="Per department: current headcount, last-12-month exit rate, last-6-month hiring pace, cost per head, "
               "the accepted-offer pipeline and the plan.",
        method="**Deterministic arithmetic**, month by month and department by department: exits = headcount × "
               "(exit rate + your change) ÷ 12; hires = max(pipeline, pace × your change) after any delay; cost = "
               "headcount × cost per head × (1 + inflation).",
        tested="Not a trained model, so there is nothing to backtest; tests check the same inputs always give the "
               "same answer and that each lever moves results in the right direction.",
        read="Compare your scenario with the baseline line and the plan. Every number can be traced to the "
             "assumptions table below.",
        flow=None,
    ),
    "monitoring": dict(
        title="Model monitoring",
        question="Are the models still good enough to trust, and when were they last trained?",
        inputs="Stored evaluation results for every model: training window, test window, sample sizes, metrics, "
               "assumptions and limitations.",
        method="Each model is compared against a simple baseline on data it never saw. Models are retrained "
               "deliberately (not on page load) after each data refresh.",
        tested="This tab *is* the evaluation record — backtest errors, hold-out scores, calibration.",
        read="If a model stops beating its baseline after a refresh, fall back to the baseline and investigate.",
        flow=None,
    ),
}


def explain(key: str) -> None:
    """Render the 'How this works' panel for a tab."""
    e = EXPLAIN[key]

    def cell(label: str, text: str) -> str:
        import re

        body = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", html.escape(text))
        return f"<div class='pl-ex-cell'><div class='pl-ex-l'>{label}</div><div class='pl-ex-t'>{body}</div></div>"

    st.markdown(
        "<div class='pl-ex'><div class='pl-ex-h'>How this works</div><div class='pl-ex-grid'>"
        + cell("The question", e["question"]) + cell("What goes in", e["inputs"]) + cell("How it works", e["method"])
        + cell("How it was tested", e["tested"]) + cell("How to read it", e["read"]) + "</div></div>",
        unsafe_allow_html=True)
    if e.get("flow"):
        from views.how_it_works import MODEL_FLOWS

        with st.expander("See the model flow", icon=":material/account_tree:"):
            st.graphviz_chart(MODEL_FLOWS[e["flow"]], width="stretch")
