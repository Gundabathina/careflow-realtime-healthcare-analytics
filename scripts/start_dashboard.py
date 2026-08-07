#!/usr/bin/env python3
"""Start the CareFlow Analytics Streamlit dashboard.

streamlit (and plotly) are installed in an isolated .venv-dashboard
virtualenv rather than the project's main Python -- streamlit currently
requires pyarrow<25, which would downgrade the project's pinned
pyarrow>=25.0 if installed into the main environment (see
docs/dashboard_guide.md). This script resolves the dashboard's own
streamlit binary the same way scripts/run_dbt.py resolves dbt's: an
isolated-venv default, overridable via CAREFLOW_STREAMLIT_BIN.

Usage:
    PYTHONPATH=src python3 scripts/start_dashboard.py
    PYTHONPATH=src python3 scripts/start_dashboard.py --port 8502
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import get_project_root  # noqa: E402
from careflow.logging_config import get_logger  # noqa: E402
from careflow.warehouse.postgres_client import MissingCredentialsError, check_connectivity  # noqa: E402

logger = get_logger(__name__)

PROJECT_ROOT = get_project_root()
DEFAULT_STREAMLIT_BIN = PROJECT_ROOT / ".venv-dashboard" / "bin" / "streamlit"


def resolve_streamlit_executable() -> str:
    override = os.environ.get("CAREFLOW_STREAMLIT_BIN")
    if override:
        return override
    if DEFAULT_STREAMLIT_BIN.is_file():
        return str(DEFAULT_STREAMLIT_BIN)
    found = shutil.which("streamlit")
    return found or str(DEFAULT_STREAMLIT_BIN)


def streamlit_available() -> bool:
    override = os.environ.get("CAREFLOW_STREAMLIT_BIN")
    if override:
        return Path(override).is_file() or shutil.which(override) is not None
    return DEFAULT_STREAMLIT_BIN.is_file() or shutil.which("streamlit") is not None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=8501, help="Port to serve the dashboard on (default: 8501)")
    parser.add_argument(
        "--skip-db-check", action="store_true",
        help="Skip the PostgreSQL connectivity check before starting Streamlit",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not streamlit_available():
        logger.error(
            "streamlit executable not found (resolved to %s) -- create the isolated dashboard "
            "environment first (see docs/dashboard_guide.md), or set CAREFLOW_STREAMLIT_BIN.",
            resolve_streamlit_executable(),
        )
        return 1

    if not args.skip_db_check:
        try:
            ok, reason = check_connectivity()
        except MissingCredentialsError as exc:
            logger.error("PostgreSQL credentials not configured: %s", exc)
            return 1
        if not ok:
            logger.error("Could not reach the CareFlow PostgreSQL warehouse: %s", reason)
            return 1
        logger.info("PostgreSQL connectivity OK.")

    app_path = PROJECT_ROOT / "dashboard" / "app.py"
    cmd = [
        resolve_streamlit_executable(), "run", str(app_path),
        "--server.port", str(args.port),
    ]
    logger.info("Starting dashboard: %s", " ".join(cmd))
    logger.info("URL: http://localhost:%d", args.port)

    try:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    except OSError as exc:
        logger.error("Failed to start streamlit: %s", exc)
        return 1
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
