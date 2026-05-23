select
    category,
    sum(incident_count) as total_incidents
from {{ ref('int_incident_rollup') }}
group by 1
