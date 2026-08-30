/**
 * ECR — **one repository, not three.**
 *
 * That is the single most important fact in this module and it is read straight off
 * `docker-compose.yml`: `api`, `worker` and `beat` all declare `image: anvex/api:dev` and
 * differ only in the `command` they run. Three repositories would be three copies of the
 * same layers, three pushes per deployment, and three chances for the worker to be running
 * a different commit from the API that enqueued its work.
 *
 * `modules/compute` builds all three task definitions from `"${repository_url}:${image_tag}"`
 * for exactly the same reason.
 */

resource "aws_ecr_repository" "api" {
  name = "${var.name}/api"

  # `MUTABLE` because the deploy path tags with the commit SHA *and* moves a rolling tag.
  # Immutable tags would make the second push fail. If the deploy path ever stops moving a
  # rolling tag, this should become `IMMUTABLE`.
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    # Basic scanning: free, runs on push, and reports OS-package CVEs. Enhanced scanning is
    # Inspector and is billed per image.
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Storage is billed per gigabyte and a Python image is a few hundred megabytes, so an
# unpruned repository is a bill that grows once per deployment forever. Two rules, and the
# order matters — ECR evaluates by `rulePriority` ascending and an image matches at most one
# rule, so the untagged sweep has to come first or the count rule would claim them.
resource "aws_ecr_lifecycle_policy" "api" {
  repository = aws_ecr_repository.api.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Expire untagged layers left behind by an overwritten tag."
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = 1
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep the most recent ${var.image_retention_count} images."
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.image_retention_count
        }
        action = { type = "expire" }
      },
    ]
  })
}
