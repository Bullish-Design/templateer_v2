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

from templateer.models import CommandValidator, ParseValidator

ValidatorSpec = ParseValidator | CommandValidator


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
