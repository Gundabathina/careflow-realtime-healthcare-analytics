"""Configuration-driven referential integrity validation for CareFlow Analytics.

Checks foreign-key relationships between Synthea CSV files (e.g.
encounters.PATIENT -> patients.Id) using chunked reads, and skips any
relationship whose required files or columns are not present.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from careflow.config import Config, load_config
from careflow.logging_config import get_logger
from careflow.profiling.file_profiler import DEFAULT_CHUNK_SIZE, _relative_to_root

logger = get_logger(__name__)

RELATIONSHIP_PROFILING_VERSION = "1.0.0"
RELATIONSHIP_SUMMARY_FILENAME = "relationship_summary.json"

DEFAULT_MAX_SAMPLES = 10
DEFAULT_WARNING_MATCH_PCT = 98.0
DEFAULT_FAIL_MATCH_PCT = 90.0

STATUS_VALUES = ("pass", "warning", "fail", "skipped")


@dataclass(frozen=True)
class RelationshipConfig:
    """One configured foreign-key relationship between two CSV files."""

    name: str
    parent_file: str
    parent_key: str
    child_file: str
    child_key: str


DEFAULT_RELATIONSHIPS: tuple[RelationshipConfig, ...] = (
    RelationshipConfig("encounters.PATIENT -> patients.Id", "patients.csv", "Id", "encounters.csv", "PATIENT"),
    RelationshipConfig("encounters.ORGANIZATION -> organizations.Id", "organizations.csv", "Id", "encounters.csv", "ORGANIZATION"),
    RelationshipConfig("encounters.PROVIDER -> providers.Id", "providers.csv", "Id", "encounters.csv", "PROVIDER"),
    RelationshipConfig("encounters.PAYER -> payers.Id", "payers.csv", "Id", "encounters.csv", "PAYER"),
    RelationshipConfig("conditions.PATIENT -> patients.Id", "patients.csv", "Id", "conditions.csv", "PATIENT"),
    RelationshipConfig("conditions.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "conditions.csv", "ENCOUNTER"),
    RelationshipConfig("procedures.PATIENT -> patients.Id", "patients.csv", "Id", "procedures.csv", "PATIENT"),
    RelationshipConfig("procedures.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "procedures.csv", "ENCOUNTER"),
    RelationshipConfig("medications.PATIENT -> patients.Id", "patients.csv", "Id", "medications.csv", "PATIENT"),
    RelationshipConfig("medications.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "medications.csv", "ENCOUNTER"),
    RelationshipConfig("observations.PATIENT -> patients.Id", "patients.csv", "Id", "observations.csv", "PATIENT"),
    RelationshipConfig("observations.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "observations.csv", "ENCOUNTER"),
    RelationshipConfig("claims.PATIENTID -> patients.Id", "patients.csv", "Id", "claims.csv", "PATIENTID"),
    RelationshipConfig("claims.APPOINTMENTID -> encounters.Id", "encounters.csv", "Id", "claims.csv", "APPOINTMENTID"),
    RelationshipConfig("devices.PATIENT -> patients.Id", "patients.csv", "Id", "devices.csv", "PATIENT"),
    RelationshipConfig("devices.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "devices.csv", "ENCOUNTER"),
    RelationshipConfig("immunizations.PATIENT -> patients.Id", "patients.csv", "Id", "immunizations.csv", "PATIENT"),
    RelationshipConfig("immunizations.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "immunizations.csv", "ENCOUNTER"),
    RelationshipConfig("careplans.PATIENT -> patients.Id", "patients.csv", "Id", "careplans.csv", "PATIENT"),
    RelationshipConfig("careplans.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "careplans.csv", "ENCOUNTER"),
    RelationshipConfig("imaging_studies.PATIENT -> patients.Id", "patients.csv", "Id", "imaging_studies.csv", "PATIENT"),
    RelationshipConfig("imaging_studies.ENCOUNTER -> encounters.Id", "encounters.csv", "Id", "imaging_studies.csv", "ENCOUNTER"),
)


def load_relationship_configs(config: Config | None = None) -> list[RelationshipConfig]:
    """Load relationship definitions from ``data_quality.relationships`` in config.

    Falls back to :data:`DEFAULT_RELATIONSHIPS` when no config override is
    present, so callers (including tests) can use this module without any
    project configuration file at all.
    """
    cfg = config or load_config()
    raw = cfg.get("data_quality", "relationships", default=None)
    if not raw:
        return list(DEFAULT_RELATIONSHIPS)
    return [
        RelationshipConfig(
            name=item["name"],
            parent_file=item["parent_file"],
            parent_key=item["parent_key"],
            child_file=item["child_file"],
            child_key=item["child_key"],
        )
        for item in raw
    ]


def load_relationship_thresholds(config: Config | None = None) -> tuple[float, float]:
    """Return (warning_match_pct, fail_match_pct) from config, with sane defaults."""
    cfg = config or load_config()
    warning_pct = float(
        cfg.get("data_quality", "relationship_thresholds", "warning_match_pct", default=DEFAULT_WARNING_MATCH_PCT)
    )
    fail_pct = float(
        cfg.get("data_quality", "relationship_thresholds", "fail_match_pct", default=DEFAULT_FAIL_MATCH_PCT)
    )
    return warning_pct, fail_pct


def _read_header(path: Path) -> list[str] | None:
    try:
        return list(pd.read_csv(path, nrows=0).columns)
    except (pd.errors.EmptyDataError, pd.errors.ParserError, UnicodeDecodeError, OSError):
        return None


def _skip_reason(csv_dir: Path, relationship: RelationshipConfig) -> str | None:
    parent_path = csv_dir / relationship.parent_file
    child_path = csv_dir / relationship.child_file

    if not parent_path.is_file():
        return f"Parent file missing: {relationship.parent_file}"
    if not child_path.is_file():
        return f"Child file missing: {relationship.child_file}"

    parent_columns = _read_header(parent_path)
    if parent_columns is None:
        return f"Could not read parent file: {relationship.parent_file}"
    if relationship.parent_key not in parent_columns:
        return f"Parent column '{relationship.parent_key}' missing in {relationship.parent_file}"

    child_columns = _read_header(child_path)
    if child_columns is None:
        return f"Could not read child file: {relationship.child_file}"
    if relationship.child_key not in child_columns:
        return f"Child column '{relationship.child_key}' missing in {relationship.child_file}"

    return None


def _load_parent_keys(path: Path, key_column: str, chunk_size: int) -> tuple[set[str], int]:
    """Return (unique parent key values, duplicate parent key occurrences)."""
    counts: dict[str, int] = {}
    for chunk in pd.read_csv(path, usecols=[key_column], chunksize=chunk_size, dtype=str):
        for value in chunk[key_column].dropna():
            counts[value] = counts.get(value, 0) + 1
    duplicate_occurrences = sum(c - 1 for c in counts.values() if c > 1)
    return set(counts.keys()), duplicate_occurrences


def _skipped_relationship_result(base: dict, reason: str) -> dict:
    return {
        **base,
        "records_evaluated": None,
        "non_null_foreign_keys": None,
        "matched_references": None,
        "unmatched_references": None,
        "match_percentage": None,
        "null_foreign_keys": None,
        "duplicate_parent_keys": None,
        "sample_unmatched_values": [],
        "status": "skipped",
        "skipped_reason": reason,
    }


def evaluate_relationship(
    csv_dir: Path,
    relationship: RelationshipConfig,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    warning_pct: float = DEFAULT_WARNING_MATCH_PCT,
    fail_pct: float = DEFAULT_FAIL_MATCH_PCT,
) -> dict:
    """Validate a single foreign-key relationship, skipping if not checkable."""
    base = {
        "relationship": relationship.name,
        "parent_file": relationship.parent_file,
        "parent_key": relationship.parent_key,
        "child_file": relationship.child_file,
        "child_key": relationship.child_key,
    }

    reason = _skip_reason(csv_dir, relationship)
    if reason:
        return _skipped_relationship_result(base, reason)

    parent_path = csv_dir / relationship.parent_file
    child_path = csv_dir / relationship.child_file

    try:
        parent_keys, duplicate_parent_keys = _load_parent_keys(
            parent_path, relationship.parent_key, chunk_size
        )

        records_evaluated = 0
        null_fk = 0
        non_null_fk = 0
        matched = 0
        unmatched_samples: list[str] = []
        seen_unmatched: set[str] = set()

        for chunk in pd.read_csv(child_path, usecols=[relationship.child_key], chunksize=chunk_size, dtype=str):
            records_evaluated += len(chunk)
            column = chunk[relationship.child_key]
            null_mask = column.isna()
            null_fk += int(null_mask.sum())
            non_null_values = column[~null_mask]
            non_null_fk += len(non_null_values)

            is_match = non_null_values.isin(parent_keys)
            matched += int(is_match.sum())

            if len(unmatched_samples) < max_samples:
                for value in non_null_values[~is_match.values]:
                    if value not in seen_unmatched:
                        seen_unmatched.add(value)
                        unmatched_samples.append(value)
                        if len(unmatched_samples) >= max_samples:
                            break
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _skipped_relationship_result(base, f"Error evaluating relationship: {exc}")

    unmatched = non_null_fk - matched
    match_pct = (matched / non_null_fk * 100) if non_null_fk else 100.0

    if match_pct >= warning_pct:
        status = "pass"
    elif match_pct >= fail_pct:
        status = "warning"
    else:
        status = "fail"

    return {
        **base,
        "records_evaluated": records_evaluated,
        "non_null_foreign_keys": non_null_fk,
        "matched_references": matched,
        "unmatched_references": unmatched,
        "match_percentage": round(match_pct, 4),
        "null_foreign_keys": null_fk,
        "duplicate_parent_keys": duplicate_parent_keys,
        "sample_unmatched_values": unmatched_samples,
        "status": status,
        "skipped_reason": None,
    }


def build_relationship_summary(
    csv_dir: Path,
    relationships: list[RelationshipConfig] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    warning_pct: float = DEFAULT_WARNING_MATCH_PCT,
    fail_pct: float = DEFAULT_FAIL_MATCH_PCT,
) -> dict:
    """Evaluate every configured relationship and return the full summary manifest."""
    rels = relationships if relationships is not None else load_relationship_configs()
    results: list[dict] = []
    counts = {"pass": 0, "warning": 0, "fail": 0, "skipped": 0}

    for relationship in rels:
        logger.info("Validating relationship: %s", relationship.name)
        result = evaluate_relationship(
            csv_dir,
            relationship,
            chunk_size=chunk_size,
            max_samples=max_samples,
            warning_pct=warning_pct,
            fail_pct=fail_pct,
        )
        results.append(result)
        counts[result["status"]] += 1
        if result["status"] in ("fail", "skipped"):
            logger.warning(
                "Relationship %s -> %s", relationship.name, result["status"]
            )

    return {
        "relationship_profiling_version": RELATIONSHIP_PROFILING_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_directory": _relative_to_root(csv_dir),
        "summary": {
            "total_relationships": len(rels),
            "passed": counts["pass"],
            "warnings": counts["warning"],
            "failed": counts["fail"],
            "skipped": counts["skipped"],
        },
        "relationships": results,
    }


def write_relationship_summary_json(summary: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2, default=str)
        fh.write("\n")


def run_relationship_validation(
    csv_dir: Path,
    output_dir: Path,
    relationships: list[RelationshipConfig] | None = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    warning_pct: float = DEFAULT_WARNING_MATCH_PCT,
    fail_pct: float = DEFAULT_FAIL_MATCH_PCT,
) -> dict:
    """Build the relationship summary and write it to ``output_dir``."""
    summary = build_relationship_summary(
        csv_dir,
        relationships=relationships,
        chunk_size=chunk_size,
        max_samples=max_samples,
        warning_pct=warning_pct,
        fail_pct=fail_pct,
    )
    write_relationship_summary_json(summary, output_dir / RELATIONSHIP_SUMMARY_FILENAME)
    return summary
