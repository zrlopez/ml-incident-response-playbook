{% snapshot incidents_snapshot %}
{{ config(target_schema='snapshots', unique_key='incident_id', strategy='timestamp', updated_at='updated_at') }}
select * from {{ source('operational', 'incident_events') }}
{% endsnapshot %}
