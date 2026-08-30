"""Assertions about the AWS Terraform skeleton (ANV-40).

``backend/infra/`` is configuration nothing in this repository executes and nothing has ever
applied, which puts it in the same category as ``.github/workflows/ci.yml``: the easiest kind
of file to break silently. ``terraform validate`` catches a syntax error and a bad reference
and stops there — it has no opinion about whether the task definitions still match what
``app/settings.py`` reads, whether a variable turns anything, or whether somebody pasted an
account id into a public repository.

So this module asserts six kinds of thing, in rising order of how much they matter:

1. **The tree is the shape it claims to be.** Every module the root calls exists, every
   module on disk is called, and both `.tfvars` files are there.
2. **The variables are honest.** Everything declared is referenced, everything referenced is
   declared, everything without a default is set by *both* environments, and every module
   call passes every required input.
3. **Nothing account-specific, region-specific or secret-shaped is written down.** No twelve
   digit number, no literal region, no literal partition in an ARN, no key-shaped string —
   and, structurally, no ``aws_secretsmanager_secret_version`` and no ``aws_iam_access_key``
   resource, because both write a plaintext secret into Terraform state.
4. **The environment contract has not drifted.** ``local.container_environment`` and
   ``local.container_secrets`` together are exactly ``Settings``' fields, upper-cased. This
   is the highest-value assertion in the module and it is the same shape as ANV-43's
   client/server password-rule drift test: two files edited months apart by people who are
   not looking at each other.
5. **The AWS estate mirrors the compose stack.** The worker and beat command lines, the image
   versions, the ports, the export prefix and its retention are all read off the local
   topology rather than invented, so this asserts they still agree.
6. **Local development does not depend on any of it, and nothing applies it.** No script, no
   workflow, no compose service mentions Terraform, and ``terraform apply`` appears nowhere.

Parsing is done with a real HCL2 parser rather than with regexes over the source, for the
reason ``test_ci_workflow.py`` gives about YAML: a matcher that quietly mis-models the
language reports that the configuration is correct when it is not. The parser also separates
comments out into ``__comments__`` keys, which :func:`_strip_meta` discards — so the prose in
those files, which quotes the rules below constantly, is never mistaken for a violation of
them.

Like :mod:`tests.unit.test_ci_workflow` this is a fast, fixtureless module that reads files
outside ``backend/``, and it carries the same consequence: the backend suite needs the whole
repository checked out, and the paths it reads are in ``ci.yml``'s backend filter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from functools import cache
from pathlib import Path
from typing import Any, Final

import hcl2
import pytest
import yaml

from app.domain.storage import EXPORT_RETENTION, EXPORTS_PREFIX
from app.settings import REPO_ROOT, Settings

# --------------------------------------------------------------------------------- paths

INFRA_DIR: Final[Path] = REPO_ROOT / "backend" / "infra"
COMPOSE_PATH: Final[Path] = REPO_ROOT / "docker-compose.yml"
DOCKERFILE_PATH: Final[Path] = REPO_ROOT / "backend" / "Dockerfile"
SCRIPTS_DIR: Final[Path] = REPO_ROOT / "scripts"
WORKFLOW_PATH: Final[Path] = REPO_ROOT / ".github" / "workflows" / "ci.yml"
GITIGNORE_PATH: Final[Path] = REPO_ROOT / ".gitignore"
DEPLOY_DOC_PATH: Final[Path] = REPO_ROOT / "docs" / "aws-deployment.md"
ENV_EXAMPLE_PATH: Final[Path] = REPO_ROOT / ".env.example"

#: The modules the root is expected to call. Named rather than discovered: this list is the
#: ticket's own decomposition (network / data / storage / registry / secrets / compute), and
#: a module quietly disappearing should fail rather than shrink the assertions.
EXPECTED_MODULES: Final[tuple[str, ...]] = (
    "compute",
    "data",
    "network",
    "registry",
    "secrets",
    "storage",
)

#: The two environments `variables.tf` validates `environment` against.
ENVIRONMENTS: Final[tuple[str, ...]] = ("local", "dev")

# ------------------------------------------------------------------------------- patterns

#: python-hcl2 annotates the tree with these. They carry the comments, which must not be
#: scanned: the prose in `backend/infra/` quotes every rule this module enforces.
META_KEYS: Final[frozenset[str]] = frozenset(
    {"__comments__", "__inline_comments__", "__is_block__", "__start_line__", "__end_line__"}
)

VAR_REFERENCE: Final[re.Pattern[str]] = re.compile(r"\bvar\.([a-z_][a-z0-9_]*)")
LOCAL_REFERENCE: Final[re.Pattern[str]] = re.compile(r"\blocal\.([a-z_][a-z0-9_]*)")
MODULE_REFERENCE: Final[re.Pattern[str]] = re.compile(
    r"\bmodule\.([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)"
)

#: An AWS account id is twelve digits. Nothing else in this configuration is.
ACCOUNT_ID: Final[re.Pattern[str]] = re.compile(r"\b\d{12}\b")

#: A region name. `data.aws_region` and `var.aws_region` are the two legal ways to say it;
#: the literal belongs in a `.tfvars`, which is the environment layout, and nowhere else.
REGION_LITERAL: Final[re.Pattern[str]] = re.compile(
    r"\b(?:us|eu|ap|sa|ca|me|af|il|mx)-(?:gov-)?[a-z]+-\d\b"
)

#: An ARN with the partition written out. `arn:${var.partition}:…` is the legal form.
LITERAL_PARTITION_ARN: Final[re.Pattern[str]] = re.compile(r"\barn:aws[a-z-]*:")

#: Long-lived and temporary AWS access key ids.
ACCESS_KEY_ID: Final[re.Pattern[str]] = re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")

#: PEM key material of any flavour.
PRIVATE_KEY_BLOCK: Final[re.Pattern[str]] = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")

#: An argument that means a credential, assigned a bare quoted string. `password = var.x` and
#: `manage_master_user_password = true` are fine; `password = "hunter2"` is not. The left-hand
#: names are exact rather than a wildcard, because `parameter_group_name` and `policy_arn`
#: are perfectly ordinary strings.
CREDENTIAL_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:password|master_password|secret|secret_key|access_key|secret_access_key"
    r"|auth_token|api_key|token)\b\s*=\s*\"",
    re.M,
)

#: Resources that would put a plaintext secret into the Terraform state file.
FORBIDDEN_RESOURCES: Final[tuple[str, ...]] = (
    "aws_secretsmanager_secret_version",
    "aws_iam_access_key",
    "aws_ssm_parameter",
)

#: Terraform subcommands that change or price real infrastructure. None may be wired into a
#: script or a workflow — see the module docstring and `docs/aws-deployment.md` §6.
MUTATING_COMMANDS: Final[tuple[str, ...]] = (
    "terraform apply",
    "terraform destroy",
    "terraform plan",
    "terraform import",
)

# --------------------------------------------------------------------------------- parsing


def _strip_meta(node: Any) -> Any:
    """The parsed tree with python-hcl2's annotations — comments included — removed."""
    if isinstance(node, dict):
        return {key: _strip_meta(value) for key, value in node.items() if key not in META_KEYS}
    if isinstance(node, list):
        return [_strip_meta(item) for item in node]
    return node


@cache
def document(path: Path) -> dict[str, Any]:
    """One HCL file, parsed and stripped of comments.

    Cached because several tests want the same twenty files and parsing is the expensive
    part; the results are only ever read.
    """
    return _strip_meta(hcl2.loads(path.read_text(encoding="utf-8")))


def text_of(value: object) -> str:
    """One parsed scalar as the text it stands for.

    python-hcl2 keeps the quotes on string literals and wraps every expression in ``${…}``,
    so ``'"celery"'`` and ``'${var.name}'`` both arrive as Python strings that still carry
    their syntax. This removes one layer of it.
    """
    if not isinstance(value, str):
        return str(value)
    body = value.strip()
    if len(body) >= 2 and body[0] == '"' and body[-1] == '"':
        return body[1:-1]
    if body.startswith("${") and body.endswith("}"):
        return body[2:-1]
    return body


def tf_files(directory: Path, *, recursive: bool = False) -> list[Path]:
    pattern = "**/*.tf" if recursive else "*.tf"
    return sorted(directory.glob(pattern))


def merged(paths: Iterable[Path]) -> dict[str, list[Any]]:
    """Several `.tf` files as one document.

    Terraform itself treats every `.tf` in a directory as one configuration, so a test that
    asked "is this variable used" per file would be asking the wrong question.
    """
    combined: dict[str, list[Any]] = {}
    for path in paths:
        for kind, entries in document(path).items():
            combined.setdefault(kind, []).extend(entries)
    return combined


@cache
def root() -> dict[str, list[Any]]:
    """The root module: the `.tf` files directly in `backend/infra/`."""
    return merged(tf_files(INFRA_DIR))


@cache
def module(name: str) -> dict[str, list[Any]]:
    return merged(tf_files(INFRA_DIR / "modules" / name))


@cache
def tfvars(environment: str) -> dict[str, Any]:
    return _strip_meta(
        hcl2.loads((INFRA_DIR / "envs" / f"{environment}.tfvars").read_text(encoding="utf-8"))
    )


def labelled(doc: dict[str, list[Any]], kind: str) -> dict[str, dict[str, Any]]:
    """`{label: body}` for a block type with one label — `variable`, `output`, `module`."""
    found: dict[str, dict[str, Any]] = {}
    for entry in doc.get(kind, []):
        for label, body in entry.items():
            found[text_of(label)] = body
    return found


def two_label_blocks(doc: dict[str, list[Any]], kind: str) -> list[tuple[str, str, dict[str, Any]]]:
    """`[(type, name, body)]` for a block type with two labels — `resource` and `data`."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for entry in doc.get(kind, []):
        for block_type, named in entry.items():
            for block_name, body in named.items():
                found.append((text_of(block_type), text_of(block_name), body))
    return found


def resources(doc: dict[str, list[Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    return two_label_blocks(doc, "resource")


def data_sources(doc: dict[str, list[Any]]) -> list[tuple[str, str, dict[str, Any]]]:
    return two_label_blocks(doc, "data")


def locals_of(doc: dict[str, list[Any]]) -> dict[str, Any]:
    """Every `locals` block in a configuration, flattened into one mapping."""
    found: dict[str, Any] = {}
    for block in doc.get("locals", []):
        found.update(block)
    return found


def keys_of(mapping: dict[str, Any]) -> set[str]:
    """The keys of a parsed HCL object, with any quoting removed.

    An object key may be written bare or quoted and the parser preserves the difference,
    which is never what a comparison wants.
    """
    return {text_of(key) for key in mapping}


def walk_strings(node: object) -> Iterator[str]:
    """Every string anywhere in a parsed document — keys and values, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk_strings(key)
            yield from walk_strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from walk_strings(item)
    elif isinstance(node, str):
        yield node


def references(doc: dict[str, list[Any]], pattern: re.Pattern[str]) -> set[str]:
    """Names matched by `pattern` anywhere except inside a `variable` block.

    A variable mentioned only by its own `validation` condition or its own description is
    not a variable anything uses, and counting it would make the "declared and used" rule
    unfalsifiable for exactly the variables most likely to be dead.
    """
    scanned = {kind: entries for kind, entries in doc.items() if kind != "variable"}
    return {match for text in walk_strings(scanned) for match in pattern.findall(text)}


# ------------------------------------------------------------------------- configurations

#: Every independent Terraform configuration in the tree. Terraform scopes variables, locals
#: and outputs per *directory*, so this is the unit almost every rule below is applied to.
CONFIGURATIONS: Final[list[str]] = ["<root>", *EXPECTED_MODULES]


def configuration(label: str) -> dict[str, list[Any]]:
    return root() if label == "<root>" else module(label)


#: Every HCL file in the tree, for the rules that are about source text rather than meaning.
ALL_HCL_FILES: Final[list[Path]] = sorted(tf_files(INFRA_DIR, recursive=True)) + sorted(
    (INFRA_DIR / "envs").glob("*.tfvars")
)


def hcl_file_id(path: Path) -> str:
    return str(path.relative_to(INFRA_DIR)).replace("\\", "/")


# ================================================================================== tests


class TestTheTreeIsTheShapeItClaims:
    """It exists, it is laid out as `README.md` says, and nothing is orphaned."""

    def test_the_infra_directory_exists(self) -> None:
        assert INFRA_DIR.is_dir(), f"{INFRA_DIR} is missing"

    @pytest.mark.parametrize(
        "filename",
        ["versions.tf", "providers.tf", "variables.tf", "locals.tf", "main.tf", "outputs.tf"],
    )
    def test_the_root_module_has_its_files(self, filename: str) -> None:
        assert (INFRA_DIR / filename).is_file(), f"backend/infra/{filename} is missing"

    def test_it_has_a_readme(self) -> None:
        assert (INFRA_DIR / "README.md").is_file()

    @pytest.mark.parametrize("name", EXPECTED_MODULES)
    def test_every_expected_module_exists(self, name: str) -> None:
        directory = INFRA_DIR / "modules" / name
        assert directory.is_dir(), f"module `{name}` is missing"
        assert tf_files(directory), f"module `{name}` contains no .tf files"

    @pytest.mark.parametrize("name", EXPECTED_MODULES)
    def test_every_module_declares_variables_and_outputs(self, name: str) -> None:
        """A module with no inputs is a copy-paste and a module with no outputs is a leaf."""
        assert (INFRA_DIR / "modules" / name / "variables.tf").is_file()
        assert (INFRA_DIR / "modules" / name / "outputs.tf").is_file()
        assert labelled(module(name), "variable"), f"module `{name}` declares no variables"
        assert labelled(module(name), "output"), f"module `{name}` declares no outputs"

    def test_the_root_calls_exactly_the_modules_on_disk(self) -> None:
        """Neither a call to a module that does not exist, nor a module nothing calls."""
        called = labelled(root(), "module")
        on_disk = {path.name for path in (INFRA_DIR / "modules").iterdir() if path.is_dir()}
        assert set(called) == set(EXPECTED_MODULES)
        assert on_disk == set(EXPECTED_MODULES)

    @pytest.mark.parametrize("name", EXPECTED_MODULES)
    def test_every_module_source_is_a_local_path_that_resolves(self, name: str) -> None:
        """A registry source would be somebody else's code on this repository's plan."""
        source = text_of(labelled(root(), "module")[name]["source"])
        assert source == f"./modules/{name}", f"module `{name}` has source `{source}`"
        assert (INFRA_DIR / source).is_dir()

    @pytest.mark.parametrize("environment", ENVIRONMENTS)
    def test_both_environments_have_a_tfvars_file(self, environment: str) -> None:
        assert (INFRA_DIR / "envs" / f"{environment}.tfvars").is_file()

    def test_the_environment_variable_is_constrained_to_those_two(self) -> None:
        """A third environment should mean adding its file, not typing a new string."""
        validation = labelled(root(), "variable")["environment"]["validation"][0]
        condition = text_of(validation["condition"])
        for environment in ENVIRONMENTS:
            assert f'"{environment}"' in condition
        assert "var.environment" in condition


class TestEverythingParses:
    """A real HCL2 parser, on every file, including the ones nothing else asserts about."""

    @pytest.mark.parametrize("path", ALL_HCL_FILES, ids=hcl_file_id)
    def test_the_file_parses(self, path: Path) -> None:
        assert document(path), f"{hcl_file_id(path)} parsed to nothing"


class TestTheProviderIsPinned:
    """A floating provider is somebody else's release on this repository's plan."""

    def test_terraform_and_the_aws_provider_are_both_constrained(self) -> None:
        terraform_block = root()["terraform"][0]
        assert "1.9" in text_of(terraform_block["required_version"])
        aws = terraform_block["required_providers"][0]["aws"]
        assert text_of(aws["source"]) == "hashicorp/aws"
        assert text_of(aws["version"]).startswith("~>"), (
            "the aws provider is not pinned to a major/minor line"
        )

    def test_the_state_backend_is_a_partial_configuration(self) -> None:
        """An empty `backend "s3" {}` is what makes `init -backend=false` and a public repo
        compatible: no real bucket name is written down, so nothing here can name one."""
        backend = root()["terraform"][0]["backend"][0]
        assert keys_of(backend) == {"s3"}
        configured = next(iter(backend.values()))
        assert not configured, f"the s3 backend is configured inline: {configured}"

    def test_the_lock_file_is_committed_and_covers_more_than_one_platform(self) -> None:
        """`terraform providers lock -platform=…` was run for linux, windows and macOS.

        The lock file does not *name* the platforms — it records one `h1:` package hash per
        locked platform and a shared set of `zh:` registry hashes — so the count is the only
        thing there is to assert. It is worth asserting: a lock generated by a plain `init`
        on one machine carries a single `h1:`, and the first `init` anywhere else silently
        re-locks it, which makes the pin decorative.
        """
        lock = INFRA_DIR / ".terraform.lock.hcl"
        assert lock.is_file(), "backend/infra/.terraform.lock.hcl is not committed"
        text = lock.read_text(encoding="utf-8")
        assert 'provider "registry.terraform.io/hashicorp/aws"' in text

        constraint = text_of(root()["terraform"][0]["required_providers"][0]["aws"]["version"])
        assert f'constraints = "{constraint}"' in text, (
            "the lock file was generated against a different version constraint"
        )
        package_hashes = text.count("h1:")
        assert package_hashes >= 3, (
            f"the lock file carries {package_hashes} package hashes; it was locked for one "
            f"platform and will be re-locked on any other machine"
        )


class TestTheVariablesAreHonest:
    """Declared and used, used and declared, described, and set by both environments."""

    @pytest.mark.parametrize("label", CONFIGURATIONS)
    def test_every_declared_variable_is_referenced(self, label: str) -> None:
        """A variable nothing reads is a knob that turns nothing."""
        declared = set(labelled(configuration(label), "variable"))
        used = references(configuration(label), VAR_REFERENCE)
        assert declared - used == set(), (
            f"`{label}` declares variables nothing uses: {sorted(declared - used)}"
        )

    @pytest.mark.parametrize("label", CONFIGURATIONS)
    def test_every_referenced_variable_is_declared(self, label: str) -> None:
        declared = set(labelled(configuration(label), "variable"))
        used = references(configuration(label), VAR_REFERENCE)
        assert used - declared == set(), (
            f"`{label}` reads variables nothing declares: {sorted(used - declared)}"
        )

    @pytest.mark.parametrize("label", CONFIGURATIONS)
    def test_every_declared_local_is_referenced(self, label: str) -> None:
        declared = keys_of(locals_of(configuration(label)))
        used = {
            match
            for text in walk_strings(configuration(label))
            for match in LOCAL_REFERENCE.findall(text)
        }
        assert declared - used == set(), (
            f"`{label}` declares locals nothing uses: {sorted(declared - used)}"
        )

    @pytest.mark.parametrize("label", CONFIGURATIONS)
    def test_every_variable_has_a_description_and_a_type(self, label: str) -> None:
        for name, body in labelled(configuration(label), "variable").items():
            assert body.get("description"), f"`{label}`.{name} has no description"
            assert body.get("type"), f"`{label}`.{name} has no type"

    def test_the_two_variables_that_must_never_be_guessed_have_no_default(self) -> None:
        """Where you are deploying, and which environment you are, are never a default."""
        declared = labelled(root(), "variable")
        for name in ("aws_region", "environment"):
            assert "default" not in declared[name], f"`{name}` has a default; it must not"

    @pytest.mark.parametrize("environment", ENVIRONMENTS)
    def test_every_required_variable_is_set_by_this_environment(self, environment: str) -> None:
        """The assertion that catches the real mistake: a new required variable added to the
        root and set in one tfvars file but not the other."""
        required = {
            name for name, body in labelled(root(), "variable").items() if "default" not in body
        }
        provided = set(tfvars(environment))
        assert required - provided == set(), (
            f"envs/{environment}.tfvars does not set: {sorted(required - provided)}"
        )

    @pytest.mark.parametrize("environment", ENVIRONMENTS)
    def test_this_environment_sets_nothing_undeclared(self, environment: str) -> None:
        """Terraform warns about an undeclared variable in a tfvars file and carries on."""
        declared = set(labelled(root(), "variable"))
        provided = set(tfvars(environment))
        assert provided - declared == set(), (
            f"envs/{environment}.tfvars sets undeclared variables: {sorted(provided - declared)}"
        )

    def test_the_environment_name_matches_the_file_it_is_in(self) -> None:
        for environment in ENVIRONMENTS:
            assert text_of(tfvars(environment)["environment"]) == environment


class TestTheModuleWiringIsComplete:
    """Every call passes every required input, and reads only outputs that exist."""

    @pytest.mark.parametrize("name", EXPECTED_MODULES)
    def test_the_call_passes_only_inputs_the_module_declares(self, name: str) -> None:
        passed = set(labelled(root(), "module")[name]) - {"source"}
        declared = set(labelled(module(name), "variable"))
        assert passed - declared == set(), (
            f"the `{name}` module call passes undeclared inputs: {sorted(passed - declared)}"
        )

    @pytest.mark.parametrize("name", EXPECTED_MODULES)
    def test_the_call_passes_every_required_input(self, name: str) -> None:
        passed = set(labelled(root(), "module")[name]) - {"source"}
        required = {
            variable
            for variable, body in labelled(module(name), "variable").items()
            if "default" not in body
        }
        assert required - passed == set(), (
            f"the `{name}` module call omits required inputs: {sorted(required - passed)}"
        )

    def test_every_module_output_the_root_reads_exists(self) -> None:
        """`module.data.postgres_hostname` is a plan-time error nobody sees until they plan."""
        for text in walk_strings(root()):
            for name, output in MODULE_REFERENCE.findall(text):
                assert name in EXPECTED_MODULES, f"reference to unknown module `{name}`"
                assert output in labelled(module(name), "output"), (
                    f"the root reads `module.{name}.{output}`, which module `{name}` "
                    f"does not output"
                )

    @pytest.mark.parametrize("label", CONFIGURATIONS)
    def test_every_output_has_a_description(self, label: str) -> None:
        for name, body in labelled(configuration(label), "output").items():
            assert body.get("description"), f"`{label}` output `{name}` has no description"


class TestNothingSecretOrAccountSpecificIsWrittenDown:
    """The rules that matter because this repository is public.

    Every one of these scans the **parsed** configuration, so the comments — which explain
    each rule, sometimes by quoting the shape it forbids — are not scanned. That exclusion
    was verified by mutation: pasting a real-shaped account id into a resource argument fails
    these, and writing the same digits into a comment does not.
    """

    def scanned_text(self) -> Iterator[tuple[str, str]]:
        """`(where, text)` for every string in every configuration and both tfvars files."""
        for label in CONFIGURATIONS:
            for text in walk_strings(configuration(label)):
                yield label, text
        for environment in ENVIRONMENTS:
            for text in walk_strings(tfvars(environment)):
                yield f"envs/{environment}.tfvars", text

    def test_no_account_id_appears_anywhere(self) -> None:
        for where, text in self.scanned_text():
            assert not ACCOUNT_ID.search(text), (
                f"a twelve-digit number — an AWS account id — appears in `{where}`: {text!r}"
            )

    def test_no_access_key_id_appears_anywhere(self) -> None:
        for where, text in self.scanned_text():
            assert not ACCESS_KEY_ID.search(text), f"an AWS access key id is in `{where}`"

    def test_no_private_key_material_appears_anywhere(self) -> None:
        for where, text in self.scanned_text():
            assert not PRIVATE_KEY_BLOCK.search(text), f"PEM key material is in `{where}`"

    @pytest.mark.parametrize("label", CONFIGURATIONS)
    def test_no_region_is_hardcoded_in_a_tf_file(self, label: str) -> None:
        """`var.aws_region` and `data.aws_region` are the two ways to say it.

        The literal belongs in a `.tfvars` — which *is* the per-environment layer — and
        nowhere else, or the module is only correct in one region.
        """
        for text in walk_strings(configuration(label)):
            assert not REGION_LITERAL.search(text), f"`{label}` hardcodes a region: {text!r}"

    @pytest.mark.parametrize("label", CONFIGURATIONS)
    def test_no_arn_writes_out_its_partition(self, label: str) -> None:
        """`arn:${var.partition}:…`, from `data.aws_partition`. `arn:aws:` is one partition."""
        for text in walk_strings(configuration(label)):
            assert not LITERAL_PARTITION_ARN.search(text), (
                f"`{label}` contains an ARN with a literal partition: {text!r}"
            )

    @pytest.mark.parametrize("resource_type", FORBIDDEN_RESOURCES)
    def test_no_resource_that_would_put_a_secret_in_state(self, resource_type: str) -> None:
        """`aws_secretsmanager_secret_version` is the one that would undo the whole design.

        Terraform stores every attribute of every managed resource in the state file, in
        plaintext, and `sensitive = true` marks an *output* rather than the state. So the
        secrets module creates named, empty boxes and a human fills them — see
        `modules/secrets/main.tf`. `aws_iam_access_key` is the same mistake wearing IAM's
        clothes, and it is why `modules/storage` creates a user and not a key.
        """
        for label in CONFIGURATIONS:
            present = [
                name for kind, name, _ in resources(configuration(label)) if kind == resource_type
            ]
            assert not present, (
                f"`{label}` declares `{resource_type}.{present[0]}`, which would write a "
                f"plaintext secret into Terraform state"
            )

    @pytest.mark.parametrize("path", ALL_HCL_FILES, ids=hcl_file_id)
    def test_no_credential_argument_is_assigned_a_literal(self, path: Path) -> None:
        """`password = "…"`, and its four siblings. Read off the source rather than the parse
        tree, because this one is about the *shape of the assignment* rather than a value."""
        found = CREDENTIAL_ASSIGNMENT.findall(path.read_text(encoding="utf-8"))
        assert not found, f"{path.name} assigns a credential a literal string: {found}"

    def test_the_deploy_document_carries_no_real_identifier_either(self) -> None:
        """It is the file most likely to acquire one, because it is written by pasting."""
        text = DEPLOY_DOC_PATH.read_text(encoding="utf-8")
        assert not ACCOUNT_ID.search(text)
        assert not ACCESS_KEY_ID.search(text)
        assert not PRIVATE_KEY_BLOCK.search(text)


class TestTheEnvironmentContract:
    """The highest-value module in this file.

    `app/settings.py` is the only place in the backend allowed to read the environment
    (`CLAUDE.md` §4), so the set of fields it declares *is* the contract with any deployment.
    `modules/compute/locals.tf` is the deployment's half. These assert the two are the same
    set, in both directions — which is the same shape as ANV-43's client/server password-rule
    drift test, and exists for the same reason.
    """

    def compute_locals(self) -> dict[str, Any]:
        return locals_of(module("compute"))

    def container_environment(self) -> dict[str, str]:
        return {
            name: text_of(value)
            for name, value in self.compute_locals()["container_environment"].items()
        }

    def container_secrets(self) -> dict[str, str]:
        return {
            name: text_of(value)
            for name, value in self.compute_locals()["container_secrets"].items()
        }

    def settings_variables(self) -> set[str]:
        return {field.upper() for field in Settings.model_fields}

    def test_the_task_definitions_set_every_setting_the_application_reads(self) -> None:
        """A field added to `Settings` with no home in the task definitions is a deployment
        that silently runs on the field's default — which for `postgres_host` is `db`, a
        compose service name that resolves to nothing in AWS."""
        provided = set(self.container_environment()) | set(self.container_secrets())
        missing = self.settings_variables() - provided
        assert missing == set(), (
            f"`Settings` reads variables the task definitions never set: {sorted(missing)}"
        )

    def test_the_task_definitions_set_nothing_the_application_does_not_read(self) -> None:
        """The other direction. A variable nothing reads is either a typo or a leftover, and
        `Settings` ignores unknown keys rather than complaining."""
        provided = set(self.container_environment()) | set(self.container_secrets())
        extra = provided - self.settings_variables()
        assert extra == set(), (
            f"the task definitions set variables `Settings` never reads: {sorted(extra)}"
        )

    def test_nothing_is_both_a_plain_value_and_a_secret(self) -> None:
        """ECS rejects the duplicate at task start, which is late to find out."""
        overlap = set(self.container_environment()) & set(self.container_secrets())
        assert overlap == set(), f"set as both plain and secret: {sorted(overlap)}"

    @pytest.mark.parametrize(
        "name",
        [
            "POSTGRES_PASSWORD",
            "JWT_SECRET_KEY",
            "ALPHAVANTAGE_API_KEY",
            "NEWSAPI_API_KEY",
            "S3_ACCESS_KEY_ID",
            "S3_SECRET_ACCESS_KEY",
        ],
    )
    def test_every_credential_is_a_secret_rather_than_a_plain_value(self, name: str) -> None:
        """These six are the `SecretStr` fields plus the S3 key id, which is only half a
        credential but is useless to separate from its other half."""
        assert name in self.container_secrets(), f"{name} is not resolved from Secrets Manager"
        assert name not in self.container_environment()

    def test_every_secret_resolves_from_secrets_manager_and_not_from_a_literal(self) -> None:
        for name, value in self.container_secrets().items():
            assert "var.secret_arns[" in value or "var.postgres_master_secret_arn" in value, (
                f"{name} does not resolve from a Secrets Manager ARN: {value!r}"
            )

    def test_every_secret_key_the_compute_module_names_is_declared_by_the_secrets_module(
        self,
    ) -> None:
        """`var.secret_arns["jwt-signing-key"]` is a map lookup, and a map lookup on a key
        that is not there fails at plan time — but only if somebody plans."""
        declared = keys_of(locals_of(module("secrets"))["definitions"])
        looked_up = {
            match
            for value in self.container_secrets().values()
            for match in re.findall(r"var\.secret_arns\[\"([^\"]+)\"\]", value)
        }
        assert looked_up, "the compute module looks up no secrets at all"
        assert looked_up <= declared, (
            f"compute looks up secrets the secrets module does not declare: "
            f"{sorted(looked_up - declared)}"
        )

    def test_the_postgres_password_comes_from_the_secret_rds_owns(self) -> None:
        """`manage_master_user_password` means Terraform never learns the value. The
        `:password::` suffix is how ECS addresses one field of that JSON secret."""
        instance = next(
            body for kind, _, body in resources(module("data")) if kind == "aws_db_instance"
        )
        assert instance["manage_master_user_password"] is True
        assert "password" not in instance, "the RDS instance is given a password directly"
        assert self.container_secrets()["POSTGRES_PASSWORD"].endswith(":password::")

    def test_the_execution_role_can_read_exactly_those_secrets_and_no_more(self) -> None:
        """A `Resource: "*"` here would mean the API can read every secret in the account.

        The grant is built from the same two inputs the task definitions are, so it cannot
        drift from them — this asserts it still is.
        """
        policy = next(
            body
            for kind, name, body in data_sources(module("compute"))
            if kind == "aws_iam_policy_document" and name == "execution_secrets"
        )
        statement = next(
            block
            for block in policy["statement"]
            if "secretsmanager:GetSecretValue" in str(block.get("actions"))
        )
        resources_expression = text_of(statement["resources"])
        assert "values(var.secret_arns)" in resources_expression
        assert "var.postgres_master_secret_arn" in resources_expression
        assert '"*"' not in resources_expression


class TestTheKnownDivergence:
    """One thing in the environment contract is not yet a working value, and says so.

    `S3_ENDPOINT_URL` is set to the empty string, which is what an operator *means* by "real
    S3" and is not what the application currently accepts: `Settings.s3_endpoint_url` defaults
    to the MinIO URL so it cannot be unset by omission, and `S3Client` hands whatever it holds
    to `aioboto3`, where `""` is not `None`. Fixing it is an application change and ANV-40
    changed no application code, so the marker is the contract — the same idiom as
    `TODO(ANV-mail)` in the recovery route.
    """

    def test_the_endpoint_is_empty_rather_than_pointing_at_minio(self) -> None:
        environment = locals_of(module("compute"))["container_environment"]
        assert text_of(environment["S3_ENDPOINT_URL"]) == ""

    def test_the_marker_is_on_the_assignment_it_is_about(self) -> None:
        """It disappears when the application change lands, and not before.

        Asserted against the **assignment**, and mutation testing took two attempts to get
        there. "the marker is somewhere in the file" was worthless — the module header
        explains the divergence at length, so deleting the marker beside the value left the
        string in the file. "a line containing both `S3_ENDPOINT_URL` and the marker" was
        *also* worthless, for a subtler version of the same reason: the header's first line
        names the variable while raising the TODO, so it satisfied both halves on its own.
        Only a pattern that matches an actual `S3_ENDPOINT_URL = "" #  TODO…` assignment is
        about the thing the marker annotates.
        """
        source = (INFRA_DIR / "modules" / "compute" / "locals.tf").read_text(encoding="utf-8")
        marked_assignment = re.compile(
            r'^\s*S3_ENDPOINT_URL\s*=\s*""\s*#[^\n]*TODO\(ANV-s3-aws\)', re.M
        )
        assert marked_assignment.search(source), (
            'the `S3_ENDPOINT_URL = ""` assignment no longer carries its TODO(ANV-s3-aws) '
            "marker; either the application change landed and the whole divergence should go, "
            "or a comment was tidied away from the one line that needed it"
        )

    def test_the_deploy_document_explains_it(self) -> None:
        text = DEPLOY_DOC_PATH.read_text(encoding="utf-8")
        assert "TODO(ANV-s3-aws)" in text
        assert "_require_configuration" in text


class TestTheEstateMirrorsTheComposeStack:
    """Read off `docker-compose.yml`, not invented.

    A generic AWS skeleton copied from a tutorial would pass `terraform validate` and tell
    nobody anything about this application. These are the assertions that make the two
    topologies one topology.
    """

    def compose(self) -> dict[str, Any]:
        return yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))

    def compose_command(self, service: str) -> list[str]:
        return [str(part) for part in self.compose()["services"][service]["command"]]

    def terraform_command(self, service: str) -> list[str]:
        commands = locals_of(module("compute"))["service_commands"]
        return [text_of(part) for part in commands[service]]

    def test_the_worker_runs_the_command_compose_runs(self) -> None:
        """Including `--pool prefork`, which `app/db/engine.py`'s fork rules are written for,
        and `--concurrency`, whose value comes from `local.tfvars` — so the two numbers are
        asserted equal as well as the two command lines."""
        expected = self.compose_command("worker")
        actual = [
            str(tfvars("local")["worker_concurrency"])
            if part == "tostring(var.worker_concurrency)"
            else part
            for part in self.terraform_command("worker")
        ]
        assert actual == expected

    def test_the_beat_command_including_its_schedule_path_matches_compose(self) -> None:
        """`--schedule` is not decoration: beat's shelve file otherwise lands in the working
        directory. Compose sends it to /tmp for the same reason this does."""
        assert self.terraform_command("beat") == self.compose_command("beat")

    def test_the_api_runs_the_images_own_command(self) -> None:
        """Compose overrides it only to add `--reload`, which is a development concern."""
        assert locals_of(module("compute"))["service_commands"]["api"] is None

    def test_there_is_one_ecr_repository_because_there_is_one_image(self) -> None:
        """`api`, `worker` and `beat` all declare `image: anvex/api:dev` in compose. Three
        repositories would be three chances for the worker to run a different commit."""
        compose = self.compose()["services"]
        images = {compose[name]["image"] for name in ("api", "worker", "beat")}
        assert len(images) == 1, f"compose no longer runs one image: {sorted(images)}"

        repositories = [
            name for kind, name, _ in resources(module("registry")) if kind == "aws_ecr_repository"
        ]
        assert repositories == ["api"], f"expected exactly one ECR repository, got {repositories}"

    def test_all_three_task_definitions_run_the_same_image(self) -> None:
        definitions = [
            body
            for kind, _, body in resources(module("compute"))
            if kind == "aws_ecs_task_definition"
        ]
        assert len(definitions) == 3
        for body in definitions:
            assert "image = local.image" in text_of(body["container_definitions"])

    @pytest.mark.parametrize(
        ("service", "compose_image", "tfvars_key"),
        [
            ("db", "postgres", "postgres_engine_version"),
            ("redis", "redis", "redis_engine_version"),
        ],
    )
    def test_the_engine_major_version_matches_the_compose_image(
        self, service: str, compose_image: str, tfvars_key: str
    ) -> None:
        """`postgres:16-alpine` and `16.4` are the same decision written twice."""
        tag = self.compose()["services"][service]["image"].split(":")[1]
        compose_major = tag.split("-")[0]
        assert self.compose()["services"][service]["image"].startswith(compose_image)
        for environment in ENVIRONMENTS:
            configured = text_of(tfvars(environment)[tfvars_key])
            assert configured.split(".")[0] == compose_major, (
                f"envs/{environment}.tfvars runs {tfvars_key}={configured} but compose runs "
                f"{compose_image}:{tag}"
            )

    def test_the_redis_parameter_group_matches_the_redis_major_version(self) -> None:
        for environment in ENVIRONMENTS:
            major = text_of(tfvars(environment)["redis_engine_version"]).split(".")[0]
            group = text_of(tfvars(environment)["redis_parameter_group_name"])
            assert group == f"default.redis{major}"

    def test_the_postgres_parameter_group_family_matches_the_engine_version(self) -> None:
        for environment in ENVIRONMENTS:
            major = text_of(tfvars(environment)["postgres_engine_version"]).split(".")[0]
            assert text_of(tfvars(environment)["postgres_parameter_group_family"]) == (
                f"postgres{major}"
            )

    def test_the_api_port_is_the_port_the_image_exposes(self) -> None:
        """The Dockerfile `EXPOSE`s it and its CMD binds it; the target group forwards to it."""
        exposed = re.search(r"^EXPOSE\s+(\d+)", DOCKERFILE_PATH.read_text(encoding="utf-8"), re.M)
        assert exposed is not None, "the backend Dockerfile no longer EXPOSEs a port"
        assert text_of(locals_of(root())["api_port"]) == exposed.group(1)


class TestTheOperationalRulesThatCameFromComposeAndTheApp:
    """The four behaviours that are decisions rather than defaults."""

    def service(self, name: str) -> dict[str, Any]:
        return next(
            body
            for kind, label, body in resources(module("compute"))
            if kind == "aws_ecs_service" and label == name
        )

    def test_beat_is_pinned_to_one_task_and_is_not_a_variable(self) -> None:
        """compose: "Running two `beat` processes would double every scheduled job, so there
        is exactly one and it is never scaled." There is no value other than 1."""
        assert self.service("beat")["desired_count"] == 1

    def test_beat_stops_its_old_task_before_starting_the_new_one(self) -> None:
        """The rolling default would briefly run two schedulers, which is the same bug the
        line above prevents, arriving through the deployment instead of through scaling."""
        beat = self.service("beat")
        assert beat["deployment_minimum_healthy_percent"] == 0
        assert beat["deployment_maximum_percent"] == 100

    def test_the_api_can_survive_its_own_deployment_in_dev(self) -> None:
        """One task means a gap. `local` accepts that and `dev` does not."""
        assert tfvars("dev")["api_desired_count"] >= 2

    def test_the_target_group_polls_readiness_and_the_container_polls_liveness(self) -> None:
        """`app/api/health.py` splits them and says why: readiness runs `SELECT 1`, so wiring
        it to the container check would turn a database blip into a restart loop, while
        wiring liveness to the load balancer would route traffic at a broken pool."""
        target_group = next(
            body for kind, _, body in resources(module("compute")) if kind == "aws_lb_target_group"
        )
        assert text_of(target_group["health_check"][0]["path"]) == "/health/ready"

        api = next(
            body
            for kind, label, body in resources(module("compute"))
            if kind == "aws_ecs_task_definition" and label == "api"
        )
        container = text_of(api["container_definitions"])
        assert "/health'" in container, "the api container check does not poll /health"
        assert "/health/ready" not in container, (
            "the api container health check polls readiness, which is a restart loop"
        )

    def test_no_task_gets_a_public_address(self) -> None:
        """Private subnets and a NAT gateway. `assign_public_ip = true` would put the
        application tier on the internet and is the cheap answer `docs/` argues against."""
        for name in ("api", "worker", "beat"):
            network = self.service(name)["network_configuration"][0]
            assert network["assign_public_ip"] is False, f"{name} tasks get a public address"

    def test_the_data_tier_has_no_default_route(self) -> None:
        """A database with nowhere to go is a cheaper guarantee than any egress rule."""
        routes = [body for kind, _, body in resources(module("network")) if kind == "aws_route"]
        data_routes = [
            body for body in routes if "aws_route_table.data" in text_of(body["route_table_id"])
        ]
        assert data_routes == [], "the data subnets have a default route"


class TestTheStorageRulesComeFromTheDomain:
    """`app/domain/storage.py` owns the key layout, and says so in its own docstring:
    the prefix "appears in every lifecycle policy and dashboard filter that mentions this
    prefix. Quietly rewriting a typo into a *different valid* prefix is how a policy stops
    matching." This is that policy."""

    def test_the_lifecycle_prefix_is_the_one_the_application_writes_under(self) -> None:
        configured = text_of(locals_of(root())["s3_export_prefix"])
        assert configured == f"{EXPORTS_PREFIX}/", (
            f"the lifecycle rule filters on `{configured}` but the app writes under "
            f"`{EXPORTS_PREFIX}/`"
        )

    @pytest.mark.parametrize("environment", ENVIRONMENTS)
    def test_the_expiration_matches_the_retention_the_domain_declares(
        self, environment: str
    ) -> None:
        assert tfvars(environment)["s3_export_expiration_days"] == EXPORT_RETENTION.days

    def test_the_bucket_refuses_public_access_four_different_ways(self) -> None:
        kinds = {kind for kind, _, _ in resources(module("storage"))}
        for required in (
            "aws_s3_bucket_public_access_block",
            "aws_s3_bucket_ownership_controls",
            "aws_s3_bucket_server_side_encryption_configuration",
            "aws_s3_bucket_policy",
        ):
            assert required in kinds, f"the exports bucket has no {required}"

    def test_versioning_and_the_noncurrent_expiration_travel_together(self) -> None:
        """Versioning without a noncurrent expiry is a bill that grows while the listing
        looks empty: a delete leaves the old version behind and it is still billed."""
        lifecycle = next(
            body
            for kind, _, body in resources(module("storage"))
            if kind == "aws_s3_bucket_lifecycle_configuration"
        )
        assert "noncurrent_version_expiration" in str(lifecycle)


class TestLocalDevelopmentDoesNotDependOnAnyOfIt:
    """The promise `CLAUDE.md` §1 makes about this directory, asserted.

    "AWS IaC (terraform). Local dev never depends on this." A developer must be able to work
    on this repository with no AWS account, no Terraform installed and no idea that
    `backend/infra/` exists — which is only true for as long as nothing on the local path
    reaches for it.
    """

    def local_development_sources(self) -> Iterator[tuple[str, str]]:
        yield "docker-compose.yml", COMPOSE_PATH.read_text(encoding="utf-8")
        yield ".env.example", ENV_EXAMPLE_PATH.read_text(encoding="utf-8")
        for script in sorted(SCRIPTS_DIR.glob("*.ps1")) + sorted(SCRIPTS_DIR.glob("*.sh")):
            yield f"scripts/{script.name}", script.read_text(encoding="utf-8")
        for script in sorted((REPO_ROOT / "backend" / "scripts").glob("*.py")):
            yield f"backend/scripts/{script.name}", script.read_text(encoding="utf-8")

    def test_no_local_development_path_mentions_terraform(self) -> None:
        for where, text in self.local_development_sources():
            lowered = text.lower()
            for word in ("terraform", "tfvars", "backend/infra"):
                assert word not in lowered, f"`{where}` mentions `{word}`"

    def test_the_compose_stack_still_runs_the_seven_services_the_estate_mirrors(self) -> None:
        """If compose loses a service, the AWS shape of it is orphaned rather than wrong, and
        the mirror tests above would keep passing over an estate nobody needs."""
        services = set(yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))["services"])
        assert {"db", "redis", "minio", "api", "worker", "beat"} <= services


class TestNothingApplies:
    """No script, no workflow, no hook. See `docs/aws-deployment.md` §6."""

    def automatable_sources(self) -> Iterator[tuple[str, str]]:
        for script in sorted(SCRIPTS_DIR.glob("*.ps1")) + sorted(SCRIPTS_DIR.glob("*.sh")):
            yield f"scripts/{script.name}", script.read_text(encoding="utf-8")
        for workflow in sorted(WORKFLOW_PATH.parent.glob("*.yml")):
            yield f".github/workflows/{workflow.name}", workflow.read_text(encoding="utf-8")

    @pytest.mark.parametrize("command", MUTATING_COMMANDS)
    def test_no_script_or_workflow_runs_it(self, command: str) -> None:
        """`terraform plan` is in this list too. A plan needs real credentials and reaches a
        real account; it creates nothing, but a workflow that can run one holds a credential
        that could do more (ANV-38)."""
        for where, text in self.automatable_sources():
            assert command not in text, f"`{where}` runs `{command}`"

    def test_the_ci_workflow_is_still_read_only(self) -> None:
        """ANV-38's conclusion, restated here because ANV-40 is the ticket most likely to
        break it: a deploy needs `id-token: write` on its own job in a separate file."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        assert workflow["permissions"] == {"contents": "read", "pull-requests": "read"}
        assert "id-token" not in WORKFLOW_PATH.read_text(encoding="utf-8")


class TestTheTfvarsAreActuallyCommittable:
    """A variable layout that git ignores is a variable layout nobody else has.

    The repository's `.gitignore` carries the usual blanket `*.tfvars`, because a tfvars file
    normally holds credentials. These ones hold none — every secret in this configuration
    lives in Secrets Manager and Terraform never learns its value — so there is an explicit
    exception, and it is worth asserting because deleting it would silently un-ship the files
    on somebody else's clone rather than here.
    """

    def test_the_gitignore_exception_is_present(self) -> None:
        lines = {line.strip() for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()}
        assert "!backend/infra/envs/*.tfvars" in lines, (
            "`.gitignore` no longer un-ignores backend/infra/envs/*.tfvars"
        )

    def test_terraform_state_is_still_ignored(self) -> None:
        lines = {line.strip() for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()}
        assert {".terraform/", "*.tfstate", "*.tfstate.*"} <= lines


class TestTheDeployDocument:
    """ANV-40's "done when": `docs/` explains the deploy path and what it would cost."""

    def text(self) -> str:
        return DEPLOY_DOC_PATH.read_text(encoding="utf-8")

    def test_it_exists(self) -> None:
        assert DEPLOY_DOC_PATH.is_file()

    def test_it_says_plainly_that_nothing_is_provisioned(self) -> None:
        """The single most important sentence in it. Both `README.md`s say it too."""
        assert "Nothing in this document has been provisioned" in self.text()
        assert "$0.00" in self.text()

    def test_it_documents_a_monthly_cost_for_both_environments(self) -> None:
        totals = re.findall(r"\*\*≈ \$(\d+)\*\*", self.text())
        assert len(totals) >= 2, "the document has no per-environment monthly totals"
        assert all(int(total) > 0 for total in totals)

    @pytest.mark.parametrize(
        "service",
        [
            "Application Load Balancer",
            "NAT Gateway",
            "RDS PostgreSQL",
            "ElastiCache Redis",
            "ECS Fargate",
            "Secrets Manager",
            "CloudWatch Logs",
            "ECR",
        ],
    )
    def test_the_cost_table_itemises_every_billed_service(self, service: str) -> None:
        assert service in self.text(), f"the cost estimate does not mention {service}"

    @pytest.mark.parametrize("key", ["postgres_instance_class", "redis_node_type"])
    def test_the_instance_sizes_it_quotes_are_the_ones_actually_configured(self, key: str) -> None:
        """The drift that would matter most: a cost estimate for a machine nobody deploys."""
        for environment in ENVIRONMENTS:
            size = text_of(tfvars(environment)[key])
            assert f"`{size}`" in self.text(), (
                f"envs/{environment}.tfvars uses `{size}`, which the cost estimate never names"
            )

    def test_it_describes_the_deploy_path_end_to_end(self) -> None:
        for step in (
            "terraform init -backend-config=",
            "put-secret-value",
            "docker push",
            "alembic upgrade head",
            "update-service",
            "/health/ready",
        ):
            assert step in self.text(), f"the deploy path never mentions `{step}`"

    def test_it_states_that_local_development_is_unaffected(self) -> None:
        assert "docker compose up" in self.text()
