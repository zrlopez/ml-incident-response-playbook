# src/services — Business orchestration layer.
# Service classes coordinate between repositories, external integrations,
# and domain logic. Route handlers in api/app.py should be thin wrappers
# that delegate to service methods.
#
# Current services:
#   - IncidentService: Manages incident lifecycle orchestration.
#
# Future services (not yet implemented):
#   - AlertService: PagerDuty / OpsGenie integration for SEV1/SEV2 incidents.
#   - OnCallService: On-call rotation assignment on incident creation.
#   - NotificationService: Webhook and Slack notification dispatch.
