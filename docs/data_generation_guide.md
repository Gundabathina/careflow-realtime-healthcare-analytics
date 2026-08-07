# Synthetic Data Generation Guide (Phase 2B)

CareFlow Analytics uses [Synthea](https://github.com/synthetichealth/synthea),
an open-source synthetic patient generator, to produce realistic but
entirely fictional patient, encounter, and claims data for development and
testing. No real patient data is used anywhere in this project.

This phase covers installation and generation only. Profiling, relationship
checks, warehousing, streaming, orchestration, dashboards, and modeling are
out of scope until later phases.

## Prerequisites

- **Git** — used to clone the Synthea repository.
- **Java 11+ (JDK)** — Synthea is a Java/Gradle project.

`scripts/setup_synthea.py` checks both and reports the detected versions
before doing anything else.

## Configuration

All Synthea settings live under `data_generation.synthea` in
[`config/project_config.yaml`](../config/project_config.yaml):

| Key | Purpose |
| --- | --- |
| `population_size` | Number of patients to generate |
| `state` | US state Synthea should model |
| `seed` | Random seed for reproducible runs |
| `export_formats.csv` | Enable/disable CSV export |
| `export_formats.fhir` | Enable/disable FHIR export |
| `overwrite` | Whether generation may replace existing raw data |
| `repository_url` | Synthea Git repository to clone |
| `install_dir` | Where Synthea is installed (relative to project root) |
| `temp_output_dir` | Scratch directory Synthea writes into during a run |
| `manifest_path` | Where the generation manifest is written |

Raw data destinations reuse the paths already defined under
`paths.data.raw_synthea_csv` and `paths.data.raw_synthea_fhir` — they are
not duplicated in the `synthea` block.

Access all of this in code via `careflow.config.load_config().synthea`,
which returns a `SyntheaSettings` dataclass with every path already
resolved relative to the project root.

## Setting up Synthea

```bash
PYTHONPATH=src python3 scripts/setup_synthea.py
```

This will:

1. Check that `git` and `java` are available and print their versions.
2. Clone Synthea into `tools/synthea` (from `config/project_config.yaml`)
   **only if that directory doesn't already exist** — an existing
   installation is never modified or deleted.
3. Return a non-zero exit code if any prerequisite is missing or the clone
   fails.

The first real generation run will take longer than subsequent ones
because Synthea builds itself via its Gradle wrapper on first use.

## Generating data

```bash
PYTHONPATH=src python3 scripts/generate_synthea_data.py --population 50
```

Useful options (all override the corresponding config value for a single
run):

- `--population N` — number of patients
- `--state "Massachusetts"` — target state
- `--seed N` — reproducible seed
- `--csv` / `--no-csv`, `--fhir` / `--no-fhir` — export format toggles
- `--overwrite` — allow replacing existing raw data

By default, generation refuses to run if the configured raw CSV/FHIR
destinations already contain files — pass `--overwrite` (or set
`overwrite: true` in config) to replace them. Overwriting only replaces
files with matching names; it never deletes unrelated files already in
those directories.

### How a run works

1. Validates Java is available and Synthea is installed.
2. Builds the Synthea command as an argument list (no shell interpolation).
3. Runs Synthea from its installation directory, writing output to a
   fresh, isolated temporary directory under `temp_output_dir`.
4. Only after Synthea exits successfully **and** all requested output
   formats are confirmed present does it copy files into the raw data
   destinations — so a failed or partial run never touches existing data.
5. The temporary run directory is always removed afterward.

## The generation manifest

On success, a manifest is written to `data/raw/synthea/generation_manifest.json`
(configurable via `manifest_path`). It records:

- UTC generation timestamp
- Requested population, state, and seed
- Enabled export formats
- The sanitized Synthea command that was run
- Each generated file's name, destination path, size, SHA-256 checksum,
  and (for CSV) row count excluding the header
- The CareFlow version
- The installed Synthea repository's Git commit hash, when available

The manifest is written only after a fully successful run — it is never
partially written. All paths in the manifest are relative to the project
root; no usernames, absolute home directories, or secrets are ever
included.

## Testing

`tests/test_synthea_generator.py` covers command construction, CSV/FHIR
generation, overwrite handling, failure paths, checksum/row-count
utilities, and manifest sanitization — all using temporary directories and
mocked subprocess calls. No test requires Java, Git, network access, or a
real Synthea installation:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_synthea_generator.py
```
