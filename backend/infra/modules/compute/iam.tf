/**
 * The two roles an ECS task has, which are not the same role and are easy to conflate.
 *
 * **Execution role** — assumed by the ECS *agent*, before the container exists. It pulls the
 * image, resolves the `secrets` entries, and creates the log streams. This is the role that
 * needs `secretsmanager:GetSecretValue`, and it is scoped to exactly the four secrets
 * `modules/secrets` declares plus the one RDS owns. Not `Resource: "*"`: the whole reason a
 * secret is in Secrets Manager is that reading it is an event with a name attached.
 *
 * **Task role** — assumed by the *application*, inside the container. Today it grants
 * nothing except the SSM channel `ecs execute-command` needs, and the reason is the one in
 * `modules/storage/iam.tf`: `S3Client` refuses to build a client without explicit
 * credentials, so it never uses the ambient identity, so a task-role S3 grant would be
 * permissions nothing exercises. The role exists anyway, attached and empty, because the day
 * the application learns to use the credential chain is the day it becomes the whole answer
 * — and an ECS service cannot gain a task role without a new task definition revision.
 */

data "aws_iam_policy_document" "ecs_assume_role" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

# ------------------------------------------------------------------------- execution role

resource "aws_iam_role" "execution" {
  name               = "${var.name}-ecs-execution"
  description        = "Assumed by the ECS agent: pulls the image, resolves secrets, writes logs."
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

# The managed policy covers ECR pull and CloudWatch Logs. Its ARN is built from the
# partition rather than written out, so no literal ARN appears in this repository.
resource "aws_iam_role_policy_attachment" "execution_managed" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:${var.partition}:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "execution_secrets" {
  name   = "${var.name}-secrets"
  role   = aws_iam_role.execution.id
  policy = data.aws_iam_policy_document.execution_secrets.json
}

data "aws_iam_policy_document" "execution_secrets" {
  statement {
    sid     = "ReadTheSecretsTheTaskDefinitionNames"
    effect  = "Allow"
    actions = ["secretsmanager:GetSecretValue"]

    # Exactly the secrets referenced by `local.container_secrets`, and the RDS-owned one.
    # A secret added there and forgotten here fails the task at start, loudly, rather than
    # being covered by a wildcard nobody reviews.
    resources = concat(
      values(var.secret_arns),
      [var.postgres_master_secret_arn],
    )
  }

  # Only needed if the secrets are ever re-encrypted under a customer-managed key; with the
  # AWS-managed `aws/secretsmanager` key the grant is implicit. Stated so that switching to
  # a CMK is one resource change rather than a debugging session.
  statement {
    sid       = "PullTheImage"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "PullFromThisRepositoryOnly"
    effect = "Allow"

    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
    ]

    resources = [var.image_repository_arn]
  }
}

# ------------------------------------------------------------------------------ task role

resource "aws_iam_role" "task" {
  name               = "${var.name}-ecs-task"
  description        = "Assumed by the application. See the header: deliberately near-empty today."
  assume_role_policy = data.aws_iam_policy_document.ecs_assume_role.json
}

resource "aws_iam_role_policy" "task" {
  name   = "${var.name}-task"
  role   = aws_iam_role.task.id
  policy = data.aws_iam_policy_document.task.json
}

data "aws_iam_policy_document" "task" {
  # `aws ecs execute-command` — a shell in a running task, which on Fargate is the only way
  # to run `alembic upgrade head` against RDS without a bastion. The channel is SSM's; the
  # actions do not name a resource because the session id does not exist yet.
  statement {
    sid    = "ExecuteCommandChannel"
    effect = "Allow"

    actions = [
      "ssmmessages:CreateControlChannel",
      "ssmmessages:CreateDataChannel",
      "ssmmessages:OpenControlChannel",
      "ssmmessages:OpenDataChannel",
    ]

    resources = ["*"]
  }

  # There is deliberately **no S3 statement here**, and the omission is the interesting part
  # of this file. The day `S3Client` learns to use the credential chain (TODO(ANV-s3-aws) in
  # `locals.tf`), this role takes an `s3_bucket_arn` input and grants the exports prefix,
  # and `modules/storage`'s IAM user and its Secrets Manager entry both get deleted. Until
  # then a grant here would be permission for something nothing does.
}
