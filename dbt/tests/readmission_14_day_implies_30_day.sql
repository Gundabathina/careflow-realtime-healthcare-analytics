-- (6) 14-day readmissions are also 30-day readmissions.
{{ assert_implies(ref('int_readmission_events'), 'readmitted_within_14_days', 'readmitted_within_30_days') }}
