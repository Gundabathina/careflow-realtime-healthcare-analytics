-- CareFlow Analytics - PostgreSQL warehouse schema (Phase 3B)
--
-- Hand-authored, explicit column definitions -- types are chosen by
-- semantic meaning (surrogate key, natural id, currency, ratio, date key,
-- timestamp, flag), never generated automatically from pandas/pyarrow
-- inference. Idempotent: safe to run against an already-initialized
-- database.
--
-- Foreign keys are only declared where the referenced column is nullable
-- and Gold's loader guarantees an unresolved reference becomes SQL NULL
-- (never the -1 "unknown" sentinel used inside Gold Parquet). Fact-to-fact
-- references (e.g. a condition's encounter_key back to fact_encounter) and
-- all mart tables are intentionally left without FK constraints since
-- marts are derived/aggregated and some fact-to-fact links are not always
-- resolvable; the warehouse validator reports orphan counts for these
-- instead of enforcing them at the database level.

CREATE SCHEMA IF NOT EXISTS careflow_meta;
CREATE SCHEMA IF NOT EXISTS careflow_dim;
CREATE SCHEMA IF NOT EXISTS careflow_fact;
CREATE SCHEMA IF NOT EXISTS careflow_mart;
CREATE SCHEMA IF NOT EXISTS careflow_audit;

-- ============================================================================
-- careflow_meta: warehouse metadata
-- ============================================================================

CREATE TABLE IF NOT EXISTS careflow_meta.schema_version (
    schema_version TEXT PRIMARY KEY,
    applied_at_utc TIMESTAMPTZ NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS careflow_meta.load_manifest (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    source_path TEXT,
    source_checksum TEXT,
    source_rows BIGINT,
    loaded_rows BIGINT,
    load_method TEXT,
    status TEXT NOT NULL,
    error_message TEXT,
    duration_seconds DOUBLE PRECISION,
    schema_version TEXT,
    loader_version TEXT,
    started_at_utc TIMESTAMPTZ,
    completed_at_utc TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS careflow_meta.table_registry (
    schema_name TEXT NOT NULL,
    table_name TEXT NOT NULL,
    table_kind TEXT NOT NULL,
    primary_key TEXT,
    gold_source_file TEXT,
    PRIMARY KEY (schema_name, table_name)
);

-- ============================================================================
-- careflow_audit: load and validation audit trail
-- ============================================================================

CREATE TABLE IF NOT EXISTS careflow_audit.load_run (
    run_id TEXT PRIMARY KEY,
    started_at_utc TIMESTAMPTZ NOT NULL,
    completed_at_utc TIMESTAMPTZ,
    gold_manifest_checksum TEXT,
    status TEXT NOT NULL,
    tables_processed INTEGER,
    tables_skipped INTEGER,
    tables_failed INTEGER
);

CREATE TABLE IF NOT EXISTS careflow_audit.load_error (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT NOT NULL,
    table_name TEXT,
    error_message TEXT NOT NULL,
    occurred_at_utc TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS careflow_audit.validation_result (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT,
    check_id TEXT NOT NULL,
    table_name TEXT,
    category TEXT,
    status TEXT NOT NULL,
    details TEXT,
    records_evaluated BIGINT,
    records_failed BIGINT,
    checked_at_utc TIMESTAMPTZ NOT NULL
);

-- ============================================================================
-- careflow_dim: dimensions
-- ============================================================================

CREATE TABLE IF NOT EXISTS careflow_dim.dim_patient (
    patient_key BIGINT PRIMARY KEY,
    patient_id TEXT NOT NULL,
    birth_date DATE,
    death_date DATE,
    gender TEXT,
    race TEXT,
    ethnicity TEXT,
    marital_status TEXT,
    city TEXT,
    state TEXT,
    county TEXT,
    zip TEXT,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    income NUMERIC(18, 2),
    healthcare_expenses NUMERIC(18, 2),
    healthcare_coverage NUMERIC(18, 2),
    is_deceased BOOLEAN,
    age_at_reference_date INTEGER,
    age_group TEXT,
    source_file TEXT,
    source_checksum TEXT,
    transformation_timestamp_utc TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS careflow_dim.dim_provider (
    provider_key BIGINT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    organization_id TEXT,
    provider_name TEXT,
    gender TEXT,
    speciality TEXT,
    city TEXT,
    state TEXT,
    zip TEXT
);

CREATE TABLE IF NOT EXISTS careflow_dim.dim_organization (
    organization_key BIGINT PRIMARY KEY,
    organization_id TEXT NOT NULL,
    organization_name TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    revenue NUMERIC(18, 2),
    utilization INTEGER
);

CREATE TABLE IF NOT EXISTS careflow_dim.dim_payer (
    payer_key BIGINT PRIMARY KEY,
    payer_id TEXT NOT NULL,
    payer_name TEXT,
    ownership TEXT,
    state_headquartered TEXT,
    amount_covered NUMERIC(18, 2),
    amount_uncovered NUMERIC(18, 2),
    revenue NUMERIC(18, 2),
    unique_customers INTEGER,
    member_months INTEGER
);

CREATE TABLE IF NOT EXISTS careflow_dim.dim_date (
    date_key INTEGER PRIMARY KEY,
    full_date DATE NOT NULL,
    day INTEGER,
    day_name TEXT,
    week_of_year INTEGER,
    month INTEGER,
    month_name TEXT,
    quarter INTEGER,
    year INTEGER,
    year_month TEXT,
    is_weekend BOOLEAN
);

CREATE TABLE IF NOT EXISTS careflow_dim.dim_condition (
    condition_key BIGINT PRIMARY KEY,
    code TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS careflow_dim.dim_procedure (
    procedure_key BIGINT PRIMARY KEY,
    code TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS careflow_dim.dim_medication (
    medication_key BIGINT PRIMARY KEY,
    code TEXT NOT NULL,
    description TEXT
);

-- ============================================================================
-- careflow_fact: facts
-- ============================================================================

CREATE TABLE IF NOT EXISTS careflow_fact.fact_encounter (
    encounter_key BIGINT PRIMARY KEY,
    encounter_id TEXT NOT NULL,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    provider_key BIGINT REFERENCES careflow_dim.dim_provider (provider_key),
    provider_key_is_missing BOOLEAN,
    organization_key BIGINT REFERENCES careflow_dim.dim_organization (organization_key),
    organization_key_is_missing BOOLEAN,
    payer_key BIGINT REFERENCES careflow_dim.dim_payer (payer_key),
    payer_key_is_missing BOOLEAN,
    encounter_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    start_timestamp TIMESTAMPTZ,
    stop_timestamp TIMESTAMPTZ,
    encounter_class TEXT,
    encounter_duration_minutes DOUBLE PRECISION,
    base_encounter_cost NUMERIC(18, 2),
    total_claim_cost NUMERIC(18, 2),
    payer_coverage NUMERIC(18, 2),
    patient_responsibility NUMERIC(18, 2),
    patient_responsibility_is_negative BOOLEAN,
    is_inpatient BOOLEAN,
    is_emergency BOOLEAN,
    reason_code TEXT,
    reason_description TEXT
);

CREATE TABLE IF NOT EXISTS careflow_fact.fact_condition (
    condition_event_key BIGINT PRIMARY KEY,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    encounter_key BIGINT,
    condition_key BIGINT REFERENCES careflow_dim.dim_condition (condition_key),
    condition_key_is_missing BOOLEAN,
    start_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    stop_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    is_active BOOLEAN,
    condition_duration_days INTEGER
);

CREATE TABLE IF NOT EXISTS careflow_fact.fact_procedure (
    procedure_event_key BIGINT PRIMARY KEY,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    encounter_key BIGINT,
    procedure_key BIGINT REFERENCES careflow_dim.dim_procedure (procedure_key),
    procedure_key_is_missing BOOLEAN,
    start_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    stop_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    procedure_duration_minutes DOUBLE PRECISION,
    base_cost NUMERIC(18, 2),
    reason_code TEXT,
    reason_description TEXT
);

CREATE TABLE IF NOT EXISTS careflow_fact.fact_medication (
    medication_event_key BIGINT PRIMARY KEY,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    encounter_key BIGINT,
    payer_key BIGINT REFERENCES careflow_dim.dim_payer (payer_key),
    payer_key_is_missing BOOLEAN,
    medication_key BIGINT REFERENCES careflow_dim.dim_medication (medication_key),
    medication_key_is_missing BOOLEAN,
    start_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    stop_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    base_cost NUMERIC(18, 2),
    payer_coverage NUMERIC(18, 2),
    total_cost NUMERIC(18, 2),
    dispenses INTEGER,
    medication_duration_days INTEGER,
    is_active BOOLEAN
);

CREATE TABLE IF NOT EXISTS careflow_fact.fact_observation (
    observation_key BIGINT PRIMARY KEY,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    encounter_key BIGINT,
    observation_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    category TEXT,
    observation_code TEXT,
    description TEXT,
    raw_value TEXT,
    numeric_value DOUBLE PRECISION,
    units TEXT,
    observation_type TEXT
);

CREATE TABLE IF NOT EXISTS careflow_fact.fact_claim (
    claim_key BIGINT PRIMARY KEY,
    claim_id TEXT NOT NULL,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    encounter_key BIGINT,
    provider_key BIGINT REFERENCES careflow_dim.dim_provider (provider_key),
    provider_key_is_missing BOOLEAN,
    payer_key BIGINT REFERENCES careflow_dim.dim_payer (payer_key),
    payer_key_is_missing BOOLEAN,
    service_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    claim_status TEXT,
    outstanding_amount NUMERIC(18, 2),
    claim_type TEXT
);

CREATE TABLE IF NOT EXISTS careflow_fact.fact_immunization (
    immunization_key BIGINT PRIMARY KEY,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    encounter_key BIGINT,
    immunization_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    code TEXT,
    description TEXT,
    base_cost NUMERIC(18, 2)
);

-- Grain made explicit per the Phase 2F/3A finding that imaging_studies.Id
-- is NOT row-level unique: one study can span multiple series/instance
-- rows. The primary key is the composite (study_id, series_uid,
-- instance_uid), matching Gold's imaging_study_composite_key(); study_id
-- alone is never used as a primary key.
CREATE TABLE IF NOT EXISTS careflow_fact.fact_imaging_study (
    imaging_study_key BIGINT PRIMARY KEY,
    study_id TEXT NOT NULL,
    series_uid TEXT,
    instance_uid TEXT,
    patient_key BIGINT REFERENCES careflow_dim.dim_patient (patient_key),
    patient_key_is_missing BOOLEAN,
    encounter_key BIGINT,
    study_date_key INTEGER REFERENCES careflow_dim.dim_date (date_key),
    bodysite_code TEXT,
    modality_code TEXT,
    sop_code TEXT,
    procedure_code TEXT,
    CONSTRAINT uq_fact_imaging_study_natural UNIQUE (study_id, series_uid, instance_uid)
);

-- ============================================================================
-- careflow_mart: analytics marts (derived/aggregated -- no FK constraints,
-- but a primary key wherever the aggregation grain is guaranteed unique)
-- ============================================================================

CREATE TABLE IF NOT EXISTS careflow_mart.mart_patient_360 (
    patient_key BIGINT PRIMARY KEY,
    patient_id TEXT,
    gender TEXT,
    race TEXT,
    ethnicity TEXT,
    age_group TEXT,
    is_deceased BOOLEAN,
    total_encounters INTEGER,
    inpatient_encounters INTEGER,
    emergency_encounters INTEGER,
    total_conditions INTEGER,
    active_conditions INTEGER,
    total_procedures INTEGER,
    total_medications INTEGER,
    total_observations INTEGER,
    total_immunizations INTEGER,
    first_encounter_date DATE,
    last_encounter_date DATE,
    total_claim_cost NUMERIC(18, 2),
    total_payer_coverage NUMERIC(18, 2),
    total_patient_responsibility NUMERIC(18, 2),
    average_encounter_duration_minutes DOUBLE PRECISION,
    most_recent_encounter_class TEXT
);

CREATE TABLE IF NOT EXISTS careflow_mart.mart_readmission (
    index_encounter_key BIGINT PRIMARY KEY,
    patient_key BIGINT,
    index_discharge_timestamp TIMESTAMPTZ,
    next_encounter_key BIGINT,
    next_encounter_timestamp TIMESTAMPTZ,
    days_to_readmission DOUBLE PRECISION,
    readmitted_within_30_days BOOLEAN,
    readmitted_within_7_days BOOLEAN,
    readmitted_within_14_days BOOLEAN,
    index_encounter_class TEXT,
    next_encounter_class TEXT
);

CREATE TABLE IF NOT EXISTS careflow_mart.mart_hospital_operations (
    organization_key BIGINT,
    encounter_date_key INTEGER,
    encounter_class TEXT,
    encounter_count INTEGER,
    unique_patients INTEGER,
    average_duration_minutes DOUBLE PRECISION,
    median_duration_minutes DOUBLE PRECISION,
    inpatient_count INTEGER,
    emergency_count INTEGER,
    total_claim_cost NUMERIC(18, 2),
    payer_coverage NUMERIC(18, 2),
    patient_responsibility NUMERIC(18, 2),
    provider_count INTEGER,
    PRIMARY KEY (organization_key, encounter_date_key, encounter_class)
);

CREATE TABLE IF NOT EXISTS careflow_mart.mart_financial_performance (
    payer_key BIGINT,
    organization_key BIGINT,
    year_month TEXT,
    encounter_count INTEGER,
    total_claim_cost NUMERIC(18, 2),
    total_payer_coverage NUMERIC(18, 2),
    total_patient_responsibility NUMERIC(18, 2),
    average_claim_cost NUMERIC(18, 2),
    coverage_ratio DOUBLE PRECISION,
    PRIMARY KEY (payer_key, organization_key, year_month)
);

CREATE TABLE IF NOT EXISTS careflow_mart.mart_provider_utilization (
    provider_key BIGINT,
    year_month TEXT,
    encounter_count INTEGER,
    unique_patients INTEGER,
    total_procedures INTEGER,
    average_encounter_duration_minutes DOUBLE PRECISION,
    total_claim_cost NUMERIC(18, 2),
    PRIMARY KEY (provider_key, year_month)
);

CREATE TABLE IF NOT EXISTS careflow_mart.mart_monthly_kpis (
    year_month TEXT PRIMARY KEY,
    total_patients_served INTEGER,
    total_encounters INTEGER,
    inpatient_encounters INTEGER,
    emergency_encounters INTEGER,
    average_length_of_stay_minutes DOUBLE PRECISION,
    total_claim_cost NUMERIC(18, 2),
    total_payer_coverage NUMERIC(18, 2),
    total_patient_responsibility NUMERIC(18, 2),
    readmission_count INTEGER,
    readmission_rate_30_day DOUBLE PRECISION,
    average_procedures_per_encounter DOUBLE PRECISION,
    average_medications_per_patient DOUBLE PRECISION
);
