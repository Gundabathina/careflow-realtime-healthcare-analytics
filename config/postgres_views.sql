-- CareFlow Analytics - PostgreSQL reporting views (Phase 3B)
--
-- PII strategy: Gold's dim_patient already excludes SSN, passport,
-- driver's license, street address, and full patient name (those columns
-- were never carried past the Silver -> Gold patient dimension build in
-- Phase 3A). The one patient-level field that IS present and precise
-- enough to be sensitive is latitude/longitude -- these views never
-- select dim_patient.latitude or dim_patient.longitude. Only coarse
-- geography (state, county, zip) is exposed, which is standard for
-- healthcare population-health reporting.
--
-- Idempotent (CREATE OR REPLACE VIEW).

CREATE OR REPLACE VIEW careflow_mart.vw_patient_summary AS
SELECT
    m.patient_key,
    m.patient_id,
    m.gender,
    m.race,
    m.ethnicity,
    m.age_group,
    m.is_deceased,
    d.state,
    d.county,
    d.zip,
    m.total_encounters,
    m.inpatient_encounters,
    m.emergency_encounters,
    m.total_conditions,
    m.active_conditions,
    m.total_procedures,
    m.total_medications,
    m.total_observations,
    m.total_immunizations,
    m.first_encounter_date,
    m.last_encounter_date,
    m.total_claim_cost,
    m.total_payer_coverage,
    m.total_patient_responsibility,
    m.average_encounter_duration_minutes,
    m.most_recent_encounter_class
FROM careflow_mart.mart_patient_360 m
LEFT JOIN careflow_dim.dim_patient d ON d.patient_key = m.patient_key;

CREATE OR REPLACE VIEW careflow_mart.vw_readmission_analysis AS
SELECT
    r.index_encounter_key,
    r.patient_key,
    p.age_group,
    p.gender,
    r.index_discharge_timestamp,
    r.next_encounter_key,
    r.next_encounter_timestamp,
    r.days_to_readmission,
    r.readmitted_within_7_days,
    r.readmitted_within_14_days,
    r.readmitted_within_30_days,
    r.index_encounter_class,
    r.next_encounter_class
FROM careflow_mart.mart_readmission r
LEFT JOIN careflow_dim.dim_patient p ON p.patient_key = r.patient_key;

CREATE OR REPLACE VIEW careflow_mart.vw_hospital_operations AS
SELECT
    h.organization_key,
    o.organization_name,
    o.city,
    o.state,
    d.full_date AS encounter_date,
    d.year_month,
    h.encounter_class,
    h.encounter_count,
    h.unique_patients,
    h.average_duration_minutes,
    h.median_duration_minutes,
    h.inpatient_count,
    h.emergency_count,
    h.total_claim_cost,
    h.payer_coverage,
    h.patient_responsibility,
    h.provider_count
FROM careflow_mart.mart_hospital_operations h
LEFT JOIN careflow_dim.dim_organization o ON o.organization_key = h.organization_key
LEFT JOIN careflow_dim.dim_date d ON d.date_key = h.encounter_date_key;

CREATE OR REPLACE VIEW careflow_mart.vw_financial_performance AS
SELECT
    f.payer_key,
    pay.payer_name,
    f.organization_key,
    o.organization_name,
    f.year_month,
    f.encounter_count,
    f.total_claim_cost,
    f.total_payer_coverage,
    f.total_patient_responsibility,
    f.average_claim_cost,
    f.coverage_ratio
FROM careflow_mart.mart_financial_performance f
LEFT JOIN careflow_dim.dim_payer pay ON pay.payer_key = f.payer_key
LEFT JOIN careflow_dim.dim_organization o ON o.organization_key = f.organization_key;

CREATE OR REPLACE VIEW careflow_mart.vw_provider_utilization AS
SELECT
    u.provider_key,
    pr.provider_name,
    pr.speciality,
    pr.organization_id,
    u.year_month,
    u.encounter_count,
    u.unique_patients,
    u.total_procedures,
    u.average_encounter_duration_minutes,
    u.total_claim_cost
FROM careflow_mart.mart_provider_utilization u
LEFT JOIN careflow_dim.dim_provider pr ON pr.provider_key = u.provider_key;

CREATE OR REPLACE VIEW careflow_mart.vw_monthly_kpis AS
SELECT
    year_month,
    total_patients_served,
    total_encounters,
    inpatient_encounters,
    emergency_encounters,
    average_length_of_stay_minutes,
    total_claim_cost,
    total_payer_coverage,
    total_patient_responsibility,
    readmission_count,
    readmission_rate_30_day,
    average_procedures_per_encounter,
    average_medications_per_patient
FROM careflow_mart.mart_monthly_kpis;
