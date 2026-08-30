variable "name" {
  description = "Name prefix (`anvex-dev`)."
  type        = string
}

variable "image_retention_count" {
  description = "How many images ECR keeps before expiring the oldest."
  type        = number
}
