with source as (
    select * from {{ source('operational', 'incident_events') }}
)
select
    incident_id,
    category,
    severity,
    status,
    owner,
    created_at,
    updated_at
from source
