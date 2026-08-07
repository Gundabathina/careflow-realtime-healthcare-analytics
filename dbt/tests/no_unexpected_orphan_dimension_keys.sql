-- (12) No unexpected orphan dimension keys on fact_encounter's core
-- dimension references (already enforced by database FK constraints in
-- Phase 3B and by relationships tests, but re-verified here explicitly).
with orphans as (
    select 'fact_encounter.patient_key' as relationship, count(*) as orphan_count
    from {{ ref('stg_careflow__encounters') }} e
    left join {{ ref('stg_careflow__patients') }} p on e.patient_key = p.patient_key
    where e.patient_key is not null and p.patient_key is null

    union all

    select 'fact_encounter.provider_key', count(*)
    from {{ ref('stg_careflow__encounters') }} e
    left join {{ ref('stg_careflow__providers') }} pr on e.provider_key = pr.provider_key
    where e.provider_key is not null and pr.provider_key is null

    union all

    select 'fact_encounter.organization_key', count(*)
    from {{ ref('stg_careflow__encounters') }} e
    left join {{ ref('stg_careflow__organizations') }} o on e.organization_key = o.organization_key
    where e.organization_key is not null and o.organization_key is null

    union all

    select 'fact_encounter.payer_key', count(*)
    from {{ ref('stg_careflow__encounters') }} e
    left join {{ ref('stg_careflow__payers') }} pay on e.payer_key = pay.payer_key
    where e.payer_key is not null and pay.payer_key is null
)
select *
from orphans
where orphan_count > 0
