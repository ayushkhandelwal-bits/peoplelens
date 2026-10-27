"""Formatted tables with CSV export."""

from __future__ import annotations

import pandas as pd
import streamlit as st


def show(df: pd.DataFrame, formats: dict | None = None, height: int | None = None, download: str | None = None,
         rename: dict | None = None, hide_index: bool = True) -> None:
    """Display a dataframe with column formats and an optional CSV download.

    ``formats`` maps column -> one of: "int", "pct", "pct_signed", "inr", "float1", "float2", "date".
    The download always contains the unformatted values.
    """
    formats = formats or {}
    cfg = {}
    for col, kind in formats.items():
        if col not in df.columns:
            continue
        label = (rename or {}).get(col, col.replace("_", " ").capitalize())
        if kind == "int":
            cfg[col] = st.column_config.NumberColumn(label, format="%d")
        elif kind in ("pct", "pct_signed"):
            cfg[col] = st.column_config.NumberColumn(label, format="percent")
        elif kind == "inr":
            cfg[col] = st.column_config.NumberColumn(label, format="₹ %.0f")
        elif kind == "float1":
            cfg[col] = st.column_config.NumberColumn(label, format="%.1f")
        elif kind == "float2":
            cfg[col] = st.column_config.NumberColumn(label, format="%.2f")
        elif kind == "date":
            cfg[col] = st.column_config.DateColumn(label, format="MMM YYYY")
    for col in df.columns:
        if col not in cfg:
            cfg[col] = st.column_config.Column((rename or {}).get(col, str(col).replace("_", " ").capitalize()))
    kwargs = {"height": height} if height else {}
    st.dataframe(df, column_config=cfg, hide_index=hide_index, width="stretch", **kwargs)
    if download:
        st.download_button("Download CSV", df.to_csv(index=False).encode("utf-8"), file_name=f"{download}.csv",
                           mime="text/csv", key=f"dl_{download}_{len(df)}", type="tertiary", icon=":material/download:")
