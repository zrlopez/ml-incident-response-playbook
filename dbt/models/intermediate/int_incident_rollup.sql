select
    category,
    severity,
    count(*) as incident_count
from {{ ref('stg_incidents') }}
group by 1, 2
