# Commit Message Convention

This repository follows [Conventional Commits](https://www.conventionalcommits.org/).

## Format

```
<type>(scope): short imperative summary

[optional body — bullet details, reasoning]

[optional footer — Closes #42, BREAKING CHANGE: ...]
```

## Rules

- Subject line **≤ 72 characters**
- Use **imperative mood**: "add", "fix", "remove" — not "added", "fixed"
- No capitalisation of first word after the colon
- No period at end of subject line
- Reference issues in footer: `Closes #42`

## Types

| Type | When to use |
|------|-------------|
| `feat` | New feature or capability |
| `fix` | Bug fix |
| `ci` | CI/CD pipeline changes |
| `chore` | Maintenance, dependency bumps, tooling |
| `docs` | Documentation only |
| `refactor` | Code restructure with no behaviour change |
| `test` | Adding or updating tests |
| `perf` | Performance improvement |
| `revert` | Reverting a previous commit |

## Scope (optional)

Use the affected module or area: `api`, `auth`, `db`, `observability`, `ci`, `deps`

## Examples

```
feat(auth): add argon2 password hashing for user credentials

fix(ci): pin codeql upload-sarif to v3 — v4 breaks sarif path resolution

chore(deps): bump pytest-asyncio 0.24.0 -> 0.26.0

ci(sast): add bandit hard gate for medium severity findings
```

## What to Avoid

- `fix stuff`
- `WIP`
- `asdf` / `test` / `misc`
- Vague messages like `update code` or `changes`
- Multiple unrelated changes in one commit — split them
