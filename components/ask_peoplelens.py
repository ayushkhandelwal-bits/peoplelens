"""Rendering for Ask PeopleLens answers."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from components import charts
from components.kpi_cards import chips
from src.assistant.answer_generator import Answer, Assistant


@st.cache_resource(show_spinner=False)
def assistant(v: str) -> Assistant:
    return Assistant()


def _chart(ev) -> go.Figure | None:
    spec, df = ev.chart, ev.table
    if not spec or df is None or df.empty:
        return None
    kind, x, y = spec.get("type"), spec.get("x"), spec.get("y")
    if kind == "funnel":
        return charts.funnel(df[y].tolist(), df[x].tolist(), height=300)
    if kind == "line":
        ys = y if isinstance(y, list) else [y]
        d = df.copy()
        if x == "month":
            d[x] = pd.to_datetime(d[x])
        return charts.line(d, x, ys, height=300)
    if kind == "bar" and x in df and (y in df):
        d = df.dropna(subset=[y]).head(25)
        if spec.get("color") and spec["color"] in d:
            fig = go.Figure()
            for k, (name, g) in enumerate(d.groupby(spec["color"])):
                fig.add_trace(go.Bar(x=g[x].astype(str), y=g[y], name=str(name),
                                     marker_color=charts.PALETTE[k % len(charts.PALETTE)]))
            return charts.finish(fig, None, 300, pct_y=spec.get("pct", False))
        horizontal = len(d) > 7
        return charts.bar(d.assign(**{x: d[x].astype(str)}), x, y, horizontal=horizontal, pct=spec.get("pct", False),
                          height=max(300, 22 * len(d)) if horizontal else 300)
    return None


def render_answer(ans: Answer, show_sql: bool = True) -> None:
    if ans.status in ("refused", "unsupported", "error"):
        kind = {"refused": "warning", "unsupported": "info", "error": "error"}[ans.status]
        getattr(st, kind)(ans.text)
        if ans.suggestions:
            st.caption("Try: " + " · ".join(f"“{s}”" for s in ans.suggestions))
        return
    ev = ans.evidence
    chips([f"Intent: {ans.intent_label or ans.intent}", f"Confidence: {ans.parsed.confidence:.0%}",
           f"Source: {ev.source}"] if ev else [])
    if ans.filters_text:
        st.caption("Interpreted as — " + ans.filters_text)
    st.markdown(ans.text)
    if ans.metric_definition:
        st.caption(ans.metric_definition)
    if ev is None:
        return
    fig = _chart(ev)
    table = ev.table.copy()
    for c in table.select_dtypes("float").columns:
        table[c] = table[c].round(3 if table[c].abs().max() < 10 else 1)
    c1, c2 = st.columns([3, 2]) if fig is not None else (st.container(), None)
    if fig is not None:
        with c1:
            st.plotly_chart(fig, config=charts.CONFIG, key=f"ask_fig_{hash(ans.question)}")
        with c2:
            st.dataframe(table, hide_index=True, width="stretch", height=300)
    else:
        st.dataframe(table, hide_index=True, width="stretch")
    if show_sql:
        with st.expander("Query template and parameters", icon=":material/code:"):
            if ev.sql:
                st.code(ev.sql, language="sql")
                st.caption("Parameters (bound, never concatenated): "
                           + ", ".join(f"{k} = {v}" for k, v in ev.params.items()))
            else:
                st.caption("Answered from a saved, validated model artefact rather than a SQL template.")
