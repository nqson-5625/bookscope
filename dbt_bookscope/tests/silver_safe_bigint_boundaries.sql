with cases(value, expected) as (
  values
    ('0', 0::bigint),
    ('000000000000000000001', 1::bigint),
    ('9223372036854775807', 9223372036854775807::bigint),
    ('9223372036854775808', null::bigint),
    ('-1', null::bigint),
    ('1.0', null::bigint),
    ('', null::bigint)
)
select value, expected, {{ safe_nonnegative_bigint('value') }} as observed
from cases
where {{ safe_nonnegative_bigint('value') }} is distinct from expected
