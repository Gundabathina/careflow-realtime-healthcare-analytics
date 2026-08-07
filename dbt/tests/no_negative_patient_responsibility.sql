-- (1) No negative patient responsibility that isn't flagged. A negative
-- value can legitimately occur (payer overpayment relative to total
-- claim cost); the design is to flag it via
-- patient_responsibility_is_negative, never to silently allow an
-- unflagged negative value through.
select *
from {{ ref('stg_careflow__encounters') }}
where patient_responsibility < 0
  and not patient_responsibility_is_negative
