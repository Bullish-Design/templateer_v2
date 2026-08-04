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
    what the template declares.  An explicit markdown validator is not
    duplicated.
    """
    if output.kind != "region":
        return declared
    if any(isinstance(v, MarkdownValidator) for v in declared):
        return declared
    return [MarkdownValidator(kind="markdown"), *declared]


def validate_region_payload(artifact: str) -> list[str]:
    """Validate *artifact* as the payload of a fenced YAML region block.

    Returns a list of errors; empty means the payload is clean.

    The consumer swaps the fence *body* (the block's CodeText span) and owns
    the fences, so the artifact is bare YAML by contract.  A fenced artifact
    is tolerated: a leading fence line must be matched by a trailing fence
    line, and the body must not contain a fence line.  A payload that begins
    with ``---`` is therefore treated as a fence opener, never as a YAML
    document-start marker.
    """
    body, fence_errors = _extract_fenced_body(artifact)
    if fence_errors:
        return fence_errors

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

    # Round-trip stability ---------------------------------------------
    try:
        re_parsed = yaml.safe_load(yaml.safe_dump(parsed))
    except yaml.YAMLError as e:
        return [f"region payload does not round-trip through YAML: {e}"]
    if re_parsed != parsed:
        return ["region payload does not round-trip: parse/dump changes the document"]

    # Duplicate keys ----------------------------------------------------
    return _find_duplicate_keys(body)


def _extract_fenced_body(artifact: str) -> tuple[str, list[str]]:
    """Return ``(body, errors)`` for a possibly-fenced artifact."""
    lines = artifact.splitlines()
    if not lines:
        return "", ["region payload is empty"]

    first = lines[0].strip()
    opener: str | None = None
    if first.startswith("```"):
        opener = "```"  # trailing language tag allowed: ```yaml
    elif first == "---" or first.startswith("--- "):
        opener = "---"

    if opener is None:
        return artifact, []  # bare body — the normal case

    if len(lines) < 2:
        return "", [f"unclosed '{opener}' fence: opener without a closer"]
    if lines[-1].strip() != opener:
        return "", [
            f"unclosed '{opener}' fence: expected closing '{opener}', "
            f"got {lines[-1].strip()!r}"
        ]

    body_lines = lines[1:-1]
    errors: list[str] = []
    for i, line in enumerate(body_lines, start=2):
        if line.strip().startswith(opener):
            errors.append(f"stray fence '{opener}' at line {i}: the body must not "
                          f"contain a fence line")
    return "\n".join(body_lines), errors


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
                        stderr = result.stderr.strip()
                        msg = f"Command '{' '.join(cmd)}' failed"
                        if stderr:
                            msg += f": {stderr}"
                        bucket.append(msg)
                except FileNotFoundError:
                    bucket.append(f"Command '{' '.join(cmd)}' not found")
                except subprocess.TimeoutExpired:
                    bucket.append(f"Command '{' '.join(cmd)}' timed out")
                except Exception as e:
                    bucket.append(f"Command '{' '.join(cmd)}' error: {e}")

            elif isinstance(validator, MarkdownValidator):
                bucket = warnings if validator.optional else errors
                bucket += validate_region_payload(artifact)

    return errors, warnings


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
