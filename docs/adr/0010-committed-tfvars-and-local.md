# ADR-0010 — `.tfvars` are committed, and `local` is not a deployment

## Status

**Accepted** — ANV-40. Recorded in ANV-39.

## Context

The standard advice is to gitignore `*.tfvars`, and the reason is sound: tfvars files are
where people put passwords. `.gitignore` templates for Terraform ship with that rule already
in them, and this repository's does too.

But [ADR-0009](./0009-no-secrets-in-terraform.md) already guarantees the thing the rule
protects against — no secret value exists anywhere in this configuration, by construction and
by test. What is left in `backend/infra/envs/*.tfvars` is a *variable layout*: region,
instance classes, desired counts, worker concurrency, whether the ALB has deletion protection.
Ignoring that means the layout exists only on the machine of whoever wrote it, and a second
person's `terraform plan` is a different plan.

Separately, the cost of this estate is the number that decides whether it is ever stood up,
and a cost estimate needs a floor that is not invented. The obvious floor is "the compose
stack, in AWS, one of everything, no backups" — but writing that as a *deployable
environment* invites somebody to deploy it.

## Decision

**Both `.tfvars` files are committed**, with an explicit `!backend/infra/envs/*.tfvars`
exception in `.gitignore` beside the general `*.tfvars` rule.
`backend/tests/unit/test_infra_terraform.py` asserts both the exception and the absence of
anything secret-shaped in the files, and asserts that every variable without a default is set
by **both** files.

**`local` is a variable set, not a deployment.** It is the sizing that mirrors
`docker-compose.yml` one for one, and the file says so twice. `dev` is the environment
somebody would actually stand up.

## Consequences

**A reviewer can diff the two topologies.** `local` versus `dev` is a readable diff of what
changes when the thing becomes real, and `local` versus `docker-compose.yml` is a readable
diff of what changes when the compose stack becomes AWS. Neither is possible if one side is
gitignored.

**The cost table has an honest floor**, and it is worse than people expect: **≈ $110/month**
for the `local` shape and **≈ $161/month** for a usable `dev`, of which the ALB and the NAT
gateway are **~$55/month before a single container runs** — half the floor, fixed, and
untunable. There is no $20 version. `docs/aws-deployment.md` itemises the five levers worth
having and what each one costs you.

**Somebody will read `local` as a deployment anyway.** That is the risk this creates, and
the mitigation is two statements in the file itself plus this record. The alternative —
deleting `local` — would leave the cost document quoting a floor nothing describes.

**Every variable without a default must be set twice**, so adding one is two edits, not one,
and the suite fails until both are made. That is deliberate: a variable set in `dev` and
forgotten in `local` makes the floor a fiction.

**`single_nat_gateway = true` in both**, which is the one place the two files agree on
something that would normally differ. AZ-independent egress costs $33.75/month for an
environment whose entire premise is that it can be down for an hour. **`X86_64` in both**,
even though ARM64 is ~20% cheaper, because an x86 image pushed to an ARM64 task definition
fails at task *start* rather than at deploy — a cheaper architecture is not worth a failure
mode that only appears after the rollout begins.
