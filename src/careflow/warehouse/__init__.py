"""PostgreSQL analytics warehouse utilities for CareFlow Analytics.

This package loads the Gold star schema into a local PostgreSQL
warehouse (via Docker Compose), manages schema/index/view DDL, and
validates the loaded warehouse against Gold's own manifest and KPI
summary.
"""
