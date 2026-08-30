/**
 * The exports bucket — the AWS half of docker-compose's `minio` service.
 *
 * `app/clients/s3.py` talks to MinIO locally and to this bucket in AWS through the same
 * `aioboto3` client; `app/domain/storage.py` owns the key layout and `app/services/storage.py`
 * decides what goes in it. Nothing about that changes here.
 *
 * The bucket is private in four independent ways — a public access block, an ownership
 * control that disables ACLs entirely, default encryption, and a policy that refuses
 * unencrypted transport. Any one of them being enough is the point: the failure mode this
 * guards against is somebody clicking something in the console.
 */

resource "aws_s3_bucket" "exports" {
  # Bucket names are global. The account id suffix is what makes this one ours; it is read
  # from the caller's identity at plan time and appears in no source file.
  bucket        = "${var.name}-exports-${var.account_id}"
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "exports" {
  bucket = aws_s3_bucket.exports.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ACLs off. With `BucketOwnerEnforced` the only access control is the bucket policy and IAM,
# which means there is one place to read to know who can get at an object.
resource "aws_s3_bucket_ownership_controls" "exports" {
  bucket = aws_s3_bucket.exports.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "exports" {
  bucket = aws_s3_bucket.exports.id

  rule {
    apply_server_side_encryption_by_default {
      # SSE-S3, not SSE-KMS. KMS bills per request, and an export bucket is written and read
      # object-by-object; the upgrade is a decision with a number attached, not a default.
      sse_algorithm = "AES256"
    }

    bucket_key_enabled = true
  }
}

# Versioning on. An export is derived data and could be regenerated, but a delete here is
# usually a bug in the lifecycle rule below rather than an intention.
resource "aws_s3_bucket_versioning" "exports" {
  bucket = aws_s3_bucket.exports.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "exports" {
  bucket = aws_s3_bucket.exports.id

  # Versioning above means a delete leaves a noncurrent version behind, so this rule has to
  # expire both halves or the bill grows forever while the listing looks empty.
  dynamic "rule" {
    for_each = var.export_expiration_days > 0 ? [1] : []

    content {
      id     = "expire-exports"
      status = "Enabled"

      filter {
        prefix = var.export_prefix
      }

      expiration {
        days = var.export_expiration_days
      }

      noncurrent_version_expiration {
        noncurrent_days = var.export_expiration_days
      }
    }
  }

  # Unconditional, and separate from the rule above: a multipart upload that fails leaves
  # billable parts that appear in no listing at all.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"

    filter {}

    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }

  depends_on = [aws_s3_bucket_versioning.exports]
}

# TLS or nothing. `aws:SecureTransport` is false only for plain HTTP, and a presigned URL
# handed to a browser is exactly the kind of thing that ends up on one by accident.
resource "aws_s3_bucket_policy" "exports" {
  bucket = aws_s3_bucket.exports.id
  policy = data.aws_iam_policy_document.exports_bucket.json

  depends_on = [aws_s3_bucket_public_access_block.exports]
}

data "aws_iam_policy_document" "exports_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    actions = ["s3:*"]

    resources = [
      aws_s3_bucket.exports.arn,
      "${aws_s3_bucket.exports.arn}/*",
    ]

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}
