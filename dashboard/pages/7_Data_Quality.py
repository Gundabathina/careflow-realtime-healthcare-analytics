"""Page 7: Data Quality -- pipeline health across every phase of the platform.

Reads only the existing pipeline reports already written by each phase
(Bronze manifest, Silver/Gold quality reports, PostgreSQL validation,
dbt test results, Airflow run summary) -- never re-runs a check itself.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pandas as pd
import streamlit as st

from dashboard.components.charts import horizontal_bar_chart
from dashboard.components.layout import configure_page, render_empty_state, render_header, render_section_title
from dashboard.queries import get_data_quality_summary

configure_page("Data Quality")
render_header("Data Quality", "Pipeline health across Bronze, Silver, Gold, PostgreSQL, dbt, and Airflow.")

summary = get_data_quality_summary()
stages = summary.get("stages", [])

col1, col2 = st.columns(2)
with col1:
    st.metric("Last Pipeline Run", summary.get("last_pipeline_run") or "Unavailable")
with col2:
    st.metric("Last Successful Pipeline Run", summary.get("last_successful_pipeline_run") or "Unavailable")

st.divider()
render_section_title("Pipeline Stage Status")

for stage in stages:
    with st.container(border=True):
        header_col, status_col = st.columns([3, 1])
        with header_col:
            st.markdown(f"**{stage['stage']}**")
            if stage.get("generated_at_utc"):
                st.caption(f"Last updated: {stage['generated_at_utc']}")
        with status_col:
            if not stage.get("available"):
                st.warning("No report found")
            elif stage.get("failed") or (stage.get("final_status") == "failed"):
                st.error("Issues found")
            elif stage.get("warnings"):
                st.warning("Warnings")
            else:
                st.success("Healthy")

        if not stage.get("available"):
            continue

        metric_cols = st.columns(4)
        metric_labels = [
            ("passed", "Passed"), ("processed", "Processed"), ("ingested", "Ingested"),
            ("warnings", "Warnings"), ("failed", "Failed"), ("skipped", "Skipped"), ("blocked", "Blocked"),
        ]
        shown = 0
        for key, label in metric_labels:
            if key in stage and shown < 4:
                with metric_cols[shown]:
                    st.metric(label, stage[key])
                shown += 1

st.divider()
render_section_title("Checks Passed / Warnings / Failed / Skipped by Stage")

rows = []
for stage in stages:
    if not stage.get("available"):
        continue
    rows.append({
        "stage": stage["stage"],
        "passed": stage.get("passed") or stage.get("processed") or stage.get("ingested") or 0,
        "warnings": stage.get("warnings") or 0,
        "failed": stage.get("failed") or 0,
        "skipped": stage.get("skipped") or stage.get("blocked") or 0,
    })

if rows:
    chart_df = pd.DataFrame(rows).melt(id_vars="stage", var_name="status", value_name="count")
    fig = horizontal_bar_chart(chart_df[chart_df["status"] == "passed"], "stage", "count", "Checks Passed by Stage", "Checks", "Stage")
    if fig:
        st.plotly_chart(fig, width="stretch")
    else:
        render_empty_state()

    with st.expander("Full stage detail"):
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
else:
    render_empty_state("No pipeline reports found yet.")
