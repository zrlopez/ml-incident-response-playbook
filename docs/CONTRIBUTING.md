# Contributing to the ML Incident Response Playbook

Thank you for taking the time to contribute. This project helps MLOps, data, and
platform teams document and operationalize incident response for AI/ML systems.
Contributions of any kind — bug fixes, new runbooks, documentation improvements,
or security hardening — are welcome and appreciated.

---

## Before You Start

- **Check open issues** before beginning work to avoid duplicating effort:
  [github.com/zrlopez/ml-incident-response-playbook/issues](https://github.com/zrlopez/ml-incident-response-playbook/issues)
- **Open an issue first** for non-trivial changes (new runbook templates,
  architectural additions, security controls) so the approach can be
  discussed before you invest significant time.
- **Review the policies** that govern this project:
  - [Code of Conduct](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/CODE_OF_CONDUCT.md)
  - [Security Policy](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/SECURITY.md) — security vulnerabilities must not be
    reported as public issues; follow the private reporting process described there.

---

## Preferred Workflow

1. **Fork** the repository on GitHub.
2. **Create a branch** from `main` using the naming pattern
   `type/short-description`:

   | Type | Example branch name |
   |---|---|
   | `feat` | `feat/gdpr-export-endpoint` |
   | `fix` | `fix/redis-denylist-asyncio` |
   | `docs` | `docs/postmortem-template` |
   | `security` | `security/arch01-rs256-upgrade` |
   | `refactor` | `refactor/user-repository-typing` |
   | `test` | `test/rate-limit-edge-cases` |
   | `chore` | `chore/update-pip-audit-pin` |

3. **Make focused, incremental changes** — one logical concern per branch.
4. **Run the test suite** locally before pushing.
5. **Push** your branch to your fork and **open a Pull Request** against `main`.

---

## Development Setup

```bash
# Clone your fork
git clone https://github.com/your-username/ml-incident-response-playbook.git
cd ml-incident-response-playbook

# Create and activate a virtual environment
python -m venv .venv && source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env        # Fill in required values; see .env.example comments

# Apply database migrations
alembic upgrade head

# Verify seed configuration (dry run, no writes)
python scripts/seed_users.py --dry-run
```

### Running the Test Suite

```bash
pytest tests/ -v --tb=short
```

All tests must pass before opening a PR. New code paths must include corresponding
coverage in `tests/`.

---

## Commit Standards

This project uses [Conventional Commits](https://www.conventionalcommits.org/).

```text
type(scope): short imperative summary

Optional body explaining why the change was made, not just what.
Closes #123
```

**Rules:**
- Use the **imperative mood**: "Fix token revocation" not "Fixed token revocation".
- Keep the summary line under **72 characters**.
- Reference the issue number in the body or footer when one exists.
- Keep commits **atomic** — one logical change per commit makes review and
  bisection easier.

---

## Pull Request Checklist

Before marking your PR ready for review, confirm each item:

- [ ] `pytest tests/ -v` passes locally with no new failures.
- [ ] New code paths have corresponding tests in `tests/`.
- [ ] Docstrings added for all new public functions and classes.
- [ ] No secrets, credentials, or real PII appear anywhere in the diff
  (CI runs a TruffleHog scan and will block the merge if found).
- [ ] `REMEDIATION_LOG.md` updated if the change addresses a tracked
  finding (`ARCH-0*`, `CRIT-0*`, `HIGH-0*`, etc.).
- [ ] `SECURITY.md` updated if the security control surface changed
  (new auth behaviors, new dependency pins, new CI security jobs).
- [ ] `.env.example` updated and documented if new environment variables
  were added.
- [ ] Markdown files (runbooks, templates, policies) follow existing style:
  heading hierarchy, Mermaid diagram syntax, table formatting.
- [ ] Mermaid diagrams render cleanly in GitHub's Markdown preview.

---

## Code Style

| Concern | Convention |
|---|---|
| Formatter | `ruff format .` before every commit |
| Line length | 100 characters |
| Type hints | Required on all new function signatures |
| Logging | `structlog` throughout — no bare `print()` in application code |
| API shapes | Pydantic models for all request and response bodies |
| TODO comments | Must include a tracking issue: `# TODO(#42): description` |
| Secrets | Environment variables only; never hard-coded; documented in `.env.example` |

---

## What We Welcome

- **Bug fixes** in the API, data models, validation logic, or CI configuration.
- **New runbooks and templates** for ML-incident patterns such as data drift,
  model degradation, prompt injection, or training-data incidents.
- **Documentation improvements** across onboarding, deployment, monitoring,
  and operational runbooks.
- **Security hardening** that addresses findings in `REMEDIATION_LOG.md` or
  introduces new controls consistent with the security posture described in
  [SECURITY.md](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/SECURITY.md).
- **Tooling improvements** — scripts, linting rules, additional CI jobs, or
  better test fixtures.

---

## Security Contributions

If your change touches authentication, authorization, secrets handling,
dependency versions, or the CI pipeline, please:

1. Reference the relevant finding ID (e.g., `ARCH-01`, `ARCH-07`) in the
   PR description.
2. Add or update tests in `tests/test_api.py` covering the security behavior.
3. Update [SECURITY.md](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/SECURITY.md) if the active controls table changes.
4. For newly discovered vulnerabilities, **do not open a public issue**.
   Follow the private reporting process in
   [SECURITY.md](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/SECURITY.md).

---

## Documentation Contributions

Runbooks, postmortem templates, and policy documents live in `runbooks/`,
`templates/`, and `docs/`. When adding or updating them:

- Keep all examples **synthetic** — no real hostnames, IP addresses,
  usernames, incident identifiers, or model names.
- Link new documents from the appropriate index file or parent document
  so they are discoverable.
- Mermaid diagrams must render cleanly in GitHub's Markdown preview
  before the PR is marked ready.

---

## Review and Merge Process

- **Initial review:** within 5 business days of the PR being marked ready.
- **Second review or merge:** within 10 business days of the last
  substantive update.
- Maintainers may request additional tests, documentation clarification,
  or scope reduction before merging.
- PRs that add new security controls will be reviewed with particular
  care and may require a longer discussion period.

---

## License and Attribution

This project is licensed under the **MIT License**. By contributing, you
agree that your contributions are licensed under the same terms. See the
[LICENSE](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/LICENSE)
for details. Contributors who report security vulnerabilities are credited
in release notes unless they request anonymity.
