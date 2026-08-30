output "arns" {
  description = "Secret suffix => ARN. `modules/compute` builds every `valueFrom` out of this."
  value       = { for key, secret in aws_secretsmanager_secret.this : key => secret.arn }
}

output "names" {
  description = "The full secret names an operator has to populate before the first deploy."
  value       = sort([for secret in aws_secretsmanager_secret.this : secret.name])
}
