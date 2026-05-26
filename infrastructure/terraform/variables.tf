# infrastructure/terraform/variables.tf

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment: development, staging, or production"
  type        = string
  validation {
    condition     = contains(["development", "staging", "production"], var.environment)
    error_message = "environment must be one of: development, staging, production"
  }
}

variable "project" {
  description = "Project identifier used in resource naming"
  type        = string
  default     = "ml-incident"
}

variable "vpc_id" {
  description = "ID of the VPC to deploy into. Subnets tagged Tier=private will be used."
  type        = string
}

variable "rds_instance_class" {
  description = "RDS instance class for PostgreSQL"
  type        = string
  default     = "db.t3.micro"
  validation {
    condition     = can(regex("^db\\.", var.rds_instance_class))
    error_message = "rds_instance_class must start with 'db.' (e.g. db.t3.micro, db.t3.small)"
  }
}

variable "redis_node_type" {
  description = "ElastiCache node type for Redis"
  type        = string
  default     = "cache.t3.micro"
}

variable "ecs_cpu" {
  description = "ECS task CPU units (256 = 0.25 vCPU)"
  type        = number
  default     = 256
}

variable "ecs_memory" {
  description = "ECS task memory in MiB"
  type        = number
  default     = 512
}

variable "ecs_desired_count" {
  description = "Desired number of running ECS task instances"
  type        = number
  default     = 2
}

variable "ecr_image_uri" {
  description = "ECR repository URI for the API image (without tag)"
  type        = string
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}
