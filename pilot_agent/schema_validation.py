"""Fail-closed schema validation -- every write to storage must pass
through validate_or_raise() first, exactly like the AIanger Platform's
own convention. This is deliberately copied as a pattern, not as code:
this Pilot does not import anything from the AIanger repository."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

try:
    import jsonschema
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "jsonschema is required (pip install jsonschema) -- see requirements.txt"
    ) from exc

_SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schema"
_CACHE: Dict[str, dict] = {}


class SchemaValidationError(ValueError):
    pass


def _load_schema(schema_filename: str) -> dict:
    if schema_filename not in _CACHE:
        path = _SCHEMA_DIR / schema_filename
        with open(path, "r", encoding="utf-8") as f:
            _CACHE[schema_filename] = json.load(f)
    return _CACHE[schema_filename]


def validate_or_raise(instance: Dict[str, Any], schema_filename: str) -> None:
    schema = _load_schema(schema_filename)
    try:
        jsonschema.validate(instance=instance, schema=schema)
    except jsonschema.ValidationError as exc:
        raise SchemaValidationError(f"{schema_filename}: {exc.message}") from exc
