"""Gold-layer dimensional modeling utilities for CareFlow Analytics.

This package builds a star schema (dimensions, facts) and healthcare
analytics marts from Silver Parquet datasets, using deterministic
surrogate keys so the same natural key always maps to the same key
across runs.
"""
