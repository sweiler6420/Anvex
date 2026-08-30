output "bucket_name" {
  description = "The exports bucket. This is what S3_BUCKET is set to."
  value       = aws_s3_bucket.exports.bucket
}

output "bucket_arn" {
  description = "ARN of the exports bucket."
  value       = aws_s3_bucket.exports.arn
}

output "app_iam_user_name" {
  description = <<-EOT
    The IAM user whose access key the application uses.

    No key is created here — see `iam.tf`. `aws iam create-access-key --user-name <this>` is
    the out-of-band step, and its output goes straight into the Secrets Manager secret.
  EOT
  value       = aws_iam_user.app_s3.name
}
