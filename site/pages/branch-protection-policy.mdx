# Branch Protection Policy

**Repository:** `zrlopez/ml-incident-response-playbook`  
**Branch:** `main`  
**Last reviewed:** 2026-05-28  
**Owner:** @zrlopez

---

This document describes the required GitHub branch protection settings for `main`.
A reviewer can verify the current configuration at:
`Settings → Branches → main → Edit`

---

## Required Status Checks

The following CI jobs must pass before any PR can merge:

| Job Name | Workflow | Purpose |
|---|---|---|
| `secrets-scan` | `secured_ci.yml` | TruffleHog credential scan |
| `dependency-audit` | `secured_ci.yml` | pip-audit CVE gate |
| `lockfile-check` | `secured_ci.yml` | requirements.txt freshness (CI-53) |
| `sast` | `secured_ci.yml` | Bandit + mypy + Semgrep |
| `unit-tests` | `secured_ci.yml` | pytest unit suite (fail_under=80) |
| `integration-tests` | `secured_ci.yml` | pytest integration suite (fail_under=40) |
| `container-scan` | `secured_ci.yml` | Trivy CRITICAL/HIGH hard gate + SBOM |

---

## Branch Protection Rules

```
GitHub Settings → Branches → main
```

- [x] Require a pull request before merging
  - [x] Require approvals: 1 (self-review for solo project — waived; enforce for team)
  - [x] Dismiss stale pull request approvals when new commits are pushed
- [x] Require status checks to pass before merging
  - [x] Require branches to be up to date before merging
  - [x] Required checks: (see table above)
- [x] Require conversation resolution before merging
- [x] Require signed commits
  - Enforces GPG/SSH commit signing; prevents commit spoofing
  - Local setup: `git config --global commit.gpgsign true`
- [x] Do not allow bypassing the above settings
- [ ] Restrict who can push to matching branches
  - Recommended for team repos: restrict to @zrlopez + CI service accounts
  - Currently waived for solo portfolio development velocity

---

## CODEOWNERS Enforcement

All security-sensitive paths require explicit approval from `@zrlopez`.
See `.github/CODEOWNERS` for the full path list.

To activate: `Settings → Branches → main → Require review from Code Owners`

---

## Signed Commits

All commits to `main` should be GPG or SSH signed. Steps to configure locally:

```bash
# Generate a signing key (if not already done)
gpg --full-generate-key

# Configure git to use it
git config --global user.signingkey <KEY_ID>
git config --global commit.gpgsign true

# Verify signing is active
git log --show-signature -1
```

For SSH signing (simpler):
```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
```

---

## Related

- `.github/CODEOWNERS` — path-level ownership rules
- `.github/workflows/secured_ci.yml` — full CI pipeline
- `SECURITY.md` — vulnerability disclosure policy
- `docs/adr/ADR-002-jwt-algorithm-selection.md` — RS256 signing rationale
