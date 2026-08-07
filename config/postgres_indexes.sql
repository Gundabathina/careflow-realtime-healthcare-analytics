-- CareFlow Analytics - PostgreSQL warehouse indexes (Phase 3B)
--
-- Idempotent (CREATE INDEX IF NOT EXISTS). Targets common analytics
-- join/filter columns on fact and mart tables. Dimension tables are
-- small (tens to a few hundred rows) and already have a primary-key
-- index, so no secondary indexes are added there to avoid redundant
-- index maintenance overhead on tiny tables.

-- fact_encounter: the most frequently joined/filtered fact table
CREATE INDEX IF NOT EXISTS ix_fact_encounter_patient_key ON careflow_fact.fact_encounter (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_encounter_provider_key ON careflow_fact.fact_encounter (provider_key);
CREATE INDEX IF NOT EXISTS ix_fact_encounter_organization_key ON careflow_fact.fact_encounter (organization_key);
CREATE INDEX IF NOT EXISTS ix_fact_encounter_payer_key ON careflow_fact.fact_encounter (payer_key);
CREATE INDEX IF NOT EXISTS ix_fact_encounter_date_key ON careflow_fact.fact_encounter (encounter_date_key);
CREATE INDEX IF NOT EXISTS ix_fact_encounter_class ON careflow_fact.fact_encounter (encounter_class);
CREATE INDEX IF NOT EXISTS ix_fact_encounter_start_timestamp ON careflow_fact.fact_encounter (start_timestamp);
CREATE INDEX IF NOT EXISTS ix_fact_encounter_stop_timestamp ON careflow_fact.fact_encounter (stop_timestamp);

-- fact_condition
CREATE INDEX IF NOT EXISTS ix_fact_condition_patient_key ON careflow_fact.fact_condition (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_condition_encounter_key ON careflow_fact.fact_condition (encounter_key);
CREATE INDEX IF NOT EXISTS ix_fact_condition_condition_key ON careflow_fact.fact_condition (condition_key);

-- fact_procedure
CREATE INDEX IF NOT EXISTS ix_fact_procedure_patient_key ON careflow_fact.fact_procedure (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_procedure_encounter_key ON careflow_fact.fact_procedure (encounter_key);
CREATE INDEX IF NOT EXISTS ix_fact_procedure_procedure_key ON careflow_fact.fact_procedure (procedure_key);

-- fact_medication
CREATE INDEX IF NOT EXISTS ix_fact_medication_patient_key ON careflow_fact.fact_medication (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_medication_encounter_key ON careflow_fact.fact_medication (encounter_key);
CREATE INDEX IF NOT EXISTS ix_fact_medication_medication_key ON careflow_fact.fact_medication (medication_key);
CREATE INDEX IF NOT EXISTS ix_fact_medication_payer_key ON careflow_fact.fact_medication (payer_key);

-- fact_observation (largest fact table)
CREATE INDEX IF NOT EXISTS ix_fact_observation_patient_key ON careflow_fact.fact_observation (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_observation_encounter_key ON careflow_fact.fact_observation (encounter_key);
CREATE INDEX IF NOT EXISTS ix_fact_observation_date_key ON careflow_fact.fact_observation (observation_date_key);

-- fact_claim
CREATE INDEX IF NOT EXISTS ix_fact_claim_patient_key ON careflow_fact.fact_claim (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_claim_encounter_key ON careflow_fact.fact_claim (encounter_key);
CREATE INDEX IF NOT EXISTS ix_fact_claim_provider_key ON careflow_fact.fact_claim (provider_key);
CREATE INDEX IF NOT EXISTS ix_fact_claim_payer_key ON careflow_fact.fact_claim (payer_key);
CREATE INDEX IF NOT EXISTS ix_fact_claim_service_date_key ON careflow_fact.fact_claim (service_date_key);

-- fact_immunization
CREATE INDEX IF NOT EXISTS ix_fact_immunization_patient_key ON careflow_fact.fact_immunization (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_immunization_encounter_key ON careflow_fact.fact_immunization (encounter_key);

-- fact_imaging_study
CREATE INDEX IF NOT EXISTS ix_fact_imaging_study_patient_key ON careflow_fact.fact_imaging_study (patient_key);
CREATE INDEX IF NOT EXISTS ix_fact_imaging_study_encounter_key ON careflow_fact.fact_imaging_study (encounter_key);

-- mart_readmission: readmission flags are the primary analytics filter
CREATE INDEX IF NOT EXISTS ix_mart_readmission_patient_key ON careflow_mart.mart_readmission (patient_key);
CREATE INDEX IF NOT EXISTS ix_mart_readmission_within_7 ON careflow_mart.mart_readmission (readmitted_within_7_days);
CREATE INDEX IF NOT EXISTS ix_mart_readmission_within_14 ON careflow_mart.mart_readmission (readmitted_within_14_days);
CREATE INDEX IF NOT EXISTS ix_mart_readmission_within_30 ON careflow_mart.mart_readmission (readmitted_within_30_days);

-- mart_patient_360: patient_key is already the primary key (indexed)

-- year_month filters (composite primary keys on these marts already lead
-- with the grouping columns most-queried alongside year_month, but a
-- dedicated index makes "all payers/providers in month X" scans direct)
CREATE INDEX IF NOT EXISTS ix_mart_financial_performance_year_month ON careflow_mart.mart_financial_performance (year_month);
CREATE INDEX IF NOT EXISTS ix_mart_provider_utilization_year_month ON careflow_mart.mart_provider_utilization (year_month);
