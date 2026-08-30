/**
 * Routing, the NAT gateways, and the free S3 gateway endpoint.
 *
 * There is one public route table (the internet gateway is VPC-wide), one private route
 * table per NAT gateway, and one data route table with **no default route at all**.
 */

# ------------------------------------------------------------------------------ public

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name}-public"
  }
}

resource "aws_route" "public_default" {
  route_table_id         = aws_route_table.public.id
  destination_cidr_block = "0.0.0.0/0"
  gateway_id             = aws_internet_gateway.this.id
}

resource "aws_route_table_association" "public" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# --------------------------------------------------------------------------------- NAT

resource "aws_eip" "nat" {
  count = local.nat_gateway_count

  domain = "vpc"

  tags = {
    Name = "${var.name}-nat-${count.index}"
  }
}

resource "aws_nat_gateway" "this" {
  count = local.nat_gateway_count

  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id

  # The gateway is useless before the IGW has a route, and Terraform cannot infer that from
  # the arguments — neither resource references the other.
  depends_on = [aws_route.public_default]

  tags = {
    Name = "${var.name}-nat-${count.index}"
  }
}

# ----------------------------------------------------------------------------- private

resource "aws_route_table" "private" {
  count = local.nat_gateway_count

  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name}-private-${count.index}"
  }
}

resource "aws_route" "private_default" {
  count = local.nat_gateway_count

  route_table_id         = aws_route_table.private[count.index].id
  destination_cidr_block = "0.0.0.0/0"
  nat_gateway_id         = aws_nat_gateway.this[count.index].id
}

resource "aws_route_table_association" "private" {
  count = var.availability_zone_count

  subnet_id = aws_subnet.private[count.index].id

  # With `single_nat_gateway` there is one table and every AZ points at it; otherwise the
  # index lines up and each AZ egresses through its own.
  route_table_id = aws_route_table.private[var.single_nat_gateway ? 0 : count.index].id
}

# -------------------------------------------------------------------------------- data

# No default route. Postgres and Redis have no business reaching the internet, and a subnet
# with nowhere to go is a cheaper guarantee of that than any egress rule.
resource "aws_route_table" "data" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "${var.name}-data"
  }
}

resource "aws_route_table_association" "data" {
  count = var.availability_zone_count

  subnet_id      = aws_subnet.data[count.index].id
  route_table_id = aws_route_table.data.id
}

# ------------------------------------------------------------------------ S3 endpoint

# A gateway endpoint costs nothing per hour and nothing per gigabyte. Without it every
# export the app writes, and every image layer ECR serves out of S3, crosses the NAT
# gateway at per-gigabyte rates — which for a container that pulls a ~400 MB image on every
# deployment is a real number, not a rounding error.
resource "aws_vpc_endpoint" "s3" {
  count = var.enable_s3_gateway_endpoint ? 1 : 0

  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${data.aws_region.current.name}.s3"
  vpc_endpoint_type = "Gateway"

  route_table_ids = concat(
    aws_route_table.private[*].id,
    [aws_route_table.data.id],
  )

  tags = {
    Name = "${var.name}-s3"
  }
}

# The region is read from the provider rather than passed in, so this module has one fewer
# input that could disagree with the provider it is actually running under.
data "aws_region" "current" {}
