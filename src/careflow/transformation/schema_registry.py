"""Declarative schema registry for Bronze-to-Silver transformation.

Every Bronze dataset has one :class:`DatasetSchema` entry describing its
expected source columns, the renamed target columns, which columns are
primary/foreign keys, dates, identifiers, or numeric measures, which
target columns are required, and which named transform (if any) applies
dataset-specific derived columns. The transformation engine in
``silver_transformer.py`` is driven entirely by this registry rather than
relying on pandas type inference or hard-coded per-file branches.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

SCHEMA_VERSION = "1.0.0"

_NON_ALNUM_RE = re.compile(r"[^0-9a-zA-Z]+")

# Foreign-key-shaped columns that mean the same thing in every Synthea file.
# Applying this uniformly is a single, deterministic rule reused everywhere
# it appears -- not a table-specific invention.
COMMON_ID_RENAMES: dict[str, str] = {
    "PATIENT": "patient_id",
    "ENCOUNTER": "encounter_id",
    "ORGANIZATION": "organization_id",
    "PROVIDER": "provider_id",
    "PAYER": "payer_id",
    "MEMBERID": "member_id",
}


def to_snake_case(name: str) -> str:
    """Normalize an arbitrary column name to lowercase snake_case."""
    text = _NON_ALNUM_RE.sub("_", name.strip()).strip("_")
    return text.lower() or "column"


def _build_column_mapping(source_columns: list[str], overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve every source column to its target name.

    Precedence: explicit per-dataset ``overrides`` > :data:`COMMON_ID_RENAMES`
    > a literal ``Id``/``ID`` column renamed to ``id`` > generic snake_case.
    """
    overrides = overrides or {}
    mapping: dict[str, str] = {}
    for col in source_columns:
        if col in overrides:
            mapping[col] = overrides[col]
        elif col in COMMON_ID_RENAMES:
            mapping[col] = COMMON_ID_RENAMES[col]
        elif col.strip().lower() == "id":
            mapping[col] = "id"
        else:
            mapping[col] = to_snake_case(col)
    return mapping


def _auto_date_columns(target_columns: list[str]) -> tuple[str, ...]:
    """Deterministically flag date-shaped target columns by name only."""
    result = []
    for col in target_columns:
        if col in ("start", "stop", "date") or "date" in col:
            result.append(col)
    return tuple(result)


@dataclass(frozen=True)
class DatasetSchema:
    """Declarative schema for one Bronze -> Silver dataset transformation."""

    source_file: str
    """Original raw CSV filename, e.g. ``"patients.csv"``."""

    bronze_file: str
    """Bronze Parquet filename, e.g. ``"patients.parquet"``."""

    target_dataset: str
    """Silver table name, e.g. ``"patients"``."""

    expected_source_columns: tuple[str, ...]
    column_mapping: dict[str, str] = field(repr=False)
    primary_key: str | None
    foreign_keys: tuple[str, ...]
    date_columns: tuple[str, ...]
    identifier_columns: tuple[str, ...]
    numeric_columns: tuple[str, ...]
    required_columns: tuple[str, ...]
    transform_name: str | None = None

    @property
    def target_columns(self) -> tuple[str, ...]:
        return tuple(self.column_mapping.values())


def _make_schema(
    source_file: str,
    target_dataset: str,
    source_columns: list[str],
    *,
    overrides: dict[str, str] | None = None,
    explicit_date_columns: tuple[str, ...] | None = None,
    explicit_numeric_columns: tuple[str, ...] = (),
    required_columns: tuple[str, ...] = (),
    transform_name: str | None = None,
) -> DatasetSchema:
    mapping = _build_column_mapping(source_columns, overrides)
    target_columns = list(mapping.values())

    date_columns = explicit_date_columns if explicit_date_columns is not None else _auto_date_columns(target_columns)

    primary_key = mapping.get("Id") or mapping.get("ID")
    foreign_keys = tuple(
        target for target in dict.fromkeys(COMMON_ID_RENAMES.values())
        if target in target_columns and target != primary_key
    )
    identifier_columns = tuple(
        dict.fromkeys(
            ([primary_key] if primary_key else [])
            + list(foreign_keys)
            + [c for c in target_columns if c.endswith("code") or c in ("zip", "fips", "ssn", "drivers", "passport", "udi")]
        )
    )

    return DatasetSchema(
        source_file=source_file,
        bronze_file=source_file.replace(".csv", ".parquet"),
        target_dataset=target_dataset,
        expected_source_columns=tuple(source_columns),
        column_mapping=mapping,
        primary_key=primary_key,
        foreign_keys=foreign_keys,
        date_columns=date_columns,
        identifier_columns=identifier_columns,
        numeric_columns=explicit_numeric_columns,
        required_columns=required_columns,
        transform_name=transform_name,
    )


# ---------------------------------------------------------------------------
# The six datasets with explicit, dataset-specific transformation rules.
# ---------------------------------------------------------------------------

_PATIENTS = _make_schema(
    "patients.csv", "patients",
    ["Id", "BIRTHDATE", "DEATHDATE", "SSN", "DRIVERS", "PASSPORT", "PREFIX", "FIRST", "MIDDLE",
     "LAST", "SUFFIX", "MAIDEN", "MARITAL", "RACE", "ETHNICITY", "GENDER", "BIRTHPLACE", "ADDRESS",
     "CITY", "STATE", "COUNTY", "FIPS", "ZIP", "LAT", "LON", "HEALTHCARE_EXPENSES",
     "HEALTHCARE_COVERAGE", "INCOME"],
    overrides={"Id": "patient_id"},
    explicit_date_columns=("birthdate", "deathdate"),
    explicit_numeric_columns=("lat", "lon", "healthcare_expenses", "healthcare_coverage", "income"),
    required_columns=("patient_id",),
    transform_name="patients",
)

_ENCOUNTERS = _make_schema(
    "encounters.csv", "encounters",
    ["Id", "START", "STOP", "PATIENT", "ORGANIZATION", "PROVIDER", "PAYER", "ENCOUNTERCLASS",
     "CODE", "DESCRIPTION", "BASE_ENCOUNTER_COST", "TOTAL_CLAIM_COST", "PAYER_COVERAGE",
     "REASONCODE", "REASONDESCRIPTION"],
    overrides={"Id": "encounter_id", "ENCOUNTERCLASS": "encounter_class"},
    explicit_date_columns=("start", "stop"),
    explicit_numeric_columns=("base_encounter_cost", "total_claim_cost", "payer_coverage"),
    required_columns=("encounter_id", "patient_id", "start"),
    transform_name="encounters",
)

_CONDITIONS = _make_schema(
    "conditions.csv", "conditions",
    ["START", "STOP", "PATIENT", "ENCOUNTER", "SYSTEM", "CODE", "DESCRIPTION"],
    explicit_date_columns=("start", "stop"),
    required_columns=("patient_id", "code"),
    transform_name="conditions",
)

_PROCEDURES = _make_schema(
    "procedures.csv", "procedures",
    ["START", "STOP", "PATIENT", "ENCOUNTER", "SYSTEM", "CODE", "DESCRIPTION", "BASE_COST",
     "REASONCODE", "REASONDESCRIPTION"],
    explicit_date_columns=("start", "stop"),
    explicit_numeric_columns=("base_cost",),
    required_columns=("patient_id", "code"),
    transform_name="procedures",
)

_MEDICATIONS = _make_schema(
    "medications.csv", "medications",
    ["START", "STOP", "PATIENT", "PAYER", "ENCOUNTER", "CODE", "DESCRIPTION", "BASE_COST",
     "PAYER_COVERAGE", "DISPENSES", "TOTALCOST", "REASONCODE", "REASONDESCRIPTION"],
    overrides={"TOTALCOST": "total_cost"},
    explicit_date_columns=("start", "stop"),
    explicit_numeric_columns=("base_cost", "payer_coverage", "dispenses", "total_cost"),
    required_columns=("patient_id", "code"),
    transform_name="medications",
)

_OBSERVATIONS = _make_schema(
    "observations.csv", "observations",
    ["DATE", "PATIENT", "ENCOUNTER", "CATEGORY", "CODE", "DESCRIPTION", "VALUE", "UNITS", "TYPE"],
    overrides={"DATE": "observation_date"},
    explicit_date_columns=("observation_date",),
    required_columns=("patient_id", "code"),
    transform_name="observations",
)

# ---------------------------------------------------------------------------
# Remaining Bronze tables: generic normalization only (no invented
# table-specific business rules). Numeric typing here is limited to columns
# Bronze already typed as double (Phase 2E's cost_fields), reusing that
# decision rather than guessing which other columns are safely numeric.
# ---------------------------------------------------------------------------

_ALLERGIES = _make_schema(
    "allergies.csv", "allergies",
    ["START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "SYSTEM", "DESCRIPTION", "TYPE", "CATEGORY",
     "REACTION1", "DESCRIPTION1", "SEVERITY1", "REACTION2", "DESCRIPTION2", "SEVERITY2"],
)

_CAREPLANS = _make_schema(
    "careplans.csv", "careplans",
    ["Id", "START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "REASONCODE",
     "REASONDESCRIPTION"],
)

_CLAIMS = _make_schema(
    "claims.csv", "claims",
    ["Id", "PATIENTID", "PROVIDERID", "PRIMARYPATIENTINSURANCEID", "SECONDARYPATIENTINSURANCEID",
     "DEPARTMENTID", "PATIENTDEPARTMENTID", "DIAGNOSIS1", "DIAGNOSIS2", "DIAGNOSIS3", "DIAGNOSIS4",
     "DIAGNOSIS5", "DIAGNOSIS6", "DIAGNOSIS7", "DIAGNOSIS8", "REFERRINGPROVIDERID", "APPOINTMENTID",
     "CURRENTILLNESSDATE", "SERVICEDATE", "SUPERVISINGPROVIDERID", "STATUS1", "STATUS2", "STATUSP",
     "OUTSTANDING1", "OUTSTANDING2", "OUTSTANDINGP", "LASTBILLEDDATE1", "LASTBILLEDDATE2",
     "LASTBILLEDDATEP", "HEALTHCARECLAIMTYPEID1", "HEALTHCARECLAIMTYPEID2"],
    overrides={"PATIENTID": "patient_id", "APPOINTMENTID": "encounter_id"},
)

_CLAIMS_TRANSACTIONS = _make_schema(
    "claims_transactions.csv", "claims_transactions",
    ["ID", "CLAIMID", "CHARGEID", "PATIENTID", "TYPE", "AMOUNT", "METHOD", "FROMDATE", "TODATE",
     "PLACEOFSERVICE", "PROCEDURECODE", "MODIFIER1", "MODIFIER2", "DIAGNOSISREF1", "DIAGNOSISREF2",
     "DIAGNOSISREF3", "DIAGNOSISREF4", "UNITS", "DEPARTMENTID", "NOTES", "UNITAMOUNT",
     "TRANSFEROUTID", "TRANSFERTYPE", "PAYMENTS", "ADJUSTMENTS", "TRANSFERS", "OUTSTANDING",
     "APPOINTMENTID", "LINENOTE", "PATIENTINSURANCEID", "FEESCHEDULEID", "PROVIDERID",
     "SUPERVISINGPROVIDERID"],
    overrides={"PATIENTID": "patient_id", "APPOINTMENTID": "encounter_id"},
)

_DEVICES = _make_schema(
    "devices.csv", "devices",
    ["START", "STOP", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "UDI"],
)

_IMAGING_STUDIES = _make_schema(
    "imaging_studies.csv", "imaging_studies",
    ["Id", "DATE", "PATIENT", "ENCOUNTER", "SERIES_UID", "BODYSITE_CODE", "BODYSITE_DESCRIPTION",
     "MODALITY_CODE", "MODALITY_DESCRIPTION", "INSTANCE_UID", "SOP_CODE", "SOP_DESCRIPTION",
     "PROCEDURE_CODE"],
)

_IMMUNIZATIONS = _make_schema(
    "immunizations.csv", "immunizations",
    ["DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "BASE_COST"],
    overrides={"DATE": "immunization_date"},
    explicit_date_columns=("immunization_date",),
    explicit_numeric_columns=("base_cost",),
)

_ORGANIZATIONS = _make_schema(
    "organizations.csv", "organizations",
    ["Id", "NAME", "ADDRESS", "CITY", "STATE", "ZIP", "LAT", "LON", "PHONE", "REVENUE",
     "UTILIZATION"],
)

_PAYER_TRANSITIONS = _make_schema(
    "payer_transitions.csv", "payer_transitions",
    ["PATIENT", "MEMBERID", "START_DATE", "END_DATE", "PAYER", "SECONDARY_PAYER",
     "PLAN_OWNERSHIP", "OWNER_NAME"],
)

_PAYERS = _make_schema(
    "payers.csv", "payers",
    ["Id", "NAME", "OWNERSHIP", "ADDRESS", "CITY", "STATE_HEADQUARTERED", "ZIP", "PHONE",
     "AMOUNT_COVERED", "AMOUNT_UNCOVERED", "REVENUE", "COVERED_ENCOUNTERS", "UNCOVERED_ENCOUNTERS",
     "COVERED_MEDICATIONS", "UNCOVERED_MEDICATIONS", "COVERED_PROCEDURES", "UNCOVERED_PROCEDURES",
     "COVERED_IMMUNIZATIONS", "UNCOVERED_IMMUNIZATIONS", "UNIQUE_CUSTOMERS", "QOLS_AVG",
     "MEMBER_MONTHS"],
)

_PROVIDERS = _make_schema(
    "providers.csv", "providers",
    ["Id", "ORGANIZATION", "NAME", "GENDER", "SPECIALITY", "ADDRESS", "CITY", "STATE", "ZIP",
     "LAT", "LON", "ENCOUNTERS", "PROCEDURES"],
)

_SUPPLIES = _make_schema(
    "supplies.csv", "supplies",
    ["DATE", "PATIENT", "ENCOUNTER", "CODE", "DESCRIPTION", "QUANTITY"],
    overrides={"DATE": "supply_date"},
    explicit_date_columns=("supply_date",),
)

SCHEMA_REGISTRY: dict[str, DatasetSchema] = {
    schema.target_dataset: schema
    for schema in (
        _PATIENTS, _ENCOUNTERS, _CONDITIONS, _PROCEDURES, _MEDICATIONS, _OBSERVATIONS,
        _ALLERGIES, _CAREPLANS, _CLAIMS, _CLAIMS_TRANSACTIONS, _DEVICES, _IMAGING_STUDIES,
        _IMMUNIZATIONS, _ORGANIZATIONS, _PAYER_TRANSITIONS, _PAYERS, _PROVIDERS, _SUPPLIES,
    )
}


def get_schema(target_dataset: str) -> DatasetSchema:
    try:
        return SCHEMA_REGISTRY[target_dataset]
    except KeyError as exc:
        raise KeyError(f"No schema registered for dataset '{target_dataset}'") from exc


def list_datasets() -> list[str]:
    return sorted(SCHEMA_REGISTRY.keys())
