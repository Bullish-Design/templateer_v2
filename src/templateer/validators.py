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
from typing import Any

import yaml


class OutputValidationError(Exception):
    """Raised when output validation fails."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("; ".join(errors))


def validate_output(
    artifact: str,
    language: str,
    validators: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Validate a rendered artifact.

    Runs the built-in parser validator for the target language, plus any
    custom validators specified in the template metadata.

    Args:
        artifact: The rendered artifact text.
        language: Target language (toml, json, yaml, python).
        validators: Optional additional validators from template metadata.
            Each validator dict must have a ``kind`` field ("parse" or "command").
            - Parse validators: requires ``language`` field.
            - Command validators: requires ``command`` field (list of str).
            - Optional flag: ``optional`` (bool) — if True, failure is reported
              but not included in the error list.

    Returns:
        List of error messages. Empty list means all validators passed.
    """
    errors: list[str] = []

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
            kind = validator.get("kind")
            optional = validator.get("optional", False)

            if kind == "parse":
                lang = validator.get("language")
                if lang and lang in parser_validators:
                    try:
                        parser_validators[lang](artifact)
                    except Exception as e:
                        msg = f"Custom parse ({lang}) failed: {e}"
                        if not optional:
                            errors.append(msg)

            elif kind == "command":
                cmd = validator.get("command", [])
                if cmd:
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
                            if not optional:
                                errors.append(msg)
                    except FileNotFoundError:
                        msg = f"Command '{' '.join(cmd)}' not found"
                        if not optional:
                            errors.append(msg)
                    except subprocess.TimeoutExpired:
                        msg = f"Command '{' '.join(cmd)}' timed out"
                        if not optional:
                            errors.append(msg)
                    except Exception as e:
                        msg = f"Command '{' '.join(cmd)}' error: {e}"
                        if not optional:
                            errors.append(msg)

    return errors


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
