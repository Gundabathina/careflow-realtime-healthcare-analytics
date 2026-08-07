-- (3) No self-readmission: index and next encounter must never be the same encounter.
select *
from {{ ref('int_readmission_events') }}
where next_encounter_key is not null
  and index_encounter_key = next_encounter_key
