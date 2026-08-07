-- (11) Composite imaging-study grain (study_id, series_uid, instance_uid)
-- is unique. study_id ALONE is deliberately never tested as unique (it
-- is not -- see stg_careflow__imaging_studies).
select study_id, series_uid, instance_uid, count(*) as row_count
from {{ ref('stg_careflow__imaging_studies') }}
group by study_id, series_uid, instance_uid
having count(*) > 1
