"""Plotly chart helpers with one consistent, restrained visual style."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

NAVY, TEAL, SLATE = "#172554", "#0F766E", "#475569"
AMBER, RED, GREEN = "#B45309", "#B91C1C", "#15803D"
GRID, BORDER, LIGHT = "#EEF2F6", "#E2E8F0", "#94A3B8"
SEQ = ["#E6F4F1", "#99D5CA", "#4FB3A3", "#0F766E", "#0B4F4A"]
PALETTE = [NAVY, TEAL, "#64748B", "#5EAAA8", "#1E40AF", AMBER, "#94A3B8", "#0EA5E9", "#7C3AED", "#BE185D"]

pio.templates["peoplelens"] = go.layout.Template(
    layout=dict(
        font=dict(family="Inter, Segoe UI, Helvetica, Arial, sans-serif", size=12, color="#1E293B"),
        colorway=PALETTE, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=8, r=8, t=36, b=8), title=dict(font=dict(size=13, color=NAVY), x=0, xanchor="left"),
        xaxis=dict(gridcolor=GRID, linecolor=BORDER, zeroline=False, ticks="", automargin=True),
        yaxis=dict(gridcolor=GRID, linecolor=BORDER, zeroline=False, ticks="", automargin=True),
        legend=dict(orientation="h", yanchor="top", y=-0.14, xanchor="left", x=0, font=dict(size=11),
                    bgcolor="rgba(0,0,0,0)"),
        hoverlabel=dict(bgcolor="white", bordercolor=BORDER, font=dict(color="#0F172A")),
        bargap=0.28,
    )
)
pio.templates.default = "peoplelens"
CONFIG = {"displaylogo": False, "modeBarButtonsToRemove": ["lasso2d", "select2d", "autoScale2d"],
          "toImageButtonOptions": {"format": "png", "scale": 2}}


def finish(fig: go.Figure, title: str | None = None, height: int = 320, pct_y: bool = False,
           inr_y: bool = False) -> go.Figure:
    fig.update_layout(height=height, title=title)
    if fig.layout.xaxis.title.text:  # keep the legend clear of the x-axis title
        fig.update_layout(legend=dict(y=-0.3))
    if pct_y:
        fig.update_yaxes(tickformat=".0%")
    if inr_y:
        fig.update_yaxes(tickprefix="₹")
    return fig


def line(df: pd.DataFrame, x: str, ys: list[str], names: list[str] | None = None, title: str | None = None,
         colors: list[str] | None = None, dash: list[str] | None = None, height: int = 320, pct: bool = False,
         hover_fmt: str = ",.0f") -> go.Figure:
    fig = go.Figure()
    for k, y in enumerate(ys):
        fig.add_trace(go.Scatter(
            x=df[x], y=df[y], mode="lines+markers" if len(df) < 40 else "lines", name=(names or ys)[k],
            line=dict(color=(colors or PALETTE)[k % len(PALETTE)], width=2.2, dash=(dash or ["solid"] * len(ys))[k]),
            marker=dict(size=5), hovertemplate=f"%{{x|%b %Y}}<br>%{{y:{'.1%' if pct else hover_fmt}}}<extra>"
                                                f"{(names or ys)[k]}</extra>"))
    fig = finish(fig, title, height, pct_y=pct)
    if pct:
        fig.update_yaxes(tickformat=".1%")
    return fig


def band(fig: go.Figure, x, lower, upper, name: str = "80% interval", color: str = "rgba(15,118,110,0.14)"):
    fig.add_trace(go.Scatter(x=list(x) + list(x)[::-1], y=list(upper) + list(lower)[::-1], fill="toself",
                             fillcolor=color, line=dict(width=0), mode="lines", name=name, hoverinfo="skip"))
    return fig


def bar(df: pd.DataFrame, x: str, y: str, title: str | None = None, horizontal: bool = False, color: str = TEAL,
        height: int = 320, pct: bool = False, text_fmt: str | None = None, highlight: list | None = None,
        error: tuple[str, str] | None = None, hover_fmt: str = ",.0f") -> go.Figure:
    colors = [AMBER if highlight and v in highlight else color for v in df[x]] \
        if highlight else color
    err = None
    if error:
        lo, hi = error
        err = dict(type="data", symmetric=False, array=(df[hi] - df[y]).tolist(),
                   arrayminus=(df[y] - df[lo]).tolist(), color=LIGHT, thickness=1.2, width=3)
    fmt = ".1%" if pct else hover_fmt
    if horizontal:
        fig = go.Figure(go.Bar(x=df[y], y=df[x], orientation="h", marker_color=colors, error_x=err,
                               text=df[y].map(lambda v: format(v, text_fmt)) if text_fmt else None,
                               textposition="outside", cliponaxis=False,
                               hovertemplate=f"%{{y}}<br>%{{x:{fmt}}}<extra></extra>"))
        fig.update_yaxes(autorange="reversed")
        if pct:
            fig.update_xaxes(tickformat=".0%")
        return finish(fig, title, height)
    fig = go.Figure(go.Bar(x=df[x], y=df[y], marker_color=colors, error_y=err,
                           text=df[y].map(lambda v: format(v, text_fmt)) if text_fmt else None,
                           textposition="outside", cliponaxis=False,
                           hovertemplate=f"%{{x}}<br>%{{y:{fmt}}}<extra></extra>"))
    return finish(fig, title, height, pct_y=pct)


def grouped_bar(df: pd.DataFrame, x: str, ys: list[str], names: list[str], title: str | None = None,
                colors: list[str] | None = None, height: int = 320, barmode: str = "group", pct: bool = False):
    fig = go.Figure()
    for k, y in enumerate(ys):
        fig.add_trace(go.Bar(x=df[x], y=df[y], name=names[k], marker_color=(colors or PALETTE)[k],
                             hovertemplate=f"%{{x}}<br>%{{y:{'.1%' if pct else ',.0f'}}}<extra>{names[k]}</extra>"))
    fig.update_layout(barmode=barmode)
    return finish(fig, title, height, pct_y=pct)


def histogram(values, title: str | None = None, nbins: int = 30, color: str = TEAL, height: int = 300,
              xlabel: str | None = None) -> go.Figure:
    fig = go.Figure(go.Histogram(x=values, nbinsx=nbins, marker_color=color, marker_line=dict(color="white", width=1),
                                 hovertemplate="%{x}<br>%{y:,} employees<extra></extra>"))
    fig.update_xaxes(title=xlabel)
    return finish(fig, title, height)


def heatmap(z: pd.DataFrame, title: str | None = None, height: int = 320, fmt: str = ",.0f", pct: bool = False,
            colorscale=None, zmid=None, zmin=None, zmax=None) -> go.Figure:
    text = z.map(lambda v: "" if pd.isna(v) else (f"{v:.0%}" if pct else format(v, fmt))).values
    fig = go.Figure(go.Heatmap(z=z.values, x=[str(c) for c in z.columns], y=[str(i) for i in z.index],
                               colorscale=colorscale or [[0, "#F0FDFA"], [0.5, "#5EAAA8"], [1, "#0B4F4A"]],
                               text=text, texttemplate="%{text}", textfont=dict(size=11), showscale=False,
                               zmid=zmid, zmin=zmin, zmax=zmax, hovertemplate="%{y} · %{x}<br>%{text}<extra></extra>", xgap=2, ygap=2))
    fig.update_yaxes(autorange="reversed")
    return finish(fig, title, height)


def funnel(stages, values, title: str | None = None, height: int = 320) -> go.Figure:
    fig = go.Figure(go.Funnel(y=stages, x=values, texttemplate="%{value:,} · %{percentInitial:.0%}",
                              marker=dict(color=[NAVY, "#1E3A8A", "#1D4E89", "#0F766E", "#0D9488", "#14B8A6"][:len(values)]),
                              connector=dict(line=dict(color=BORDER, width=1))))
    return finish(fig, title, height)


def tornado(df: pd.DataFrame, label: str, low: str, high: str, base: float, title: str | None = None,
            height: int = 300) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Bar(y=df[label], x=df[low] - base, base=base, orientation="h", name="Low assumption",
                         marker_color="#99D5CA", hovertemplate="%{y}<br>₹%{x:,.0f}<extra>low</extra>"))
    fig.add_trace(go.Bar(y=df[label], x=df[high] - base, base=base, orientation="h", name="High assumption",
                         marker_color=TEAL, hovertemplate="%{y}<br>₹%{x:,.0f}<extra>high</extra>"))
    fig.add_vline(x=base, line=dict(color=NAVY, width=1.5, dash="dot"))
    fig.update_layout(barmode="overlay")
    return finish(fig, title, height)
