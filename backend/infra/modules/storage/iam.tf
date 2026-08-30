/**
 * Who may touch the exports bucket — and why this is an IAM **user** rather than a role.
 *
 * A Fargate task role would be the obvious answer, and it is the wrong one here, because of
 * something the application does on purpose. `app/clients/s3.py::_require_configuration`
 * refuses to construct a client when `S3_ACCESS_KEY_ID` or `S3_SECRET_ACCESS_KEY` is blank,
 * and its module docstring says exactly why: an `aioboto3` client built without explicit
 * credentials falls back to botocore's default chain — environment, `~/.aws/credentials`,
 * the instance profile — so a deployment with a blank secret would not fail, it would
 * quietly authenticate as whatever identity was lying around and write to a real bucket.
 * That guard is deliberate and it is good. Its consequence is that the app **must** be
 * handed a static key pair, which means an IAM user.
 *
 * So: the user and its policy are declared here, and **no `aws_iam_access_key` resource
 * exists anywhere in this tree**. Creating one would write the secret access key into
 * Terraform state in plaintext. The key is created out of band by an operator and pasted
 * into the Secrets Manager secret `modules/secrets` declares; `docs/aws-deployment.md`
 * spells out the four commands. `test_infra_terraform.py` fails if an `aws_iam_access_key`
 * ever appears.
 *
 * Moving the app to a task role is a real follow-up and a small one — it means letting
 * `s3_access_key_id` / `s3_secret_access_key` be empty *and* `s3_endpoint_url` be null, and
 * relying on the SDK's chain. It is deliberately not done in this ticket, which changes no
 * application code. See `README.md`, "Two things the application cannot do yet".
 */

resource "aws_iam_user" "app_s3" {
  name = "${var.name}-app-s3"
  path = "/service/"
}

resource "aws_iam_user_policy" "app_s3" {
  name   = "${var.name}-app-s3"
  user   = aws_iam_user.app_s3.name
  policy = data.aws_iam_policy_document.app_s3.json
}

data "aws_iam_policy_document" "app_s3" {
  # Listing is scoped to the exports prefix rather than the bucket: the app never needs to
  # enumerate anything else, and `s3:prefix` is the condition key that enforces it.
  statement {
    sid    = "ListExportsPrefix"
    effect = "Allow"

    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.exports.arn]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["${var.export_prefix}*", var.export_prefix]
    }
  }

  # Objects, and only under the exports prefix. `PutObject`, `GetObject`, `DeleteObject` and
  # `HeadObject` (which is `GetObject` in IAM) are the whole of what `S3Client` calls;
  # `AbortMultipartUpload` is what a failed large upload needs to clean up after itself.
  statement {
    sid    = "ReadWriteExports"
    effect = "Allow"

    actions = [
      "s3:PutObject",
      "s3:GetObject",
      "s3:DeleteObject",
      "s3:AbortMultipartUpload",
    ]

    resources = ["${aws_s3_bucket.exports.arn}/${var.export_prefix}*"]
  }
}
