# Secrets Management Runbook — ARCH-04

**Finding:** ARCH-04 — Application secrets (`JWT_SECRET_KEY`, `DATABASE_URL`, `REDIS_URL`,
dev user passwords) are injected via a plain `.env` file. In production these must be
pulled from a dedicated secrets manager so that:

- Secrets never appear in container images, build logs, or environment dumps.
- Rotation can happen without redeployment.
- Every secret access is audit-logged with principal, timestamp, and secret ARN/path.
- Least-privilege IAM/service-account policies scope access per environment.

**Status:** This runbook documents the implementation pattern. Choose one path based on
your infrastructure.

---

## Prerequisites

The codebase already reads all secrets from environment variables:

```python
JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]   # hard-fails if absent
DATABASE_URL   = os.getenv("DATABASE_URL", "")
REDIS_URL      = os.getenv("REDIS_URL", "redis://localhost:6379/0")
```

No code changes are required. The infra layer populates these variables from the
secrets manager at container start.

---

## Path A — AWS Secrets Manager + ECS / Fargate

### 1. Store secrets

```bash
# JWT signing key (rotate every 90 days via Lambda scheduled rotation)
aws secretsmanager create-secret \
  --name "ml-incident/prod/jwt-secret-key" \
  --secret-string "$(openssl rand -hex 32)" \
  --region us-east-1

# Database URL (includes credentials)
aws secretsmanager create-secret \
  --name "ml-incident/prod/database-url" \
  --secret-string "postgresql+asyncpg://appuser:PASS@rds-host:5432/incidents"

# Redis URL
aws secretsmanager create-secret \
  --name "ml-incident/prod/redis-url" \
  --secret-string "rediss://:PASS@elasticache-host:6380/0"

# Per-user dev passwords (only needed for non-production environments)
aws secretsmanager create-secret \
  --name "ml-incident/staging/dev-passwords" \
  --secret-string '{"admin":"PASS_A","analyst":"PASS_B","operator":"PASS_C"}'
```

### 2. IAM policy for the ECS task role

Create a policy scoped to only the secrets this service needs:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "ReadMlIncidentSecrets",
      "Effect": "Allow",
      "Action": [
        "secretsmanager:GetSecretValue",
        "secretsmanager:DescribeSecret"
      ],
      "Resource": [
        "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:ml-incident/prod/*"
      ]
    }
  ]
}
```

Attach this policy to the ECS task execution role — **not** the instance role.

### 3. ECS Task Definition — secrets injection

In your task definition JSON, reference secrets as environment variables:

```json
{
  "containerDefinitions": [
    {
      "name": "ml-incident-api",
      "image": "YOUR_ECR_IMAGE",
      "secrets": [
        {
          "name": "JWT_SECRET_KEY",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:ml-incident/prod/jwt-secret-key"
        },
        {
          "name": "DATABASE_URL",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:ml-incident/prod/database-url"
        },
        {
          "name": "REDIS_URL",
          "valueFrom": "arn:aws:secretsmanager:us-east-1:ACCOUNT_ID:secret:ml-incident/prod/redis-url"
        }
      ]
    }
  ]
}
```

ECS injects these as environment variables at task startup — the container never has
access to AWS credentials and the secret values never appear in task definition logs.

### 4. Automatic rotation

```bash
# Enable 90-day rotation for the JWT key using the provided Lambda rotator
aws secretsmanager rotate-secret \
  --secret-id "ml-incident/prod/jwt-secret-key" \
  --rotation-rules AutomaticallyAfterDays=90
```

For database credentials, use the [RDS-provided rotation Lambda](
https://docs.aws.amazon.com/secretsmanager/latest/userguide/rotating-secrets-rds.html).

---

## Path B — HashiCorp Vault + Kubernetes

### 1. Enable the KV secrets engine

```bash
vault secrets enable -path=ml-incident kv-v2

# Store secrets
vault kv put ml-incident/prod/api \
  jwt_secret_key="$(openssl rand -hex 32)" \
  database_url="postgresql+asyncpg://appuser:PASS@pg-host:5432/incidents" \
  redis_url="rediss://:PASS@redis-host:6380/0"
```

### 2. Vault policy

```hcl
# vault-policy-ml-incident.hcl
path "ml-incident/data/prod/api" {
  capabilities = ["read"]
}

# Deny all other paths
path "*" {
  capabilities = ["deny"]
}
```

```bash
vault policy write ml-incident-api vault-policy-ml-incident.hcl
```

### 3. Kubernetes auth + ServiceAccount

```bash
# Enable Kubernetes auth method
vault auth enable kubernetes

vault write auth/kubernetes/config \
  kubernetes_host="https://$(kubectl get svc kubernetes -o jsonpath='{.spec.clusterIP}'):443"

# Bind the policy to the ml-incident-api ServiceAccount
vault write auth/kubernetes/role/ml-incident-api \
  bound_service_account_names=ml-incident-api \
  bound_service_account_namespaces=production \
  policies=ml-incident-api \
  ttl=1h
```

### 4. Inject secrets via Vault Agent sidecar

Annotate the deployment pod spec:

```yaml
apiVersion: apps/v1
kind: Deployment
meta
  name: ml-incident-api
spec:
  template:
    meta
      annotations:
        vault.hashicorp.com/agent-inject: "true"
        vault.hashicorp.com/role: "ml-incident-api"
        vault.hashicorp.com/agent-inject-secret-api: "ml-incident/data/prod/api"
        vault.hashicorp.com/agent-inject-template-api: |
          {{- with secret "ml-incident/data/prod/api" -}}
          export JWT_SECRET_KEY="{{ .Data.data.jwt_secret_key }}"
          export DATABASE_URL="{{ .Data.data.database_url }}"
          export REDIS_URL="{{ .Data.data.redis_url }}"
          {{- end }}
    spec:
      serviceAccountName: ml-incident-api
      containers:
        - name: ml-incident-api
          command: ["/bin/sh", "-c"]
          args:
            - "source /vault/secrets/api && exec uvicorn api.app:app --host 0.0.0.0 --port 8000"
```

---

## Path C — GCP Secret Manager + Cloud Run

```bash
# Create secrets
echo -n "$(openssl rand -hex 32)" | \
  gcloud secrets create ml-incident-jwt-secret-key --data-file=-

echo -n "postgresql+asyncpg://user:pass@/incidents?host=/cloudsql/PROJECT:REGION:INSTANCE" | \
  gcloud secrets create ml-incident-database-url --data-file=-

# Grant Cloud Run service account access
gcloud secrets add-iam-policy-binding ml-incident-jwt-secret-key \
  --member="serviceAccount:ml-incident-api@PROJECT.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

In the Cloud Run service definition:

```yaml
apiVersion: serving.knative.dev/v1
kind: Service
spec:
  template:
    spec:
      containers:
        - image: gcr.io/PROJECT/ml-incident-api
          env:
            - name: JWT_SECRET_KEY
              valueFrom:
                secretKeyRef:
                  name: ml-incident-jwt-secret-key
                  key: latest
            - name: DATABASE_URL
              valueFrom:
                secretKeyRef:
                  name: ml-incident-database-url
                  key: latest
```

---

## Verification Checklist

After deploying with any path above, confirm:

- [ ] `docker inspect <container>` shows no plaintext secret values in `Env`
- [ ] `docker history <image>` shows no `ENV JWT_SECRET_KEY=...` layers
- [ ] Application starts cleanly: `GET /health` returns `{"status": "ok"}`
- [ ] `/ready` probe shows `redis_denylist: ok` and `database: ok`
- [ ] AWS CloudTrail / Vault audit log / GCP audit log shows secret access events
- [ ] Rotation test: rotate `JWT_SECRET_KEY`, verify existing tokens are revoked
  via denylist, new logins issue new tokens correctly
- [ ] `.env` file does **not** exist on the production host
- [ ] `.env` is in `.gitignore` and the git history is clean
  (`git log --all --full-diff -p -- .env` returns no secret values)

---

## ARCH-01 Dependency Note

Once secrets management is in place, ARCH-01 (RS256 JWT upgrade) becomes straightforward:
store the RSA private key in the secrets manager and the public key as a non-secret
configuration value. The JWKS endpoint then serves the public key for token verification
by downstream services.

**Estimated effort:** 1–2 days per path once infra is provisioned.
