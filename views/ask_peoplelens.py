"""Ask PeopleLens — natural-language questions answered from SQL templates and
validated model artefacts (no language model, no paid API)."""

from __future__ import annotations

import streamlit as st

from components import data
from components.ask_peoplelens import assistant, render_answer
from components.kpi_cards import caption, page_header
from src.assistant.answer_generator import EXAMPLES


def render() -> None:
    v = data.data_version()
    page_header("Workspace", "Ask PeopleLens",
                "Ask a question in plain English. PeopleLens recognises the intent, applies the filters it finds, "
                "runs a curated read-only query and explains the result — showing exactly how it got there.")
    a = assistant(v)
    if "ask_history" not in st.session_state:
        st.session_state.ask_history = []
    pending = st.session_state.pop("ask_question", None)
    with st.form("ask_main", clear_on_submit=False):
        q = st.text_input("Your question", value=pending or "", placeholder=EXAMPLES[0])
        submitted = st.form_submit_button("Ask", type="primary", icon=":material/send:")
    st.markdown("**Examples**")
    cols = st.columns(2)
    for i, ex in enumerate(EXAMPLES):
        if cols[i % 2].button(ex, key=f"ex_{i}", type="tertiary"):
            pending = ex
    question = pending if pending and not submitted else (q if submitted else None)
    if question:
        with st.spinner("Querying…"):
            ans = a.ask(question)
        st.session_state.ask_history.insert(0, ans)
        st.session_state.ask_history = st.session_state.ask_history[:6]
    if not st.session_state.ask_history:
        caption("Supported topics: headcount, joiners, attrition, retention and early attrition, time to hire/fill, "
                "funnel and sources, plan vs actual, budget variance, turnover cost, engagement, compensation, "
                "performance, promotions, span of control, exit reasons, forecasts, attrition drivers and hiring "
                "demand. Individual-level and high-stakes requests are declined.")
        return
    latest = st.session_state.ask_history[0]
    with st.container(border=True):
        st.markdown(f"#### {latest.question}")
        render_answer(latest)
    if len(st.session_state.ask_history) > 1:
        with st.expander("Earlier questions in this session"):
            for prev in st.session_state.ask_history[1:]:
                st.markdown(f"**{prev.question}**")
                st.markdown(prev.text.split("\n\n")[0] if prev.text else "")
    caption("Ask PeopleLens never executes text from the question as SQL: it fills parameters of predefined "
            "templates and runs them on a read-only connection. Answers are aggregates of synthetic data.")
