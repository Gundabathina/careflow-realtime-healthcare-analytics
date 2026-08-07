-- (4) No negative readmission intervals.
{{ assert_non_negative(ref('int_readmission_events'), 'days_to_readmission') }}
