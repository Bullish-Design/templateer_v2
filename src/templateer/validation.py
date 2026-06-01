"""Model validation utilities.

Validates raw data against Pydantic schemas. This is the bridge between
LLM-generated structured output and the deterministic rendering pipeline.
"""

from typing import Any

from pydantic import BaseModel, ValidationError


def validate_model_instance(
    schema_class: type[BaseModel],
    data: dict[str, Any],
) -> tuple[BaseModel | None, list[str]]:
    """
    Validate data against a Pydantic schema.

    This is the primary validation function used after the LLM produces
    structured output. It validates the raw data against the template's
    schema class and returns a clean result tuple.

    Args:
        schema_class: The Pydantic model class to validate against.
        data: Raw data dict to validate.

    Returns:
        Tuple of (validated_model, validation_errors).
        If validation succeeds, validated_model is the instance and errors is empty.
        If validation fails, validated_model is None and errors contains
        descriptive messages for each validation issue.
    """
    try:
        instance = schema_class.model_validate(data)
        return instance, []
    except ValidationError as e:
        errors = [str(err) for err in e.errors()]
        return None, errors
