-- (2) No encounter stop before start.
select *
from {{ ref('stg_careflow__encounters') }}
where start_timestamp is not null
  and stop_timestamp is not null
  and stop_timestamp < start_timestamp
