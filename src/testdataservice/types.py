"""Value typing for the Test Data Service.

A stored entry is a key/value pair whose value is one of a small set of
JSON-friendly types: string, boolean, number, or array.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ValueType(str, Enum):
    STRING = "string"
    BOOLEAN = "boolean"
    NUMBER = "number"
    ARRAY = "array"


def infer_type(value: Any) -> ValueType:
    """Infer the :class:`ValueType` of a raw Python value.

    ``bool`` is checked before ``int``/``float`` because ``bool`` is a subclass
    of ``int`` in Python.
    """
    if isinstance(value, bool):
        return ValueType.BOOLEAN
    if isinstance(value, (int, float)):
        return ValueType.NUMBER
    if isinstance(value, str):
        return ValueType.STRING
    if isinstance(value, list):
        return ValueType.ARRAY
    raise ValueError(
        f"Unsupported value type {type(value).__name__!r}; "
        "expected string, boolean, number, or array."
    )


def validate(value: Any, value_type: ValueType) -> Any:
    """Validate (and lightly coerce) ``value`` against ``value_type``.

    Returns the validated value. Raises ``ValueError`` on mismatch.
    """
    if value_type == ValueType.STRING:
        if not isinstance(value, str):
            raise ValueError("Expected a string value.")
    elif value_type == ValueType.BOOLEAN:
        if not isinstance(value, bool):
            raise ValueError("Expected a boolean value.")
    elif value_type == ValueType.NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Expected a number value.")
    elif value_type == ValueType.ARRAY:
        if not isinstance(value, list):
            raise ValueError("Expected an array value.")
    return value
