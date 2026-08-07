#!/usr/bin/env python3
"""Generate synthetic patient data with Synthea for CareFlow Analytics.

Wraps careflow.data_generation.synthea_generator.SyntheaGenerator with a
command-line interface for ad-hoc and scripted generation runs. All
options default to the values configured in config/project_config.yaml.

Usage:
    python3 scripts/generate_synthea_data.py --population 50
    python3 scripts/generate_synthea_data.py --population 50 --state Massachusetts --seed 42
    python3 scripts/generate_synthea_data.py --population 50 --fhir --no-csv
    python3 scripts/generate_synthea_data.py --population 50 --overwrite
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from careflow.config import load_config  # noqa: E402
from careflow.data_generation.synthea_generator import (  # noqa: E402
    SyntheaError,
    SyntheaGenerator,
)
from careflow.logging_config import get_logger  # noqa: E402

logger = get_logger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--population", type=int, default=None, help="Number of patients to generate"
    )
    parser.add_argument(
        "--state", type=str, default=None, help="US state to generate patients for"
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Random seed for reproducible generation"
    )
    parser.add_argument(
        "--csv", dest="csv", action="store_true", default=None, help="Enable CSV export"
    )
    parser.add_argument(
        "--no-csv", dest="csv", action="store_false", help="Disable CSV export"
    )
    parser.add_argument(
        "--fhir", dest="fhir", action="store_true", default=None, help="Enable FHIR export"
    )
    parser.add_argument(
        "--no-fhir", dest="fhir", action="store_false", help="Disable FHIR export"
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        default=None,
        help="Allow overwriting existing raw Synthea data",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config()
    settings = config.synthea

    overrides = {}
    if args.population is not None:
        overrides["population_size"] = args.population
    if args.state is not None:
        overrides["state"] = args.state
    if args.seed is not None:
        overrides["seed"] = args.seed
    if args.csv is not None:
        overrides["export_csv"] = args.csv
    if args.fhir is not None:
        overrides["export_fhir"] = args.fhir
    if args.overwrite is not None:
        overrides["overwrite"] = args.overwrite

    if overrides:
        settings = replace(settings, **overrides)

    generator = SyntheaGenerator(settings=settings, careflow_version=config.project_version)

    try:
        manifest = generator.generate()
    except SyntheaError as exc:
        logger.error(str(exc))
        return 1

    logger.info(
        "Synthea generation complete: %d file(s) written. Manifest: %s",
        len(manifest["files"]),
        settings.manifest_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
