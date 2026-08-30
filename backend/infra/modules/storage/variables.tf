variable "name" {
  description = "Name prefix (`anvex-dev`)."
  type        = string
}

variable "account_id" {
  description = <<-EOT
    The account this is planned against, used only to make the bucket name globally unique.

    Passed in from `data.aws_caller_identity` at the root rather than written down: S3
    bucket names share one namespace across all of AWS, so `anvex-dev-exports` is very
    likely already taken by somebody else.
  EOT
  type        = string
}

variable "force_destroy" {
  description = "Let `terraform destroy` empty the bucket first."
  type        = bool
}

variable "export_expiration_days" {
  description = "Days before an object under the exports prefix expires. 0 disables the rule."
  type        = number
}

variable "export_prefix" {
  description = "Key prefix the application writes exports under; the lifecycle rule is scoped to it."
  type        = string
}
