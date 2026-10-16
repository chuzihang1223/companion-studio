"""A bounded data-path language. No eval, attribute access, imports or code execution."""

import json
import re

from app.services.workflows.contracts import WorkflowError, bounded

PATH = re.compile(r"^(inputs|nodes|variables)(?:\.[A-Za-z0-9_-]+){0,12}$")
INTERPOLATION = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")


def references(value):
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            yield value["$ref"]
        else:
            for item in value.values():
                yield from references(item)
    elif isinstance(value, list):
        for item in value:
            yield from references(item)
    elif isinstance(value, str):
        yield from INTERPOLATION.findall(value)


def lookup(path, context):
    if (
        not isinstance(path, str)
        or not PATH.fullmatch(path)
        or any(p.startswith("_") for p in path.split("."))
    ):
        raise WorkflowError("WORKFLOW_VARIABLE_INVALID")
    value = context
    for part in path.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        elif isinstance(value, list) and part.isdecimal() and int(part) < len(value):
            value = value[int(part)]
        else:
            raise WorkflowError("WORKFLOW_VARIABLE_UNDEFINED")
    return value


def resolve(value, context, depth=0):
    if depth > 16:
        raise WorkflowError("WORKFLOW_DEPTH_LIMIT")
    if isinstance(value, dict):
        if set(value) == {"$ref"}:
            # 引用只取出原始数据，不对外部返回的字符串再次插值，防止二次注入。
            return lookup(value["$ref"], context)
        return {key: resolve(item, context, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, context, depth + 1) for item in value]
    if isinstance(value, str):
        match = INTERPOLATION.fullmatch(value)
        if match:
            return lookup(match[1], context)

        def substitute(match):
            item = lookup(match[1], context)
            if not isinstance(item, (str, int, float, bool)):
                raise WorkflowError("WORKFLOW_VARIABLE_TYPE")
            return str(item)

        return INTERPOLATION.sub(substitute, value)
    return value


OPERATORS = {
    "equals",
    "not_equals",
    "greater_than",
    "less_than",
    "contains",
    "exists",
    "is_empty",
    "and",
    "or",
    "not",
}


def validate_condition(expr, depth=0):
    if (
        depth > 8
        or not isinstance(expr, dict)
        or expr.get("op") not in OPERATORS
        or set(expr) - {"op", "left", "right", "items"}
    ):
        raise WorkflowError("WORKFLOW_CONDITION_INVALID")
    op = expr["op"]
    if op in {"and", "or", "not"}:
        items = expr.get("items")
        if not isinstance(items, list) or not 1 <= len(items) <= (1 if op == "not" else 20):
            raise WorkflowError("WORKFLOW_CONDITION_INVALID")
        for item in items:
            validate_condition(item, depth + 1)
    elif "left" not in expr or (op not in {"exists", "is_empty"} and "right" not in expr):
        raise WorkflowError("WORKFLOW_CONDITION_INVALID")


def condition(expr, context):
    validate_condition(expr)
    op = expr["op"]
    if op in {"and", "or", "not"}:
        values = [condition(e, context) for e in expr["items"]]
        return all(values) if op == "and" else any(values) if op == "or" else not values[0]
    try:
        left = resolve(expr["left"], context)
    except WorkflowError as error:
        if op == "exists" and error.code == "WORKFLOW_VARIABLE_UNDEFINED":
            return False
        raise
    if op == "exists":
        return left is not None
    if op == "is_empty":
        return left is None or left == "" or left == [] or left == {}
    right = resolve(expr["right"], context)
    if op in {"equals", "not_equals"}:
        equal = type(left) is type(right) and left == right
        return equal if op == "equals" else not equal
    if op in {"greater_than", "less_than"}:
        if type(left) not in {int, float} or type(right) not in {int, float}:
            raise WorkflowError("WORKFLOW_VARIABLE_TYPE")
        return left > right if op == "greater_than" else left < right
    if op == "contains" and isinstance(left, (str, list, dict)):
        try:
            return right in left
        except TypeError:
            pass
    raise WorkflowError("WORKFLOW_VARIABLE_TYPE")


def transform(config):
    value, op = config.get("value"), config.get("operation")
    if op == "default":
        return config.get("default") if value is None else value
    if op == "concat" and isinstance(value, list):
        if not all(isinstance(v, (str, int, float, bool)) for v in value):
            raise WorkflowError("WORKFLOW_VARIABLE_TYPE")
        return str(config.get("separator", "")).join(map(str, value))
    if op == "number" and type(value) in {str, int, float}:
        try:
            return bounded(float(value))
        except (ValueError, OverflowError):
            raise WorkflowError("WORKFLOW_VARIABLE_TYPE") from None
    if op == "length" and isinstance(value, (str, list, dict)):
        return len(value)
    if op == "select" and isinstance(value, dict):
        fields = config.get("fields", [])
        if not isinstance(fields, list) or any(
            not isinstance(f, str) or f not in value for f in fields
        ):
            raise WorkflowError("WORKFLOW_VARIABLE_UNDEFINED")
        return {f: value[f] for f in fields}
    if op in {"extract", "map"}:
        path = config.get("path", "")
        if not isinstance(path, str) or len(path) > 200:
            raise WorkflowError("WORKFLOW_VARIABLE_INVALID")
        if op == "map" and isinstance(value, list):
            return [lookup("variables." + path, {"variables": v}) for v in value]
        if op == "extract":
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
            except ValueError:
                raise WorkflowError("WORKFLOW_VARIABLE_TYPE") from None
            return lookup("variables." + path, {"variables": parsed})
    raise WorkflowError("WORKFLOW_TRANSFORM_INVALID")
