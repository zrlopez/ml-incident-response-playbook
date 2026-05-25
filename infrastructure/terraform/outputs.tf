# infrastructure/terraform/outputs.tf

output "api_cluster_arn" {
  description = "ARN of the ECS cluster"
  value       = aws_ecs_cluster.main.arn
}

output "api_service_name" {
  description = "Name of the ECS service"
  value       = aws_ecs_service.api.name
}

output "rds_endpoint" {
  description = "RDS PostgreSQL endpoint hostname"
  value       = aws_db_instance.postgres.address
  sensitive   = true
}

output "rds_port" {
  description = "RDS PostgreSQL port"
  value       = aws_db_instance.postgres.port
}

output "redis_primary_endpoint" {
  description = "ElastiCache Redis primary endpoint"
  value       = aws_elasticache_replication_group.redis.primary_endpoint_address
  sensitive   = true
}

output "db_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the DB password"
  value       = aws_secretsmanager_secret.db_password.arn
}

output "redis_secret_arn" {
  description = "ARN of the Secrets Manager secret holding the Redis auth token"
  value       = aws_secretsmanager_secret.redis_password.arn
}

output "task_execution_role_arn" {
  description = "ARN of the ECS task execution IAM role"
  value       = aws_iam_role.ecs_task_execution.arn
}

output "cloudwatch_log_group" {
  description = "CloudWatch log group name for ECS container logs"
  value       = aws_cloudwatch_log_group.ecs.name
}
