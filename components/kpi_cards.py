"""KPI cards, number formatting and insight callouts."""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from src.governance.metric_dictionary import METRICS


def fmt_int(v) -> str:
    return "—" if v is None or pd.isna(v) else f"{int(round(v)):,}"


def fmt_pct(v, signed: bool = False, digits: int = 1) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.{digits}%}" if signed else f"{v:.{digits}%}"


def fmt_inr(v, signed: bool = False) -> str:
    if v is None or pd.isna(v):
        return "—"
    s = "+" if signed and v > 0 else ""
    a = abs(v)
    sign = "-" if v < 0 else s
    if a >= 1e7:
        return f"{sign}₹{a / 1e7:,.2f} Cr"
    if a >= 1e5:
        return f"{sign}₹{a / 1e5:,.1f} L"
    return f"{sign}₹{a:,.0f}"


def tooltip(key: str, extra: str = "") -> str:
    m = METRICS.get(key)
    if not m:
        return extra
    return f"{m.definition}\n\nFormula: {m.formula}\n\nSource: {m.source_tables}" + (f"\n\n{extra}" if extra else "")


def metric(col, label: str, value: str, delta: str | None = None, key: str | None = None,
           inverse: bool = False, help_extra: str = "") -> None:
    col.metric(label, value, delta=delta, delta_color="inverse" if inverse else "normal",
               help=tooltip(key, help_extra) if key else (help_extra or None), border=False)


def insight(text: str, kind: str = "") -> None:
    """Callout; ``text`` may contain **bold** markers. Escaped for safety."""
    import re

    safe = html.escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<span class='pl-k'>\1</span>", safe)
    st.markdown(f"<div class='pl-insight {kind}'>{safe}</div>", unsafe_allow_html=True)


def page_header(eyebrow: str, title: str, lede: str) -> None:
    role = st.session_state.get("role") or "Analyst"
    badge = (f"<span class='pl-role-badge {role.lower()}' title='Access role — change it in the sidebar'>"
             f"{'🔒 ' if role == 'Viewer' else ''}Viewing as {html.escape(role)}</span>")
    st.markdown(f"<div class='pl-head-row'><div class='pl-eyebrow'>{html.escape(eyebrow)}</div>{badge}</div>",
                unsafe_allow_html=True)
    st.title(title)
    st.markdown(f"<div class='pl-lede'>{html.escape(lede)}</div>", unsafe_allow_html=True)


def caption(text: str) -> None:
    st.markdown(f"<div class='pl-caption'>{html.escape(text)}</div>", unsafe_allow_html=True)


def chips(items: list[str]) -> None:
    st.markdown("".join(f"<span class='pl-chip'>{html.escape(i)}</span>" for i in items), unsafe_allow_html=True)
