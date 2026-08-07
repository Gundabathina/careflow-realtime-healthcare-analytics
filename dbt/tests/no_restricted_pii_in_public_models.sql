-- (10) Restricted PII columns (SSN, passport, driver's license, first/
-- middle/last name, street address, precise lat/lon) must not appear in
-- any public reporting model.
{% set public_models = [
    ('careflow_dbt_mart', 'dim_patient_safe'),
    ('careflow_dbt_mart', 'mart_patient_population'),
    ('careflow_dbt_mart', 'mart_readmission_analysis'),
    ('careflow_dbt_mart', 'mart_executive_monthly'),
    ('careflow_dbt_mart', 'fct_encounters'),
    ('careflow_dbt_mart', 'fct_readmissions'),
    ('careflow_dbt_mart', 'fct_claim_financials'),
    ('careflow_dbt_mart', 'fct_provider_activity'),
    ('careflow_dbt_mart', 'dim_provider_reporting'),
    ('careflow_dbt_mart', 'dim_organization_reporting'),
    ('careflow_dbt_mart', 'dim_payer_reporting'),
    ('careflow_dbt_mart', 'mart_financial_analysis'),
    ('careflow_dbt_mart', 'mart_hospital_operations'),
    ('careflow_dbt_mart', 'mart_provider_performance'),
] %}

{% for schema_name, table_name in public_models %}
    {{ assert_no_restricted_pii_columns(schema_name, table_name) }}
    {% if not loop.last %}union all{% endif %}
{% endfor %}
