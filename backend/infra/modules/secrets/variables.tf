variable "name" {
  description = "Name prefix (`anvex-dev`)."
  type        = string
}

variable "recovery_window_days" {
  description = <<-EOT
    Days Secrets Manager holds a deleted secret before destroying it.

    0 means immediate, which is right for a throwaway environment and wrong everywhere else:
    a name inside the recovery window cannot be reused, so a destroy/apply cycle fails at
    `create` with `InvalidRequestException` until the window closes.
  EOT
  type        = number
}
