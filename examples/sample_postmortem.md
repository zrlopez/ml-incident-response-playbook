# Sample Postmortem

## Summary

Upstream schema drift caused invalid feature values to enter the scoring pipeline.

## Action Items

- Add schema validation.
- Quarantine bad records.
- Improve alert thresholds.
