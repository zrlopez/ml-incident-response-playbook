# infrastructure/terraform

Minimal AWS infrastructure module for `ml-incident-response-playbook`.

Provisions a single-region, production-capable stack:
- **RDS PostgreSQL 16** (encrypted at rest, automated backups, Performance Insights)
- **ElastiCache Redis 7** (TLS in-transit, auth token, optional multi-AZ)
- **ECS Fargate** cluster + task definition + service (2 replicas, awsvpc networking)
- **IAM roles** with least-privilege Secrets Manager access
- **CloudWatch log group** with 30-day retention
- **AWS Secrets Manager** entries for DB password, Redis auth token

> This module is a demonstration of IaC literacy. It is not a zero-click deploy —
> it requires an existing VPC with private subnets tagged `Tier=private`.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Terraform >= 1.7 | `brew install terraform` or [tfenv](https://github.com/tfutils/tfenv) |
| AWS CLI configured | `aws configure` or environment credentials |
| VPC with private subnets | Subnets must be tagged `Tier=private` |
| ECR repository | Push your image first; provide URI as `ecr_image_uri` |
| JWT secret in Secrets Manager | Path: `ml-incident/<env>/jwt_secret` |

---

## Quick Start

```bash
cd infrastructure/terraform

# 1. Copy and edit the example tfvars
cp terraform.tfvars.example terraform.tfvars
# Edit terraform.tfvars with your VPC ID, ECR URI, etc.

# 2. Initialise
terraform init

# 3. Plan (review before applying)
terraform plan -var-file=terraform.tfvars

# 4. Apply
terraform apply -var-file=terraform.tfvars
```

---

## Required Variables

| Variable | Description | Example |
|---|---|---|
| `environment` | `development`, `staging`, or `production` | `production` |
| `vpc_id` | ID of the target VPC | `vpc-0abc123` |
| `ecr_image_uri` | ECR repo URI without tag | `123456789.dkr.ecr.us-east-1.amazonaws.com/ml-incident-api` |

See [`variables.tf`](./variables.tf) for all optional variables and defaults.

---

## Example tfvars

```hcl
# terraform.tfvars.example — copy to terraform.tfvars and fill in
environment      = "staging"
vpc_id           = "vpc-0abc123def456"
ecr_image_uri    = "123456789012.dkr.ecr.us-east-1.amazonaws.com/ml-incident-api"
image_tag        = "sha-abc1234"
rds_instance_class = "db.t3.small"
redis_node_type  = "cache.t3.micro"
ecs_desired_count = 2
```

---

## Post-Apply Checklist

- [ ] Create the JWT secret in Secrets Manager: `aws secretsmanager create-secret --name ml-incident/<env>/jwt_secret --secret-string "$(python3 -c 'import secrets; print(secrets.token_hex(32))')"`
- [ ] Run database bootstrap: `SEED_ADMIN=1 ... ./scripts/bootstrap_db.sh`
- [ ] Verify ECS service health: `aws ecs describe-services --cluster ml-incident-<env> --services ml-incident-api-<env>`
- [ ] Confirm `/ready` probe: `curl https://<alb-dns>/ready`

---

## Security Notes

- All secrets are stored in AWS Secrets Manager and injected via ECS task `secrets` (not environment variables)
- RDS and Redis are in private subnets with security groups restricted to the API task only
- `deletion_protection = true` is enforced in production
- Redis uses TLS (`transit_encryption_enabled = true`) and an auth token
