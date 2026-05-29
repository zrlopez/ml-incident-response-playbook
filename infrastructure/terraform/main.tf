# infrastructure/terraform/main.tf
#
# Minimal AWS infrastructure module for ml-incident-response-playbook.
# Provisions: RDS PostgreSQL, ElastiCache Redis, ECS Fargate cluster + task.
#
# This is a demonstration module — not a zero-click deploy.
# It requires: AWS credentials, a VPC with private subnets, and an ACM cert.
# See variables.tf for all required inputs and README.md for setup instructions.
#
# Changelog:
#   2026-05-26 R-08: Fix Redis auth — split REDIS_URL into REDIS_HOST + REDIS_PORT;
#              inject REDIS_AUTH_TOKEN via ECS secrets[] (Secrets Manager).
#              Prior version embedded empty password in REDIS_URL env var
#              (rediss://:@host) defeating transit encryption auth entirely.

terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
  }
}

provider "aws" {
  region = var.aws_region

  default_tags {
    tags = {
      Project     = "ml-incident-response"
      Environment = var.environment
      ManagedBy   = "terraform"
      Repository  = "github.com/zrlopez/ml-incident-response-playbook"
    }
  }
}

# ── Data sources ──────────────────────────────────────────────────────────────

data "aws_vpc" "selected" {
  id = var.vpc_id
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
  filter {
    name   = "tag:Tier"
    values = ["private"]
  }
}

data "aws_caller_identity" "current" {}

# ── Security groups ───────────────────────────────────────────────────────────

resource "aws_security_group" "api" {
  name        = "${var.project}-api-${var.environment}"
  description = "Allow inbound HTTPS and health check traffic to the API"
  vpc_id      = var.vpc_id

  ingress {
    description = "HTTPS from ALB"
    from_port   = 8000
    to_port     = 8000
    protocol    = "tcp"
    cidr_blocks = [data.aws_vpc.selected.cidr_block]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "rds" {
  name        = "${var.project}-rds-${var.environment}"
  description = "Allow PostgreSQL access from API only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "PostgreSQL from API"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group" "redis" {
  name        = "${var.project}-redis-${var.environment}"
  description = "Allow Redis access from API only"
  vpc_id      = var.vpc_id

  ingress {
    description     = "Redis from API"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.api.id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── RDS PostgreSQL ────────────────────────────────────────────────────────────

resource "random_password" "db" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-${var.environment}"
  subnet_ids = data.aws_subnets.private.ids
}

resource "aws_db_instance" "postgres" {
  identifier             = "${var.project}-${var.environment}"
  engine                 = "postgres"
  engine_version         = "16.2"
  instance_class         = var.rds_instance_class
  allocated_storage      = 20
  max_allocated_storage  = 100
  storage_type           = "gp3"
  storage_encrypted      = true

  db_name  = "incidents"
  username = "incident_user"
  password = random_password.db.result

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  backup_retention_period = 7
  backup_window           = "03:00-04:00"
  maintenance_window      = "Mon:04:00-Mon:05:00"

  deletion_protection       = var.environment == "production"
  skip_final_snapshot       = var.environment != "production"
  final_snapshot_identifier = var.environment == "production" ? "${var.project}-final-snapshot" : null

  performance_insights_enabled = true
  monitoring_interval          = 60

  enabled_cloudwatch_logs_exports = ["postgresql", "upgrade"]

  tags = {
    Name = "${var.project}-postgres-${var.environment}"
  }
}

# Store DB password in Secrets Manager
resource "aws_secretsmanager_secret" "db_password" {
  name                    = "${var.project}/${var.environment}/db_password"
  recovery_window_in_days = var.environment == "production" ? 7 : 0
}

resource "aws_secretsmanager_secret_version" "db_password" {
  secret_id     = aws_secretsmanager_secret.db_password.id
  secret_string = random_password.db.result
}

# ── ElastiCache Redis ─────────────────────────────────────────────────────────

resource "random_password" "redis" {
  length  = 32
  special = false
}

resource "aws_elasticache_subnet_group" "main" {
  name       = "${var.project}-${var.environment}"
  subnet_ids = data.aws_subnets.private.ids
}

resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.project}-${var.environment}"
  description          = "JWT denylist and rate-limit counters for ${var.project}"

  node_type            = var.redis_node_type
  num_cache_clusters   = var.environment == "production" ? 2 : 1
  parameter_group_name = "default.redis7"
  engine_version       = "7.1"
  port                 = 6379

  subnet_group_name  = aws_elasticache_subnet_group.main.name
  security_group_ids = [aws_security_group.redis.id]

  at_rest_encryption_enabled = true
  transit_encryption_enabled = true
  auth_token                 = random_password.redis.result

  automatic_failover_enabled = var.environment == "production"

  tags = {
    Name = "${var.project}-redis-${var.environment}"
  }
}

# Store Redis auth token in Secrets Manager
# The application reads REDIS_AUTH_TOKEN from this secret at runtime.
# REDIS_URL must NOT embed the password inline — use rediss://host:port/db
# and authenticate via AUTH command using the injected secret.
resource "aws_secretsmanager_secret" "redis_password" {
  name                    = "${var.project}/${var.environment}/redis_password"
  recovery_window_in_days = var.environment == "production" ? 7 : 0
}

resource "aws_secretsmanager_secret_version" "redis_password" {
  secret_id     = aws_secretsmanager_secret.redis_password.id
  secret_string = random_password.redis.result
}

# ── ECS Fargate ───────────────────────────────────────────────────────────────

resource "aws_ecs_cluster" "main" {
  name = "${var.project}-${var.environment}"

  setting {
    name  = "containerInsights"
    value = "enabled"
  }
}

resource "aws_iam_role" "ecs_task_execution" {
  name = "${var.project}-ecs-task-execution-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "ecs_task_execution" {
  role       = aws_iam_role.ecs_task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# Allow task execution role to read all three secrets
resource "aws_iam_role_policy" "secrets_access" {
  name = "secrets-read"
  role = aws_iam_role.ecs_task_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["secretsmanager:GetSecretValue"]
      Resource = [
        aws_secretsmanager_secret.db_password.arn,
        aws_secretsmanager_secret.redis_password.arn,
        "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.project}/${var.environment}/jwt_secret*",
      ]
    }]
  })
}

resource "aws_ecs_task_definition" "api" {
  family                   = "${var.project}-api-${var.environment}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.ecs_cpu
  memory                   = var.ecs_memory
  execution_role_arn       = aws_iam_role.ecs_task_execution.arn

  container_definitions = jsonencode([{
    name      = "api"
    image     = "${var.ecr_image_uri}:${var.image_tag}"
    essential = true

    portMappings = [{
      containerPort = 8000
      protocol      = "tcp"
    }]

    # R-08 FIX: Redis auth token MUST NOT be embedded in REDIS_URL.
    # Inject REDIS_HOST and REDIS_PORT as plain env vars; authenticate
    # via REDIS_AUTH_TOKEN injected from Secrets Manager at container start.
    # Application code: redis.Redis(host=REDIS_HOST, port=REDIS_PORT,
    #                               password=REDIS_AUTH_TOKEN, ssl=True)
    environment = [
      { name = "ENVIRONMENT",   value = var.environment },
      { name = "DATABASE_URL",  value = "postgresql+asyncpg://incident_user@${aws_db_instance.postgres.address}:5432/incidents" },
      { name = "REDIS_HOST",    value = aws_elasticache_replication_group.redis.primary_endpoint_address },
      { name = "REDIS_PORT",    value = "6379" },
      { name = "REDIS_SSL",     value = "true" },
    ]

    secrets = [
      {
        name      = "JWT_SECRET_KEY"
        valueFrom = "arn:aws:secretsmanager:${var.aws_region}:${data.aws_caller_identity.current.account_id}:secret:${var.project}/${var.environment}/jwt_secret"
      },
      {
        # R-08: Redis auth token injected securely — never embedded in URL
        name      = "REDIS_AUTH_TOKEN"
        valueFrom = aws_secretsmanager_secret.redis_password.arn
      },
    ]

    healthCheck = {
      command     = ["CMD-SHELL", "curl -f http://localhost:8000/ready || exit 1"]
      interval    = 30
      timeout     = 10
      retries     = 3
      startPeriod = 15
    }

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = "/ecs/${var.project}-${var.environment}"
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "api"
      }
    }
  }])
}

resource "aws_cloudwatch_log_group" "ecs" {
  name              = "/ecs/${var.project}-${var.environment}"
  retention_in_days = 30
}

resource "aws_ecs_service" "api" {
  name            = "${var.project}-api-${var.environment}"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.ecs_desired_count
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = data.aws_subnets.private.ids
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = false
  }

  deployment_minimum_healthy_percent = 50
  deployment_maximum_percent         = 200

  lifecycle {
    ignore_changes = [desired_count]
  }
}
