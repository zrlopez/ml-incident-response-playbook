select status
from {{ ref('stg_incidents') }}
where status is null
