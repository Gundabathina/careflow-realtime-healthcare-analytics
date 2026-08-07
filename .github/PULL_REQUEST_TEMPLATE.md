## Summary

What does this change do, and why?

## Which layer(s) this touches

- [ ] Data generation (Synthea)
- [ ] Bronze / Silver / Gold pipeline
- [ ] PostgreSQL warehouse
- [ ] dbt
- [ ] Airflow
- [ ] Streamlit dashboard
- [ ] Power BI
- [ ] Documentation only

## Testing

- [ ] Ran the targeted test file: `PYTHONPATH=src python3 -m pytest -q tests/test_<relevant_file>.py`
- [ ] Ran the full suite: `PYTHONPATH=src python3 -m pytest -q tests/`
- [ ] Added/updated tests covering this change

```
Paste test output summary here.
```

## Checklist

- [ ] No `.env`, real credentials, or generated caches (`__pycache__`, `.pytest_cache`, `target/`, `dbt_packages/`, `logs/`) are included in this diff
- [ ] New SQL is parameterized (`%s` placeholders), never string-interpolated
- [ ] No SSN, passport, driver's license, patient name, street address, or precise lat/long is exposed in any new public model, dashboard page, or export (see [`docs/security.md`](../docs/security.md))
- [ ] Relevant docs in `docs/` are updated if behavior changed
- [ ] Follows [`CONTRIBUTING.md`](../CONTRIBUTING.md) conventions

## Related issue

Closes #
