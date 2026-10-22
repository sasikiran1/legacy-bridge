"""Turns the portal's OpenAPI document into LLM tools and response validators."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class Parameter(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    location: str
    required: bool
    schema_type: str


class Operation(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    method: str
    path: str
    summary: str
    parameters: tuple[Parameter, ...]
    body_fields: tuple[str, ...]
    response_fields: tuple[str, ...]
    response_is_list: bool

    @property
    def writes(self) -> bool:
        return self.method in {"POST", "PUT", "PATCH", "DELETE"}

    def tool(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required = []
        for p in self.parameters:
            properties[p.name] = {"type": p.schema_type, "description": f"{p.location} parameter"}
            if p.required:
                required.append(p.name)
        for name in self.body_fields:
            properties[name] = {"type": "string", "description": "request body field"}
        return {
            "type": "function",
            "function": {
                "name": self.id,
                "description": self.summary,
                "parameters": {"type": "object", "properties": properties, "required": required},
            },
        }

    def check_response(self, body: Any) -> list[str]:
        """Documented response fields missing from the body; non-empty means the schema drifted."""
        if not self.response_fields:
            return []
        items = body if self.response_is_list and isinstance(body, list) else [body]
        missing: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                return ["<not an object>"]
            missing |= {f for f in self.response_fields if f not in item}
        return sorted(missing)


def _component(spec: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if ref:
        resolved: dict[str, Any] = spec["components"]["schemas"][ref.rsplit("/", 1)[-1]]
        return resolved
    return schema


def parse(spec: dict[str, Any]) -> dict[str, Operation]:
    operations = {}
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            parameters = tuple(
                Parameter(
                    name=p["name"],
                    location=p["in"],
                    required=bool(p.get("required")),
                    schema_type="integer" if p.get("schema", {}).get("type") == "integer" else "string",
                )
                for p in op.get("parameters", [])
            )
            body_fields: tuple[str, ...] = ()
            body = op.get("requestBody", {}).get("content", {}).get("application/json", {}).get("schema")
            if body:
                body_fields = tuple(_component(spec, body).get("properties", {}))
            response_fields: tuple[str, ...] = ()
            is_list = False
            for status in ("200", "201"):
                content: dict[str, Any] = op.get("responses", {}).get(status, {}).get("content", {})
                first: dict[str, Any] = next(iter(content.values()), {})
                schema = first.get("schema")
                if schema:
                    if schema.get("type") == "array":
                        is_list, schema = True, schema.get("items", {})
                    response_fields = tuple(_component(spec, schema).get("properties", {}))
                    break
            operations[op["operationId"]] = Operation(
                id=op["operationId"],
                method=method.upper(),
                path=path,
                summary=" ".join(filter(None, [op.get("summary"), op.get("description")])),
                parameters=parameters,
                body_fields=body_fields,
                response_fields=response_fields,
                response_is_list=is_list,
            )
    return operations
