/**
 * The VPC and its three subnet tiers.
 *
 * ```
 *   public   ALB, NAT gateway            -> internet gateway
 *   private  ECS tasks (api/worker/beat) -> NAT gateway, no inbound route from the internet
 *   data     RDS, ElastiCache            -> no egress at all
 * ```
 *
 * Three tiers rather than two because the data tier has no route to a NAT gateway. A
 * database with no default route cannot be exfiltrated *through*, which is a property worth
 * having and costs nothing.
 *
 * The tiers are carved out of `var.vpc_cidr` with `cidrsubnet`, so the whole address plan
 * follows from one variable. With a /16 that is a /20 per subnet (4,094 usable addresses) —
 * ECS `awsvpc` gives every task its own ENI and therefore its own address, so the private
 * tier is the one that actually has to be big.
 */

data "aws_availability_zones" "available" {
  state = "available"

  filter {
    # Local Zones and Wavelength Zones come back from this data source too, and neither
    # supports RDS or Fargate. `opt-in-status` is what separates a real AZ from them.
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.availability_zone_count)

  # Tier offsets within the VPC CIDR. Four bits of extra prefix gives sixteen /20s out of a
  # /16; three tiers of up to three AZs uses nine of them and leaves room to grow.
  public_cidrs  = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 4, index)]
  private_cidrs = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 4, index + 4)]
  data_cidrs    = [for index in range(var.availability_zone_count) : cidrsubnet(var.vpc_cidr, 4, index + 8)]

  # One NAT gateway, or one per AZ. See `var.single_nat_gateway` at the root: this is the
  # most expensive decision in the configuration.
  nat_gateway_count = var.single_nat_gateway ? 1 : var.availability_zone_count
}

resource "aws_vpc" "this" {
  cidr_block = var.vpc_cidr

  # Both are required for RDS and ElastiCache to publish resolvable endpoint names, which
  # is what `POSTGRES_HOST` and `REDIS_HOST` are set to.
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = var.name
  }
}

resource "aws_internet_gateway" "this" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = var.name
  }
}
