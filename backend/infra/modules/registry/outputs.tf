output "repository_url" {
  description = "The one repository all three services pull from."
  value       = aws_ecr_repository.api.repository_url
}

output "repository_arn" {
  description = "ARN of the repository, for the ECS execution role's pull permissions."
  value       = aws_ecr_repository.api.arn
}
