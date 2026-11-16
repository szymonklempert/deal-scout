"""Check a structured review against the JSON schema subset prompts.schema() uses. Pure, standard library only.

The subset: `type` (a name or a list of names such as ["integer", "null"]), `enum` on strings, objects with
`required`, `properties` and `additionalProperties: false`, and arrays with `items`. Anything else in a schema is
ignored. Errors are human-readable paths like "$.specs[3].score: expected integer or null, got str".
"""

from typing import Any

from deal_scout import prompts
from deal_scout.domain import Domain

MAX_ERRORS = 20
OPTIONAL_KEYS = ("card_markdown", "viewing_checks")  # older producers do not send them


def type_name(value: object) -> str:
    """Name a JSON value's type the way the schema does ("integer", "null", ...), for error messages."""
    if value is None:
        return "null"
    return type(value).__name__


def matches(value: object, name: str) -> bool:
    """Whether the value has the JSON-schema type `name`; a bool is neither an integer nor a number."""
    match name:
        case "null":
            return value is None
        case "boolean":
            return isinstance(value, bool)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "string":
            return isinstance(value, str)
        case "array":
            return isinstance(value, list)
        case "object":
            return isinstance(value, dict)
    return False


def check(value: object, node: dict[str, Any], path: str, errors: list[str]) -> None:
    """Append the errors of `value` against the schema `node` to `errors`, stopping at MAX_ERRORS."""
    if len(errors) >= MAX_ERRORS:
        return
    types = node.get("type")
    names = [types] if isinstance(types, str) else list(types or [])
    if names and not any(matches(value, n) for n in names):
        errors.append(f"{path}: expected {' or '.join(names)}, got {type_name(value)}")
        return
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: {value!r} is not one of {node['enum']}")
    if isinstance(value, dict) and "object" in (names or ["object"]):
        check_object(value, node, path, errors)
    elif isinstance(value, list) and "items" in node:
        for i, item in enumerate(value):
            check(item, node["items"], f"{path}[{i}]", errors)


def check_object(value: dict[str, Any], node: dict[str, Any], path: str, errors: list[str]) -> None:
    """Check an object's required keys, unknown keys (when additionalProperties is false) and each property."""
    props = node.get("properties", {})
    errors.extend(f"{path}.{key}: missing" for key in node.get("required", []) if key not in value)
    if node.get("additionalProperties") is False:
        errors.extend(f"{path}.{key}: unexpected key" for key in value if key not in props)
    for key, sub in props.items():
        if key in value:
            check(value[key], sub, f"{path}.{key}", errors)


def validate(data: object, schema: dict[str, Any]) -> list[str]:
    """Return what is wrong with `data` against `schema` (at most MAX_ERRORS messages); empty when it conforms."""
    errors: list[str] = []
    check(data, schema, "$", errors)
    return errors[:MAX_ERRORS]


def validate_review(data: object, domain: Domain) -> list[str]:
    """Validate a review against the domain's schema; card_markdown and viewing_checks may be absent."""
    full = prompts.schema(domain, card=True)
    full["required"] = [key for key in full["required"] if key not in OPTIONAL_KEYS]
    return validate(data, full)
