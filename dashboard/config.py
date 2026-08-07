"""Static configuration for the CareFlow Analytics dashboard.

No secrets live here -- PostgreSQL credentials are read from environment
variables only, in database.py, via the same
careflow.warehouse.postgres_client used by every other CareFlow
component.
"""

from __future__ import annotations

import sys
from pathlib import Path


def get_project_root() -> Path:
    """The CareFlow repository root (dashboard/config.py -> repo root)."""
    return Path(__file__).resolve().parents[1]


# Make the project's src/ importable (careflow.config, careflow.warehouse.*)
_SRC_PATH = str(get_project_root() / "src")
if _SRC_PATH not in sys.path:
    sys.path.insert(0, _SRC_PATH)

APP_TITLE = "CareFlow Analytics"
APP_SUBTITLE = "Hospital Operations & Patient Readmission Intelligence Platform"
PAGE_ICON = "🏥"

# The dashboard reads only from the dbt reporting layer -- never Raw,
# Bronze, Silver, or Gold files, and never careflow_dim/careflow_fact
# directly (those are the Python Gold loader's tables; careflow_dbt_mart
# is the governed, tested, documented reporting surface built on top of
# them in Phase 3C).
MART_SCHEMA = "careflow_dbt_mart"

# Streamlit cache TTLs (seconds). Short enough that a fresh Airflow run
# is reflected without a manual cache clear; long enough to avoid
# hammering PostgreSQL on every widget interaction.
QUERY_CACHE_TTL_SECONDS = 300
FILTER_OPTIONS_CACHE_TTL_SECONDS = 600

# Restricted PII tokens -- defense-in-depth check applied to every query
# result's columns before it is ever rendered or exported (see
# database.py's assert_no_restricted_columns). Mirrors the same
# restricted-PII list enforced by the dbt layer's own
# no_restricted_pii_in_public_models test (Phase 3C).
RESTRICTED_COLUMN_TOKENS = (
    "ssn", "passport", "drivers_license", "driver_license",
    "first_name", "middle_name", "last_name",
    "street_address", "address_line", "latitude", "longitude",
)

# Age-group midpoints used only for an *estimated* average-age KPI --
# mart_patient_population exposes only the age_group bucket (never an
# exact age or birth date), consistent with the dbt layer's PII rules.
AGE_GROUP_MIDPOINTS: dict[str, float] = {
    "0-17": 8.5,
    "18-34": 26.0,
    "35-49": 42.0,
    "50-64": 57.0,
    "65-79": 72.0,
    "80+": 85.0,
}

CHART_COLOR_SEQUENCE = [
    "#0B5FA5",  # primary clinical blue
    "#2E8B8B",  # teal
    "#6C5CE7",  # muted indigo
    "#00A896",  # sea green
    "#E17055",  # muted terracotta (accent, used sparingly)
    "#636E72",  # slate gray
    "#0984E3",  # secondary blue
    "#B2BEC3",  # light gray
]

POSITIVE_COLOR = "#0B8457"
NEGATIVE_COLOR = "#C0392B"
NEUTRAL_COLOR = "#636E72"
