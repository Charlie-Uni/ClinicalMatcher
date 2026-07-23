import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_RESOURCE = "schemas/clinicalmatcher-1.0.0.schema.json"


class DocumentValidationError(ValueError):
    """Raised when a document violates the versioned public schema."""


def load_schema() -> Dict[str, Any]:
    resource = files("clinical_matcher").joinpath(SCHEMA_RESOURCE)
    return json.loads(resource.read_text(encoding="utf-8"))


def validate_document(document: Dict[str, Any]) -> None:
    schema = load_schema()
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda item: list(item.path))
    if errors:
        messages = []
        for error in errors:
            path = ".".join(str(part) for part in error.absolute_path) or "<root>"
            messages.append(f"{path}: {error.message}")
        raise DocumentValidationError("\n".join(messages))


def validate_path(path: Path) -> Dict[str, Any]:
    document: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    validate_document(document)
    return document
