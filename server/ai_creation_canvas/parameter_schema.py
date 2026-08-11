"""Validation for the bounded, data-only model parameter schema subset."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping


_SIZE = re.compile(r"([1-9][0-9]{1,4})x([1-9][0-9]{1,4})\Z")


def validate_parameter_schema(schema: Mapping[str, object]) -> None:
    properties = schema.get("properties")
    if schema.get("type") != "object" or schema.get("additionalProperties", False) is not False or not isinstance(properties, Mapping):
        raise ValueError("parameter schema is invalid")
    for name, raw_rule in properties.items():
        if not isinstance(name, str) or not isinstance(raw_rule, Mapping):
            raise ValueError("parameter schema is invalid")
        extension = raw_rule.get("x-ark-size")
        if extension is None:
            continue
        if raw_rule.get("type") != "string" or not isinstance(extension, Mapping) or set(extension) != {"presets", "min_pixels", "max_pixels", "min_ratio", "max_ratio"}:
            raise ValueError("parameter size constraint is invalid")
        presets = extension["presets"]
        numeric = (extension["min_pixels"], extension["max_pixels"], extension["min_ratio"], extension["max_ratio"])
        if (
            not isinstance(presets, list)
            or not presets
            or len(presets) > 16
            or any(not isinstance(item, str) or not item or len(item) > 16 for item in presets)
            or len(set(presets)) != len(presets)
            or any(isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item) or item <= 0 for item in numeric)
            or extension["min_pixels"] > extension["max_pixels"]
            or extension["min_ratio"] > extension["max_ratio"]
        ):
            raise ValueError("parameter size constraint is invalid")


def validate_parameter_values(schema: Mapping[str, object], values: Mapping[str, object]) -> dict[str, object]:
    validate_parameter_schema(schema)
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    if set(values) - set(properties):
        raise ValueError("parameters are invalid")
    result: dict[str, object] = {}
    for key, value in values.items():
        rule = properties[key]
        if not isinstance(rule, Mapping):
            raise ValueError("parameters are invalid")
        kind = rule.get("type")
        valid = (
            kind == "string" and isinstance(value, str)
            or kind == "boolean" and isinstance(value, bool)
            or kind == "integer" and isinstance(value, int) and not isinstance(value, bool)
            or kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
        )
        if not valid or "enum" in rule and value not in rule["enum"]:
            raise ValueError("parameters are invalid")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"] or "maximum" in rule and value > rule["maximum"]:
                raise ValueError("parameters are invalid")
        if "x-ark-size" in rule and isinstance(value, str):
            _validate_size(value, rule["x-ark-size"])
        result[key] = value
    return result


def _validate_size(value: str, raw_constraint: object) -> None:
    if not isinstance(raw_constraint, Mapping):
        raise ValueError("parameters are invalid")
    presets = raw_constraint["presets"]
    if value in presets:
        return
    match = _SIZE.fullmatch(value)
    if match is None:
        raise ValueError("parameters are invalid")
    width, height = (int(match.group(1)), int(match.group(2)))
    pixels = width * height
    ratio = width / height
    if not raw_constraint["min_pixels"] <= pixels <= raw_constraint["max_pixels"] or not raw_constraint["min_ratio"] <= ratio <= raw_constraint["max_ratio"]:
        raise ValueError("parameters are invalid")
