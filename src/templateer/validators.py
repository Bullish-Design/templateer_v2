"""Output validators for rendered artifacts.

Validates that rendered templates produce syntactically valid output
in the target language (TOML, JSON, YAML, Python, etc.).

This is separate from Pydantic model validation. Pydantic validates the
intermediate structured model; output validators check the final artifact
produced by the renderer.
"""

import ast
import json
import subprocess
import tomllib
from collections.abc import Iterator
from typing import Any

import yaml

from templateer.models import (
    CommandValidator,
    MarkdownValidator,
    OutputSpec,
    OutputValidator,
    ParseValidator,
)

ValidatorSpec = ParseValidator | CommandValidator | MarkdownValidator


def effective_validators(
    output: OutputSpec, declared: list[OutputValidator]
) -> list[OutputValidator]:
    """The validators that actually run for an output.

    A region template's payload check is the safety property the kind exists
    to declare: it is not an authoring choice, so it is prepended no matter
    what the template declares.  Only a *non-optional* declared markdown
    validator suppresses the prepend.  ``optional: true`` must not downgrade
    the check to a warning.
    """
    if output.kind != "region":
        return declared
    if any(isinstance(v, MarkdownValidator) and not v.optional for v in declared):
        return declared
    return [MarkdownValidator(kind="markdown"), *declared]


def validate_region_payload(artifact: str) -> list[str]:
    """Validate *artifact* as the payload of a fenced YAML region block.

    Returns a list of errors; empty means the payload is clean.

    The consumer swaps the fence *body* (the block's CodeText span) and owns
    the fences, so the artifact is bare YAML by contract.  A fenced artifact
    double-fences the hosting block and corrupts the page, so a leading
    fence line is an error.  An empty payload is a generation bug, so ``{}``
    and ``[]`` are errors too.
    """
    fence_errors = _check_no_fence(artifact)
    if fence_errors:
        return fence_errors

    body = artifact

    # One YAML document ------------------------------------------------
    try:
        parsed = yaml.safe_load(body)
    except yaml.YAMLError as e:
        return [f"region payload is not valid YAML: {e}"]
    if not isinstance(parsed, (dict, list)):
        kind = type(parsed).__name__
        return [
            f"region payload must be a YAML mapping or list (a data block), "
            f"got {kind}"
        ]
    if not parsed:
        kind = "mapping" if isinstance(parsed, dict) else "list"
        return [
            f"region payload is an empty {kind}: a generated empty payload "
            f"is a bug"
        ]

    # Round-trip stability ---------------------------------------------
    try:
        re_parsed = yaml.safe_load(yaml.safe_dump(parsed))
    except yaml.YAMLError as e:
        return [f"region payload does not round-trip through YAML: {e}"]
    if re_parsed != parsed:
        return ["region payload does not round-trip: parse/dump changes the document"]

    # Duplicate keys ----------------------------------------------------
    return _find_duplicate_keys(body)


def _check_no_fence(artifact: str) -> list[str]:
    """Report a leading fence line.

    The 05 guide D1 says the page owns the fences and the artifact is the
    bare body.  A fenced artifact double-fences the block, so it is an error.
    """
    lines = artifact.splitlines()
    if not lines:
        return ["region payload is empty"]

    first = lines[0].strip()
    if first.startswith("```"):
        opener = "```"
    elif first == "---" or first.startswith("--- "):
        opener = "---"
    else:
        return []  # bare body — the only accepted shape

    return [
        f"region payload must not be fenced: line 1 is a '{opener}' fence "
        f"line, but the page owns the fences and the payload is bare YAML"
    ]


def _find_duplicate_keys(text: str) -> list[str]:
    """Report duplicate mapping keys.

    PyYAML's ``safe_load`` silently keeps the last duplicate (verified); a
    swapped payload must not corrupt meaning silently, so duplicates are
    errors.  ``yaml.compose`` builds the node tree without constructing
    values, so this never executes anything.
    """
    try:
        node = yaml.compose(text)
    except yaml.YAMLError:
        return []  # already reported by the parse check
    if node is None:
        return []

    found: list[str] = []

    def walk(n: yaml.Node) -> None:
        if isinstance(n, yaml.MappingNode):
            seen: set[str] = set()
            for key_node, _ in n.value:
                if isinstance(key_node, yaml.ScalarNode):
                    key = key_node.value
                    if key in seen:
                        found.append(f"duplicate key {key!r} in region payload")
                    seen.add(key)
            for _, value_node in n.value:
                walk(value_node)
        elif isinstance(n, yaml.SequenceNode):
            for item in n.value:
                walk(item)

    walk(node)
    return found


def validate_output(
    artifact: str,
    language: str,
    validators: list[ValidatorSpec] | None = None,
) -> tuple[list[str], list[str]]:
    """
    Validate a rendered artifact.

    Runs the built-in parser validator for the target language, plus any
    custom validators specified in the template metadata.

    Args:
        artifact: The rendered artifact text.
        language: Target language (toml, json, yaml, python).
        validators: Optional additional validators from template metadata.

    Returns:
        ``(errors, warnings)``.  Errors are fatal; warnings come from
        validators declared ``optional: true``.
    """
    errors: list[str] = []
    warnings: list[str] = []

    # Built-in parser validation based on target language
    parser_validators = {
        "toml": _validate_toml,
        "json": _validate_json,
        "yaml": _validate_yaml,
        "python": _validate_python,
    }

    if language in parser_validators:
        try:
            parser_validators[language](artifact)
        except Exception as e:
            errors.append(f"{language} parse failed: {e}")

    # Custom validators from template metadata
    if validators:
        for validator in validators:
            bucket = warnings if validator.optional else errors

            if isinstance(validator, ParseValidator):
                lang = validator.language
                if lang in parser_validators:
                    try:
                        parser_validators[lang](artifact)
                    except Exception as e:
                        bucket.append(f"Custom parse ({lang}) failed: {e}")

            elif isinstance(validator, CommandValidator):
                cmd = validator.command
                try:
                    result = subprocess.run(
                        cmd,
                        input=artifact,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    if result.returncode != 0:
                        bucket.append(
                            _command_failure(cmd, result.stdout, result.stderr)
                        )
                except FileNotFoundError:
                    bucket.append(f"Command '{' '.join(cmd)}' not found")
                except subprocess.TimeoutExpired:
                    bucket.append(f"Command '{' '.join(cmd)}' timed out")
                except Exception as e:
                    bucket.append(f"Command '{' '.join(cmd)}' error: {e}")

            elif isinstance(validator, MarkdownValidator):
                bucket += validate_region_payload(artifact)

    return errors, warnings


def _command_failure(cmd: list[str], stdout: str, stderr: str) -> str:
    """Build the error text for a command that exited non-zero.

    Ruff and most linters write their diagnostics to stdout, so a report that
    reads stderr alone loses the detail.  Report both streams, labelled.
    """
    msg = f"Command '{' '.join(cmd)}' failed"
    parts = [
        f"{name}: {text.strip()}"
        for name, text in (("stdout", stdout), ("stderr", stderr))
        if text and text.strip()
    ]
    if parts:
        msg += ": " + "; ".join(parts)
    return msg


STRUCTURED_LANGUAGES = ("toml", "json", "yaml", "python")

# Returned by ``_relex`` when the target language cannot read the string.
_UNREADABLE = object()


def check_round_trip(
    artifact: str, language: str, model_dump: dict[str, Any]
) -> list[str]:
    """Report model strings that the artifact carries as another type.

    The escaper protects the artifact's *lexical* structure.  This check
    protects its *semantic* structure.  A field declared ``str`` with the
    value ``"true"`` reaches an unquoted YAML site as the boolean ``True``,
    and every other layer reports success.

    The rule (CONTRACT.md §4).  For each string leaf ``v`` of *model_dump*,
    compute what the target language reads if ``v`` lands unquoted.  Report a
    finding when three things hold together:

    1. the re-lexed value is not a string,
    2. the re-lexed value appears among the artifact's non-string leaves, and
    3. ``v`` itself appears nowhere among the artifact's string leaves.

    Condition 3 is the false-positive guard: a value that also reaches the
    artifact as a string is quoted somewhere, so nothing was lost.

    Args:
        artifact: The rendered artifact text.
        language: Target language.  Unstructured languages return no finding.
        model_dump: The validated model, as plain data.

    Returns:
        A list of findings.  Empty means the artifact's data agrees with the
        model's data.
    """
    if language not in STRUCTURED_LANGUAGES:
        return []

    leaves = _artifact_leaves(artifact, language)
    if leaves is None:
        return []  # unparseable: the parse validator reports that
    strings, others = leaves

    findings: list[str] = []
    for path, value in _model_strings(model_dump):
        relexed = _relex(value, language)
        if relexed is _UNREADABLE or isinstance(relexed, str):
            continue
        if not any(_same(leaf, relexed) for leaf in others):
            continue
        if value in strings:
            continue
        findings.append(
            f"{path}: the schema declares str and the model holds "
            f"{value!r}, but the artifact carries it as "
            f"{_type_name(relexed)} ({relexed!r}) — quote the interpolation"
        )
    return findings


def _artifact_leaves(
    artifact: str, language: str
) -> tuple[set[str], list[Any]] | None:
    """Return ``(string leaves, non-string leaves)`` of a parsed artifact.

    Returns ``None`` when the artifact does not parse.
    """
    strings: set[str] = set()
    others: list[Any] = []
    try:
        if language == "python":
            for node in ast.walk(ast.parse(artifact)):
                if isinstance(node, ast.Constant):
                    _record(node.value, strings, others)
            return strings, others
        if language == "toml":
            data: Any = tomllib.loads(artifact)
        elif language == "json":
            data = json.loads(artifact)
        else:
            data = yaml.safe_load(artifact)
    except Exception:
        return None
    _walk(data, strings, others)
    return strings, others


def _walk(value: Any, strings: set[str], others: list[Any]) -> None:
    """Collect every leaf of parsed data.  Mapping keys are leaves too."""
    if isinstance(value, dict):
        for key, item in value.items():
            _record(key, strings, others)
            _walk(item, strings, others)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _walk(item, strings, others)
    else:
        _record(value, strings, others)


def _record(value: Any, strings: set[str], others: list[Any]) -> None:
    if isinstance(value, str):
        strings.add(value)
    else:
        others.append(value)


def _model_strings(value: Any, path: str = "") -> Iterator[tuple[str, str]]:
    """Yield ``(dotted path, string)`` for every string leaf of the model."""
    if isinstance(value, str):
        yield path or "<root>", value
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else str(key)
            yield from _model_strings(item, child)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            yield from _model_strings(item, f"{path}[{index}]")


def _relex(value: str, language: str) -> Any:
    """Read *value* as the target language reads an unquoted token."""
    try:
        if language == "yaml":
            return yaml.safe_load(value)
        if language == "json":
            return json.loads(value)
        if language == "toml":
            return tomllib.loads(f"x = {value}")["x"]
        return ast.literal_eval(value)
    except Exception:
        return _UNREADABLE


def _same(left: Any, right: Any) -> bool:
    """Compare with the type, so ``True`` never equals ``1``."""
    return type(left) is type(right) and bool(left == right)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    return type(value).__name__


def _validate_toml(text: str) -> None:
    """Validate TOML by parsing it with ``tomllib``."""
    tomllib.loads(text)


def _validate_json(text: str) -> None:
    """Validate JSON by parsing it."""
    json.loads(text)


def _validate_yaml(text: str) -> None:
    """Validate YAML by parsing it."""
    yaml.safe_load(text)


def _validate_python(text: str) -> None:
    """Validate Python by parsing the AST."""
    ast.parse(text)
