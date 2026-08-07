"""Dynamic CSV profiling for CareFlow Analytics.

Discovers every CSV file in a directory without assuming filenames or
schemas, and computes per-column and per-file statistics using chunked
reads so large files are never fully materialized in memory at once.
"""

from __future__ import annotations

import csv as csv_module
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from careflow.config import get_project_root
from careflow.logging_config import get_logger

logger = get_logger(__name__)

PROFILING_VERSION = "1.0.0"

DEFAULT_CHUNK_SIZE = 50_000
MAX_TRACKED_UNIQUE_VALUES = 100_000
MAX_DISPLAY_SAMPLE_VALUES = 5
MAX_CLASSIFICATION_SAMPLES = 25

DATE_NAME_HINTS = ("date", "time", "start", "stop", "dob", "birthdate", "deathdate")
CATEGORICAL_MAX_UNIQUE = 50
CATEGORICAL_MAX_RATIO = 0.05
IDENTIFIER_UNIQUE_RATIO_WITH_NAME_HINT = 0.9
IDENTIFIER_UNIQUE_RATIO_WITHOUT_HINT = 0.98
MIN_ROWS_FOR_IDENTIFIER_HEURISTIC = 5
HIGH_CARDINALITY_RATIO_THRESHOLD = 0.5
HIGH_CARDINALITY_MIN_UNIQUE = 20
NUMERIC_PARSE_RATIO_THRESHOLD = 0.9
DATE_PARSE_RATIO_THRESHOLD = 0.6

DATASET_PROFILE_FILENAME = "dataset_profile.json"
COLUMN_PROFILE_FILENAME = "column_profile.csv"

COLUMN_PROFILE_FIELDNAMES = [
    "file",
    "column",
    "dtype",
    "nullable",
    "null_pct",
    "distinct_values",
    "sample_values",
    "classification",
]


def _relative_to_root(path: Path) -> str:
    root = get_project_root()
    try:
        return str(Path(path).resolve().relative_to(root))
    except ValueError:
        return str(path)


def _looks_like_identifier_name(name: str) -> bool:
    lowered = name.strip().lower()
    if lowered in ("id", "identifier", "uuid", "guid"):
        return True
    tokens = [t for t in re.split(r"[^a-z0-9]+", lowered) if t]
    return bool(tokens) and tokens[-1] in ("id", "identifier", "uuid", "guid")


def _looks_like_date_name(name: str) -> bool:
    lowered = name.strip().lower()
    return any(hint in lowered for hint in DATE_NAME_HINTS)


def _numeric_parse_ratio(values: list[str]) -> float:
    if not values:
        return 0.0
    numeric = 0
    for value in values:
        try:
            float(value)
            numeric += 1
        except (TypeError, ValueError):
            continue
    return numeric / len(values)


def _date_parse_ratio(values: list[str]) -> float:
    if not values:
        return 0.0
    try:
        parsed = pd.to_datetime(pd.Series(values), errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return 0.0
    return float(parsed.notna().sum()) / len(values)


def _stringify_extreme(value: Any) -> Any:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        return value.item()
    return value


def classify_column(
    name: str,
    dtype_label: str,
    non_null_count: int,
    unique_count: int,
    classification_samples: list[str],
) -> dict[str, bool]:
    """Return heuristic "possible X column" tags. Tags are independent, not exclusive."""
    unique_ratio = (unique_count / non_null_count) if non_null_count else 0.0
    name_is_id_like = _looks_like_identifier_name(name)
    name_is_date_like = _looks_like_date_name(name)

    is_numeric_dtype = dtype_label.startswith(("int", "float", "Int", "Float"))
    is_date_dtype = dtype_label.startswith("datetime")

    numeric_ratio = _numeric_parse_ratio(classification_samples)
    date_ratio = _date_parse_ratio(classification_samples)

    is_possible_numeric = is_numeric_dtype or numeric_ratio >= NUMERIC_PARSE_RATIO_THRESHOLD

    is_possible_date = is_date_dtype or (
        name_is_date_like and date_ratio >= DATE_PARSE_RATIO_THRESHOLD
    )

    is_possible_identifier = False
    if non_null_count >= MIN_ROWS_FOR_IDENTIFIER_HEURISTIC:
        if name_is_id_like and unique_ratio >= IDENTIFIER_UNIQUE_RATIO_WITH_NAME_HINT:
            is_possible_identifier = True
        elif unique_ratio >= IDENTIFIER_UNIQUE_RATIO_WITHOUT_HINT:
            is_possible_identifier = True

    is_possible_high_cardinality = (
        non_null_count > 0
        and unique_count > HIGH_CARDINALITY_MIN_UNIQUE
        and unique_ratio >= HIGH_CARDINALITY_RATIO_THRESHOLD
    )

    is_possible_categorical = (
        not is_possible_identifier
        and not is_possible_date
        and non_null_count > 0
        and (unique_count <= CATEGORICAL_MAX_UNIQUE or unique_ratio <= CATEGORICAL_MAX_RATIO)
    )

    return {
        "is_possible_identifier": is_possible_identifier,
        "is_possible_date": is_possible_date,
        "is_possible_categorical": is_possible_categorical,
        "is_possible_numeric": is_possible_numeric,
        "is_possible_high_cardinality": is_possible_high_cardinality,
    }


@dataclass
class _ColumnAccumulator:
    """Incrementally accumulated statistics for one column, updated per chunk."""

    name: str
    dtype_labels: set[str] = field(default_factory=set)
    non_null_count: int = 0
    null_count: int = 0
    unique_values: set[str] = field(default_factory=set)
    unique_values_capped: bool = False
    classification_samples: list[str] = field(default_factory=list)
    min_value: Any = None
    max_value: Any = None

    def update(self, series: pd.Series) -> None:
        self.dtype_labels.add(str(series.dtype))
        null_mask = series.isna()
        self.null_count += int(null_mask.sum())
        non_null_series = series[~null_mask]
        self.non_null_count += int(non_null_series.shape[0])

        if non_null_series.empty:
            return

        for text in non_null_series.astype(str).unique():
            if len(self.unique_values) < MAX_TRACKED_UNIQUE_VALUES:
                self.unique_values.add(text)
            elif text not in self.unique_values:
                self.unique_values_capped = True

        if len(self.classification_samples) < MAX_CLASSIFICATION_SAMPLES:
            remaining = MAX_CLASSIFICATION_SAMPLES - len(self.classification_samples)
            self.classification_samples.extend(
                str(v) for v in non_null_series.iloc[:remaining]
            )

        try:
            chunk_min = non_null_series.min()
            chunk_max = non_null_series.max()
        except TypeError:
            str_series = non_null_series.astype(str)
            chunk_min = str_series.min()
            chunk_max = str_series.max()

        self.min_value = chunk_min if self.min_value is None else min(self.min_value, chunk_min)
        self.max_value = chunk_max if self.max_value is None else max(self.max_value, chunk_max)

    def sample_values(self) -> list[str]:
        return self.classification_samples[:MAX_DISPLAY_SAMPLE_VALUES]

    def dtype_label(self) -> tuple[str, bool]:
        labels = sorted(self.dtype_labels) if self.dtype_labels else ["object"]
        mixed = len(labels) > 1
        return (f"mixed({','.join(labels)})" if mixed else labels[0]), mixed


def _base_file_profile(filename: str, file_size: int, status: str, error: str | None) -> dict:
    return {
        "filename": filename,
        "status": status,
        "error": error,
        "file_size_bytes": file_size,
        "total_rows": 0,
        "total_columns": 0,
        "duplicate_rows": 0,
        "duplicate_percentage": 0.0,
        "columns": [],
    }


def profile_file(path: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    """Profile a single CSV file without ever loading it entirely into memory."""
    filename = path.name
    try:
        file_size = path.stat().st_size
    except OSError as exc:
        return _base_file_profile(filename, 0, "error", f"Could not stat file: {exc}")

    if file_size == 0:
        return _base_file_profile(filename, file_size, "empty", "File is empty (0 bytes).")

    try:
        header_df = pd.read_csv(path, nrows=0)
    except pd.errors.EmptyDataError:
        return _base_file_profile(filename, file_size, "empty", "File has no header or columns.")
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return _base_file_profile(filename, file_size, "error", str(exc))

    columns = list(header_df.columns)
    accumulators = {name: _ColumnAccumulator(name) for name in columns}
    total_rows = 0
    seen_hashes: set[int] = set()
    duplicate_rows = 0

    try:
        for chunk in pd.read_csv(path, chunksize=chunk_size):
            total_rows += len(chunk)
            for row_hash in pd.util.hash_pandas_object(chunk, index=False).to_numpy():
                row_hash = int(row_hash)
                if row_hash in seen_hashes:
                    duplicate_rows += 1
                else:
                    seen_hashes.add(row_hash)
            for name in columns:
                accumulators[name].update(chunk[name])
    except (pd.errors.ParserError, UnicodeDecodeError, OSError) as exc:
        return {
            **_base_file_profile(filename, file_size, "error", str(exc)),
            "total_columns": len(columns),
        }

    columns_output = []
    for name in columns:
        acc = accumulators[name]
        dtype_label, mixed_dtype = acc.dtype_label()
        null_pct = (acc.null_count / total_rows * 100) if total_rows else 0.0
        unique_count = len(acc.unique_values)

        classification = classify_column(
            name=name,
            dtype_label=dtype_label,
            non_null_count=acc.non_null_count,
            unique_count=unique_count,
            classification_samples=acc.classification_samples,
        )

        columns_output.append(
            {
                "name": name,
                "dtype": dtype_label,
                "mixed_dtype": mixed_dtype,
                "null_count": acc.null_count,
                "null_percentage": round(null_pct, 4),
                "unique_values": unique_count,
                "unique_values_capped": acc.unique_values_capped,
                "min_value": _stringify_extreme(acc.min_value),
                "max_value": _stringify_extreme(acc.max_value),
                "sample_values": acc.sample_values(),
                "classifications": classification,
            }
        )

    duplicate_pct = (duplicate_rows / total_rows * 100) if total_rows else 0.0

    return {
        "filename": filename,
        "status": "ok",
        "error": None,
        "file_size_bytes": file_size,
        "total_rows": total_rows,
        "total_columns": len(columns),
        "duplicate_rows": duplicate_rows,
        "duplicate_percentage": round(duplicate_pct, 4),
        "columns": columns_output,
    }


def discover_csv_files(csv_dir: Path) -> list[Path]:
    """Dynamically discover every CSV file in a directory, sorted for determinism."""
    if not csv_dir.is_dir():
        return []
    return sorted(p for p in csv_dir.glob("*.csv") if p.is_file())


def build_dataset_profile(csv_dir: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    """Discover and profile every CSV file in ``csv_dir``, returning the full manifest."""
    files = discover_csv_files(csv_dir)
    file_profiles = []
    files_ok = files_empty = files_error = 0
    total_rows = total_size_bytes = total_duplicate_rows = total_column_instances = 0

    for path in files:
        logger.info("Profiling %s", path.name)
        profile = profile_file(path, chunk_size=chunk_size)
        file_profiles.append(profile)

        total_size_bytes += profile["file_size_bytes"]
        if profile["status"] == "ok":
            files_ok += 1
            total_rows += profile["total_rows"]
            total_duplicate_rows += profile["duplicate_rows"]
            total_column_instances += profile["total_columns"]
        elif profile["status"] == "empty":
            files_empty += 1
        else:
            files_error += 1
            logger.warning("Failed to profile %s: %s", profile["filename"], profile["error"])

    return {
        "profiling_version": PROFILING_VERSION,
        "profiled_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_directory": _relative_to_root(csv_dir),
        "dataset_summary": {
            "total_files": len(files),
            "files_ok": files_ok,
            "files_empty": files_empty,
            "files_error": files_error,
            "total_rows": total_rows,
            "total_column_instances": total_column_instances,
            "total_size_bytes": total_size_bytes,
            "total_duplicate_rows": total_duplicate_rows,
        },
        "files": file_profiles,
    }


def write_dataset_profile_json(manifest: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
        fh.write("\n")


def _active_classification_tags(classifications: dict[str, bool]) -> str:
    tag_names = {
        "is_possible_identifier": "identifier",
        "is_possible_date": "date",
        "is_possible_categorical": "categorical",
        "is_possible_numeric": "numeric",
        "is_possible_high_cardinality": "high_cardinality",
    }
    active = [label for key, label in tag_names.items() if classifications.get(key)]
    return "|".join(active) if active else "none"


def write_column_profile_csv(manifest: dict, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv_module.DictWriter(fh, fieldnames=COLUMN_PROFILE_FIELDNAMES)
        writer.writeheader()
        for file_profile in manifest["files"]:
            for column in file_profile["columns"]:
                writer.writerow(
                    {
                        "file": file_profile["filename"],
                        "column": column["name"],
                        "dtype": column["dtype"],
                        "nullable": column["null_count"] > 0,
                        "null_pct": column["null_percentage"],
                        "distinct_values": column["unique_values"],
                        "sample_values": "; ".join(column["sample_values"]),
                        "classification": _active_classification_tags(column["classifications"]),
                    }
                )


def run_profiling(
    csv_dir: Path, output_dir: Path, chunk_size: int = DEFAULT_CHUNK_SIZE
) -> dict:
    """Profile every CSV in ``csv_dir`` and write both report files to ``output_dir``."""
    manifest = build_dataset_profile(csv_dir, chunk_size=chunk_size)
    write_dataset_profile_json(manifest, output_dir / DATASET_PROFILE_FILENAME)
    write_column_profile_csv(manifest, output_dir / COLUMN_PROFILE_FILENAME)
    return manifest
