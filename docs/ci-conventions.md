# CI/CD Pipeline Conventions

**Document ID:** CI-CONV-001
**Version:** 1.0.0
**Owner:** Platform Engineering Lead
**Last Updated:** 2026-05-25
**Review Cycle:** Quarterly (next review: 2026-08-25)
**Classification:** Internal — Operational

---

## 1. Purpose and Scope

This document is the authoritative governing standard for all CI/CD pipeline
artifacts and commit conventions in this repository. It supersedes and
incorporates `.github/COMMIT_CONVENTION.md` (archived 2026-05-25).

It governs: commit message format, workflow file structure, in-file changelog
format, sequential CI identifier tracking, action versioning and SHA-pinning
policy, digest reference block scope, SARIF seeding, gate type definitions,
secret and permission hygiene, artifact retention, and concurrency policy.

All pipeline changes must conform to this document. Where any thread-level
instruction or external guide conflicts with this document, this document wins.
Deviations require an explicit `[GAP: ...]` declaration and resolution before
the change is merged.

**Governing standards (authoritative external sources):**

| Standard | Authority | Governs |
|---|---|---|
| Conventional Commits v1.0.0 | [conventionalcommits.org](https://www.conventionalcommits.org/en/v1.0.0/) | Commit message format |
| Semantic Versioning v2.0.0 | [semver.org](https://semver.org) | Version bump rules |
| NIST SP 800-218 SSDF v1.1 | [csrc.nist.gov](https://csrc.nist.gov/pubs/sp/800/218/final) | CI security gate design |
| NIST SP 800-161r1 C-SCRM | [csrc.nist.gov](https://csrc.nist.gov/publications/detail/sp/800-161/rev-1/final) | Action supply-chain pinning |
| GitHub Security Hardening for Actions | [docs.github.com](https://docs.github.com/en/actions/security-for-github-actions) | Permission scope, secret hygiene |
| GitHub Build System Best Practices | [docs.github.com](https://docs.github.com/en/code-security/tutorials/implement-supply-chain-best-practices/securing-builds) | Artifact provenance, immutable builds |

---

## 2. Commit Message Convention

*(Migrated from `.github/COMMIT_CONVENTION.md`, archived 2026-05-25)*

### 2.1 Format

```
<type>(<scope>): short imperative summary

[optional body — bullet details, reasoning]

[optional footer — Closes #42, BREAKING CHANGE: ...]
```

### 2.2 Rules

- Subject line **≤ 72 characters**
- Use **imperative mood**: "add", "fix", "remove" — not "added", "fixed"
- No capitalisation of first word after the colon
- No period at end of subject line
- Reference issues in footer: `Closes #42`

### 2.3 Types

| Type | When to use |
|---|---|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `ci` | CI/CD pipeline changes |
| `chore` | Maintenance, dependency bumps, tooling |
| `docs` | Documentation only |
| `refactor` | Code restructure with no behaviour change |
| `test` | Adding or updating tests |
| `perf` | Performance improvement |
| `revert` | Reverting a previous commit |

### 2.4 Scope (optional)

Use the affected module or area: `api`, `auth`, `db`, `observability`, `ci`,
`deps`, `docs`, `sast`, `test`

### 2.5 Examples

```
feat(auth): add argon2 password hashing for user credentials

fix(ci): pin codeql upload-sarif to v3 — v4 breaks sarif path resolution

chore(deps): bump pytest-asyncio 0.24.0 -> 0.26.0

ci(sast): add bandit hard gate for medium severity findings
```

### 2.6 What to Avoid

- `fix stuff` / `WIP` / `asdf` / `misc`
- Vague messages like `update code` or `changes`
- Multiple unrelated changes in one commit — split them

---

## 3. Workflow File Inventory

| File | Purpose | Trigger |
|---|---|---|
| `.github/workflows/secured_ci.yml` | Primary security-gated CI pipeline | push: main/develop; PR: main |
| `.github/workflows/docs.yml` | Vercel docs-site health check | push: main; PR: main; workflow_dispatch |
| `.github/workflows/mermaid-render.yml` | Render `docs/diagrams/*.mmd` to PNG; commits output back to branch | push: main/develop on `*.mmd` path; workflow_dispatch |

**File naming convention:** `kebab-case.yml`. Workflow files must declare a
meaningful `name:` field matching their stated purpose.

---

## 4. In-File Changelog Convention

Every workflow file must carry a changelog header block in the following format,
immediately below the file-level descriptive comment block:

```
# Changelog (Conventional Commits — type(scope): summary):
#   YYYY-MM-DD vX.Y.Z CI-NN type(scope): summary
```

### 4.1 CI Identifier (CI-NN)

- Every changelog entry carries a globally sequential CI identifier:
  `CI-01`, `CI-02`, ... `CI-NN`.
- The identifier is **globally sequential across all workflow files** —
  not per-file. CI-36 in `docs.yml` follows CI-35 in `secured_ci.yml`.
- The identifier never resets. Reverted entries retain their original
  `CI-NN`; the revert commit receives its own new identifier.
- **Current highest identifier: CI-36.** Update this line in the same
  commit as any new changelog entry.

### 4.2 Version Bump Rules (semver.org v2.0.0)

| Change type | Bump | Example |
|---|---|---|
| Breaking: gate order change, job removal, permission scope reduction | **major** | v1.x.x → v2.0.0 |
| New job, new security gate, new tool integration | **minor** | v1.2.x → v1.3.0 |
| Bug fix, path correction, config adjustment, dep bump | **patch** | v1.2.3 → v1.2.4 |
| Docs-only, comment update, chore | **patch** | v1.2.3 → v1.2.4 |

Version is **per-workflow-file** — `secured_ci.yml` and `docs.yml` version
independently.

### 4.3 Commit Type Mapping for CI Changelog

| Type | When to use in CI context |
|---|---|
| `feat` | New job, new security scanner, new test tier |
| `fix` | Correcting a broken step, wrong path, failed action pin |
| `refactor` | Restructuring jobs without behaviour change |
| `chore` | Dependency bumps, SHA updates, comment cleanup |
| `docs` | Workflow comment or reference block update only |
| `ci` | Trigger event changes, concurrency policy changes |

---

## 5. Action Versioning and SHA-Pinning Policy

All third-party and GitHub-owned actions must be pinned to a **full commit
SHA**, not a mutable tag. Mutable tags can be silently moved to a malicious
commit without any change visible in the workflow file — SHA pinning is the
only defense against this class of supply-chain attack.

**Governing authority:** NIST SP 800-161r1 §3.1 (C-SCRM: verify integrity of
third-party components); GitHub Security Hardening for Actions — "Using
third-party actions."

### 5.1 Pinning Format

```yaml
# Correct — full SHA pin with tag comment:
uses: actions/checkout@ef36d1093e2975a02b4c6a03c8b73990a59a478f  # v4.3.0

# Incorrect — mutable tag:
uses: actions/checkout@v4.3.0

# Incorrect — branch ref:
uses: actions/checkout@main
```

### 5.2 SHA Verification

SHAs must be verified via the GitHub API (`get_tag` or `get_commit`) before
being written into a workflow file. Do not copy SHAs from third-party sources
without independent verification. Verification command:

```bash
gh api repos/<owner>/<repo>/git/ref/tags/<tag>
```

### 5.3 Per-File Digest Reference Block

Each workflow file must carry a digest reference block documenting the actions
**used in that file only**. The block is scoped to the file — it does not
duplicate actions from other workflow files.

This document (Section 5.4) is the authoritative cross-repository reference map.

**Format:**

```
# =============================================================================
# ACTION SHA DIGEST REFERENCE (actions used in this workflow):
#   <action>@<tag>
#     -> <full-commit-sha>
# =============================================================================
```

### 5.4 Cross-Repository Action Reference Map

| Action | Tag | Verified SHA | Used In |
|---|---|---|---|
| `actions/checkout` | v4.3.0 | `ef36d1093e2975a02b4c6a03c8b73990a59a478f` | secured_ci.yml, mermaid-render.yml |
| `actions/setup-python` | v5.6.0 | `a26af69be951a213d495a4c3e4e4022e16d87065` | secured_ci.yml |
| `actions/setup-node` | v4.4.0 | `49933ea5288caeca8642d1e84afbd3f7d6820020` | mermaid-render.yml |
| `actions/upload-artifact` | v4.6.2 | `ea165f8d65b6e75b540449e92b4886f43607fa02` | secured_ci.yml |
| `docker/setup-buildx-action` | v4.1.0 | `d7f5e7f509e45cec5c76c4d5afdd7de93d0b3df5` | secured_ci.yml |
| `docker/build-push-action` | v7.2.0 | `f9f3042f7e2789586610d6e8b85c8f03e5195baf` | secured_ci.yml |
| `aquasecurity/trivy-action` | v0.36.0 (apt path) | `ed142fd0673e97e23eac54620cfb913e5ce36c25` | secured_ci.yml |
| `anchore/sbom-action` | v0.24.0 | `e22c389904149dbc22b58101806040fa8d37a610` | secured_ci.yml |
| `trufflesecurity/trufflehog` | v3.95.3 | `37b77001d0174ebec2fcca2bd83ff83a6d45a3ab` | secured_ci.yml |
| `github/codeql-action/*` | v4.36.0 | `7211b7c8077ea37d8641b6271f6a365a22a5fbfa` | secured_ci.yml |
| `actions/dependency-review-action` | v5.0.0 | `a1d282b36b6f3519aa1f3fc636f609c47dddb294` | secured_ci.yml |
| `semgrep/semgrep-action` | v1.1.0 | `713efdd345f3035192eaa63f56867b88e63e4e5d` | secured_ci.yml |


**Known gap — `mermaid-render.yml`:**
`[GAP: mermaid-render.yml uses actions/checkout@v4.3.0 as a tag-only pin,
not a SHA pin. Bring into compliance with Section 5.1 in the next
mermaid-render.yml changelog entry (CI-37).]`

---

## 6. Permissions and Secrets Hygiene

**Governing authority:** GitHub Security Hardening for Actions — "Permissions
for the GITHUB_TOKEN"; NIST SP 800-218 PW.2 (Review software for security
vulnerabilities).

### 6.1 Principle of Least Privilege

Workflow-level permissions must be set to the minimum required. Default for
all workflows in this repository:

```yaml
permissions:
  contents: read
  pull-requests: read
  security-events: write
```

Job-level permissions override workflow-level where a job requires elevated
access. Never grant `write-all` or omit the `permissions:` block entirely
(which defaults to the repository's maximum token permissions).

### 6.2 Secret Handling Rules

- Secrets must **never** appear in workflow `run:` steps as literal values,
  `echo` output, or environment variable debug output.
- All secrets must be stored in GitHub Actions Encrypted Secrets or
  GitHub Environments — never in workflow files, `.env` files committed to
  the repo, or workflow `env:` blocks as literals.
- Secrets passed via `env:` to a step are acceptable; secrets passed via
  `with:` inputs to a third-party action require the action to be SHA-pinned
  per Section 5.
- Required CI secrets for `secured_ci.yml`:

| Secret | Purpose | Job |
|---|---|---|
| `CI_JWT_SECRET_KEY` | JWT signing for integration tests | integration-tests |
| `CI_REDIS_PASSWORD` | Redis auth for integration tests | integration-tests |
| `DEV_ADMIN_PASSWORD` | Seed admin user | integration-tests |
| `DEV_ANALYST_PASSWORD` | Seed analyst user | integration-tests |
| `DEV_OPERATOR_PASSWORD` | Seed operator user | integration-tests |
| `SEMGREP_APP_TOKEN` | Semgrep Cloud platform token | sast (optional) |

### 6.3 Secret Presence Verification

For any job that requires a secret to function correctly, add an explicit
presence check as the first step:

```yaml
- name: Verify required CI secrets are present
  run: |
    if [ -z "${{ secrets.MY_SECRET }}" ]; then
      echo "ERROR: MY_SECRET is not configured."
      exit 1
    fi
```

---

## 7. Gate Order and Job Contract

The canonical job execution order for `secured_ci.yml`:

```
secrets-scan
  ├── dependency-audit ──────────────────────────────┐
  │                                                   ├── unit-tests
  └── sast ──────────────────────────────────────────┘
                                                           └── integration-tests
                                                                 └── container-scan
                                                                       └── deploy-gate
dependency-review  (PR only, independent)
```

### 7.1 Job Contract Requirements

Each job must satisfy:

| Field | Requirement |
|---|---|
| `name:` | Meaningful; matches the job's security function |
| `needs:` | Explicitly declared; never left implicit |
| `permissions:` | Scoped to minimum required |
| Gate type | Declared in a comment: `# Gate type: hard` or `# Gate type: observational` |
| Artifacts | Retention period explicitly set |

### 7.2 Gate Types

- **Hard gate:** Job fails and blocks the pipeline on finding. No
  `continue-on-error: true` on the gating step.
- **Observational gate:** Produces a SARIF or report artifact but does not
  block merge. Must use `continue-on-error: true` and `if: always()` on
  the upload step.

### 7.3 Artifact Retention Policy

| Artifact type | Minimum retention |
|---|---|
| Security scan results (SARIF, pip-audit) | 90 days |
| Test coverage reports | 30 days |
| SBOM (SPDX-JSON) | 365 days |
| Build logs | GitHub default (90 days) |

---

## 8. SARIF Seeding Policy

To prevent upload failures when a scan step exits non-zero before writing
output, every SARIF file must be seeded with a valid empty document **before**
the scan step that produces it.

**Seed format:**

```bash
echo '{"version":"2.1.0","$schema":"https://json.schemastore.org/sarif-2.1.0.json",
"runs":[{"tool":{"driver":{"name":"<ToolName>","rules":[]}},"results":[]}]}' \
  > <output-file>.sarif
```

All SARIF uploads must use `if: always()` to ensure the upload runs regardless
of whether the preceding scan step passed or failed.

**Stable SARIF filenames in this repository:**

| Tool | Filename |
|---|---|
| Bandit (app) | `bandit-app.sarif` |
| Bandit (tests) | `bandit-tests.sarif` |
| Semgrep | `semgrep.sarif` |
| Trivy (container) | `trivy-container.sarif` |

---

## 9. Concurrency Policy

All workflows must declare a concurrency group to prevent redundant parallel
runs. Cancel-in-progress must be enabled for all CI and docs workflows.

```yaml
concurrency:
  group: <prefix>-${{ github.ref }}
  cancel-in-progress: true
```

**Prefix conventions:**

| Workflow | Group prefix |
|---|---|
| `secured_ci.yml` | `ci` |
| `docs.yml` | `docs` |
| `mermaid-render.yml` | `mermaid` |

**Exception:** Do not use `cancel-in-progress: true` for deployment workflows
that write to production environments — a cancelled in-progress deploy can
leave infrastructure in a partially applied state.

---

## 10. Runner and Environment Policy

- All jobs use `ubuntu-latest` unless a specific OS dependency requires
  otherwise. Document any deviation with a comment stating the reason.
- Python version is pinned explicitly in `setup-python`
  (`python-version: '3.11'`) — never `python-version: 'latest'` or
  `python-version: '3.x'`.
- Node version is pinned explicitly in `setup-node` (`node-version: '20'`).
- Docker base images are pinned to a specific version tag
  (e.g., `postgres:16-alpine`, `redis:7.4.3-alpine`) — never `latest`.

---

## 11. Same-Commit Discipline

Any change to a CI/CD convention, workflow file, or action version must be
accompanied by the relevant documentation update in the **same commit**.

Mandatory same-commit checklist:

- [ ] Relevant workflow file updated
- [ ] In-file changelog entry added (`CI-NN` incremented, version bumped)
- [ ] **This file (`docs/ci-conventions.md`) updated:**
  - Section 4.1: current highest `CI-NN`
  - Section 5.4: reference map if any action version changed
- [ ] Vercel docs navigation updated if a new public docs page was added

---

## 12. Gap Declaration Policy

Unresolved conventions, ambiguities, or missing definitions must be declared
inline:

```
[GAP: description of what is not yet defined or verified]
```

A `[GAP: ...]` marker is not a placeholder to be filled with a plausible
answer. It remains until the responsible party provides the information needed
to close it. When a gap is closed, the resolution source is documented inline
and the marker is removed.

---

## 13. Changelog

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-05-25 | Initial creation; migrated content from `.github/COMMIT_CONVENTION.md` (archived); incorporated CI changelog, action pinning, SARIF seeding, gate order, permissions, secrets hygiene, concurrency, and runner policy |
