variable "name" {
  description = "Name prefix (`anvex-dev`)."
  type        = string
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC."
  type        = string
}

variable "availability_zone_count" {
  description = "How many AZs to spread the three subnet tiers across."
  type        = number
}

variable "single_nat_gateway" {
  description = "One NAT gateway for the whole VPC instead of one per AZ."
  type        = bool
}

variable "enable_s3_gateway_endpoint" {
  description = "Create the free S3 gateway endpoint on the private and data route tables."
  type        = bool
}

variable "api_port" {
  description = "Port the API container listens on. The ALB security group opens exactly this on the app SG."
  type        = number
}

variable "postgres_port" {
  description = "Port RDS listens on."
  type        = number
}

variable "redis_port" {
  description = "Port ElastiCache listens on."
  type        = number
}
