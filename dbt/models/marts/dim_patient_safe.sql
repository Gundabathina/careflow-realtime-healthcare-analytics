-- Public-safe patient dimension. Only the fields explicitly documented as
-- safe are selected: patient_key, age group, gender, race, ethnicity,
-- marital status, city, state, county, ZIP (synthetic data). No
-- patient_id, no lat/lon, no name, no SSN/passport/license/address.
select
    patient_key,
    age_group,
    gender,
    race,
    ethnicity,
    marital_status,
    city,
    state,
    county,
    zip
from {{ ref('stg_careflow__patients') }}
