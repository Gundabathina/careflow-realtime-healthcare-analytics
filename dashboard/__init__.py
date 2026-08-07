"""CareFlow Analytics -- Streamlit dashboard (Phase 5A).

Reads only from the PostgreSQL warehouse's dbt reporting layer
(``careflow_dbt_mart``). Never writes to the warehouse, never touches
Raw/Bronze/Silver/Gold files.
"""
