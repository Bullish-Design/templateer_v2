"""Tests for output validators.

Verifies that the validate_output function correctly validates rendered
artifacts in TOML, JSON, YAML, and Python, and that custom validators
(parse and command kinds) work as expected.
"""

from templateer.validators import validate_output

# ---------------------------------------------------------------------------
# TOML validation
# ---------------------------------------------------------------------------


class TestTomlValidation:
    """Tests for TOML output validation."""

    def test_validate_valid_toml(self) -> None:
        """Valid TOML passes validation."""
        toml_text = '[project]\nname = "test"\n'
        errors = validate_output(toml_text, "toml")
        assert errors == []

    def test_validate_valid_toml_multiline(self) -> None:
        """Valid TOML with mixed content passes validation."""
        toml_text = """\
[project]
name = "my-project"
version = "0.1.0"
requires-python = ">=3.12"

[project.dependencies]
requests = ">=2.0"
click = ">=8.0"
"""
        errors = validate_output(toml_text, "toml")
        assert errors == []

    def test_validate_invalid_toml_syntax_error(self) -> None:
        """Invalid TOML syntax reports errors."""
        errors = validate_output("not valid toml {{{", "toml")
        assert len(errors) > 0
        assert any("toml" in err.lower() for err in errors)

    def test_validate_invalid_toml_bare_key(self) -> None:
        """Invalid TOML bare key reports errors."""
        # A string literal without quotes is invalid TOML
        errors = validate_output("[project]\nhello = unquoted", "toml")
        assert len(errors) > 0

    def test_validate_empty_str_is_valid_toml(self) -> None:
        """Empty string is valid TOML (no content)."""
        errors = validate_output("", "toml")
        assert errors == []


# ---------------------------------------------------------------------------
# JSON validation
# ---------------------------------------------------------------------------


class TestJsonValidation:
    """Tests for JSON output validation."""

    def test_validate_valid_json_object(self) -> None:
        """Valid JSON object passes validation."""
        errors = validate_output('{"key": "value"}', "json")
        assert errors == []

    def test_validate_valid_json_array(self) -> None:
        """Valid JSON array passes validation."""
        errors = validate_output("[1, 2, 3]", "json")
        assert errors == []

    def test_validate_valid_json_nested(self) -> None:
        """Valid nested JSON passes validation."""
        errors = validate_output(
            '{"project": {"name": "test", "version": "1.0"}}',
            "json",
        )
        assert errors == []

    def test_validate_invalid_json(self) -> None:
        """Invalid JSON reports errors."""
        errors = validate_output("{key: value}", "json")
        assert len(errors) > 0

    def test_validate_invalid_json_trailing_comma(self) -> None:
        """JSON with trailing comma reports errors."""
        errors = validate_output('{"key": "value",}', "json")
        assert len(errors) > 0

    def test_validate_empty_str_is_invalid_json(self) -> None:
        """Empty string is not valid JSON."""
        errors = validate_output("", "json")
        assert len(errors) > 0


# ---------------------------------------------------------------------------
# YAML validation
# ---------------------------------------------------------------------------


class TestYamlValidation:
    """Tests for YAML output validation."""

    def test_validate_valid_yaml(self) -> None:
        """Valid YAML passes validation."""
        errors = validate_output("key: value\n", "yaml")
        assert errors == []

    def test_validate_valid_yaml_mapping(self) -> None:
        """Valid YAML mapping passes validation."""
        yaml_text = """\
project:
  name: my-project
  version: "0.1.0"
  dependencies:
    - requests
    - click
"""
        errors = validate_output(yaml_text, "yaml")
        assert errors == []

    def test_validate_valid_yaml_list(self) -> None:
        """Valid YAML list passes validation."""
        errors = validate_output("- one\n- two\n- three\n", "yaml")
        assert errors == []

    def test_validate_yaml_parse_is_lenient(self) -> None:
        """YAML is lenient: most strings parse without error."""
        # YAML accepts bare values
        errors = validate_output("hello", "yaml")
        assert errors == []


# ---------------------------------------------------------------------------
# Python validation
# ---------------------------------------------------------------------------


class TestPythonValidation:
    """Tests for Python output validation."""

    def test_validate_valid_python_import(self) -> None:
        """Valid Python import passes validation."""
        errors = validate_output("import os\nimport sys\n", "python")
        assert errors == []

    def test_validate_valid_python_function(self) -> None:
        """Valid Python function definition passes validation."""
        errors = validate_output("def foo():\n    pass\n", "python")
        assert errors == []

    def test_validate_valid_python_class(self) -> None:
        """Valid Python class definition passes validation."""
        errors = validate_output(
            "class MyModel:\n    def __init__(self):\n        pass\n",
            "python",
        )
        assert errors == []

    def test_validate_invalid_python_syntax(self) -> None:
        """Invalid Python syntax reports errors."""
        errors = validate_output("def 123foo():\n    pass\n", "python")
        assert len(errors) > 0

    def test_validate_invalid_python_indentation(self) -> None:
        """Invalid Python indentation reports errors."""
        errors = validate_output("def foo():\npass\n", "python")
        assert len(errors) > 0

    def test_validate_empty_str_is_valid_python(self) -> None:
        """Empty string is valid Python (empty module)."""
        errors = validate_output("", "python")
        assert errors == []


# ---------------------------------------------------------------------------
# Unknown language
# ---------------------------------------------------------------------------


class TestUnknownLanguage:
    """Tests for unknown/no matching language validator."""

    def test_unknown_language_no_error(self) -> None:
        """Unknown languages don't raise errors (no validator available)."""
        errors = validate_output("anything", "dockerfile")
        assert errors == []

    def test_unknown_language_returns_empty_list(self) -> None:
        """Unknown language returns an empty error list."""
        errors = validate_output("arbitrary text", "markdown")
        assert errors == []


# ---------------------------------------------------------------------------
# Custom validators (parse kind)
# ---------------------------------------------------------------------------


class TestCustomParseValidators:
    """Tests for custom parse validators from template metadata."""

    def test_custom_parse_validator_toml(self) -> None:
        """Custom TOML parse validator works."""
        validators = [{"kind": "parse", "language": "toml"}]
        errors = validate_output(
            '[project]\nname = "test"\n',
            "toml",
            validators,
        )
        assert errors == []

    def test_custom_parse_validator_fails(self) -> None:
        """Custom parse validator catches invalid syntax."""
        validators = [{"kind": "parse", "language": "toml"}]
        errors = validate_output(
            "invalid toml {{{",
            "toml",
            validators,
        )
        assert len(errors) > 0

    def test_custom_parse_validator_optional(self) -> None:
        """Optional parse validator does not add extra errors.

        The built-in language validator runs independently and may still
        report errors; this test verifies that the *optional* custom
        validator does not add *extra* errors.
        """
        # Use valid YAML (built-in) with an optional custom TOML validator
        # that will fail because the content is not valid TOML.
        validators = [{"kind": "parse", "language": "toml", "optional": True}]
        errors = validate_output(
            "key: value\n",  # valid YAML but invalid TOML
            "yaml",
            validators,
        )
        # The optional TOML custom validator failed but should not add
        # errors because it is marked optional.
        no_toml_errors = [e for e in errors if "toml" in e.lower()]
        assert len(no_toml_errors) == 0

    def test_custom_parse_validator_unknown_language(self) -> None:
        """Custom parse validator for unknown language is silently skipped."""
        validators = [{"kind": "parse", "language": "unknown_lang"}]
        errors = validate_output("some text", "unknown_lang", validators)
        assert errors == []


# ---------------------------------------------------------------------------
# Built-in validator + custom validators together
# ---------------------------------------------------------------------------


class TestCombinedValidators:
    """Tests for built-in and custom validators working together."""

    def test_built_in_and_custom_validators_both_pass(self) -> None:
        """Both built-in and custom validators pass with valid content."""
        validators = [{"kind": "parse", "language": "toml"}]
        errors = validate_output(
            '[project]\nname = "test"\n',
            "toml",
            validators,
        )
        assert errors == []

    def test_custom_validator_for_different_language(self) -> None:
        """Custom validator can target a different language than built-in."""
        # Built-in validates as yaml; custom validates as json on the same text
        validators = [{"kind": "parse", "language": "json"}]
        errors = validate_output(
            "key: value\n",  # valid YAML, invalid JSON
            "yaml",
            validators,
        )
        assert len(errors) > 0
        assert any("json" in err.lower() for err in errors)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Tests for edge cases in output validation."""

    def test_none_artifact(self) -> None:
        """None artifact produces a validation error message."""
        errors = validate_output(None, "toml")  # type: ignore[arg-type]
        assert len(errors) > 0

    def test_whitespace_only_toml(self) -> None:
        """Whitespace-only string is valid TOML."""
        errors = validate_output("   \n\n  ", "toml")
        assert errors == []

    def test_long_json(self) -> None:
        """Large but valid JSON passes validation."""
        errors = validate_output('{"items": [1,2,3,4,5]}', "json")
        assert errors == []

    def test_yaml_with_tabs(self) -> None:
        """YAML with tabs might produce warnings but still parse."""
        errors = validate_output("key:\n\tvalue\n", "yaml")
        # Tabs in YAML cause errors but YAML parser may accept them
        # We just check it doesn't crash
        assert isinstance(errors, list)
