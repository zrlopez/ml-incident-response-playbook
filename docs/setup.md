# Setup Instructions

## Prerequisites

- Git
- Python 3.11+
- Docker and Docker Compose
- Mermaid support if you want to preview runbook diagrams locally

## Local Setup

```bash
git clone https://github.com/zrlopez/ml-incident-response-playbook.git
cd ml-incident-response-playbook
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
python scripts/seed_users.py --dry-run
```

## Local Services

```bash
docker compose up -d postgres redis prometheus grafana
uvicorn api.app:app --reload --port 8000
```

Verify the service:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

## Quality Gates

```bash
pytest -q
ruff check .
mypy api src ml_models observability pipelines scripts tests
```

## Portfolio Review Path

If you are reviewing the artifact rather than running it, start with:

1. [Operational walkthrough](walkthrough.md)
2. [Architecture](architecture.md)
3. [Model card](https://github.com/zrlopez/ml-incident-response-playbook/blob/main/MODEL_CARD.md)
4. [Runbooks](https://github.com/zrlopez/ml-incident-response-playbook/tree/main/runbooks)
5. [CI conventions](ci-conventions.md)
