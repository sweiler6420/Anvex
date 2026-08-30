output "vpc_id" {
  description = "The VPC every other module attaches to."
  value       = aws_vpc.this.id
}

output "public_subnet_ids" {
  description = "Subnets the ALB is placed in."
  value       = aws_subnet.public[*].id
}

output "private_subnet_ids" {
  description = "Subnets the ECS tasks get their ENIs in."
  value       = aws_subnet.private[*].id
}

output "data_subnet_ids" {
  description = "Subnets for the RDS and ElastiCache subnet groups."
  value       = aws_subnet.data[*].id
}

output "alb_security_group_id" {
  description = "Security group for the load balancer."
  value       = aws_security_group.alb.id
}

output "app_security_group_id" {
  description = "Security group shared by the api, worker and beat tasks."
  value       = aws_security_group.app.id
}

output "db_security_group_id" {
  description = "Security group for the RDS instance."
  value       = aws_security_group.db.id
}

output "cache_security_group_id" {
  description = "Security group for the ElastiCache replication group."
  value       = aws_security_group.cache.id
}

output "availability_zones" {
  description = "The AZs actually chosen, in order. Useful when reading a plan."
  value       = local.azs
}
