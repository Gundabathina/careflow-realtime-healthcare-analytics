-- (5) 7-day readmissions are also 14-day and 30-day readmissions.
{{ assert_implies(ref('int_readmission_events'), 'readmitted_within_7_days', 'readmitted_within_14_days') }}
union all
{{ assert_implies(ref('int_readmission_events'), 'readmitted_within_7_days', 'readmitted_within_30_days') }}
