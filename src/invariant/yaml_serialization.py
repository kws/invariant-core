"""YAML authoring format for Invariant graphs.

YAML is a human-editable input format over the existing graph JSON model. It is
load-only: canonical serialization remains JSON through graph_serialization.py.
"""

from __future__ import annotations

import json
from typing import Any

from invariant.graph import Graph
from invariant.graph_serialization import (
    dump_graph_to_dict,
    load_graph_document_from_dict,
    load_graph_from_dict,
)

YAML_INSTALL_GUIDANCE = (
    "YAML graph loading requires PyYAML. Install it with: "
    "pip install invariant-core[yaml]"
)
RESOURCES_INSTALL_GUIDANCE = (
    "YAML resource subgraph loading requires JustMyResource. Install it with: "
    "pip install invariant-core[resources]"
)

JSON_GRAPH_CONTENT_TYPES = frozenset(
    {"application/vnd.invariant.graph+json", "application/json"}
)
YAML_GRAPH_CONTENT_TYPES = frozenset(
    {
        "application/vnd.invariant.graph+yaml",
        "application/yaml",
        "text/yaml",
        "application/x-yaml",
    }
)
YAML_GRAPH_SUFFIXES = (".yaml", ".yml")


def _require_yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(YAML_INSTALL_GUIDANCE) from exc
    return yaml


def _require_resource_registry() -> Any:
    try:
        from justmyresource import get_default_registry
    except ImportError as exc:
        raise RuntimeError(RESOURCES_INSTALL_GUIDANCE) from exc
    return get_default_registry()


def _construct_ref(loader: Any, node: Any) -> dict[str, str]:
    return {"$ref": loader.construct_scalar(node)}


def _construct_cel(loader: Any, node: Any) -> dict[str, str]:
    return {"$cel": loader.construct_scalar(node)}


def _construct_decimal(loader: Any, node: Any) -> dict[str, str]:
    return {"$decimal": loader.construct_scalar(node)}


def _construct_tuple(loader: Any, node: Any) -> dict[str, list[Any]]:
    if node.id != "sequence":
        raise ValueError("!tuple value must be a sequence")
    return {"$tuple": loader.construct_sequence(node, deep=True)}


def _construct_plain_value(loader: Any, node: Any) -> Any:
    if node.id == "scalar":
        return loader.construct_scalar(node)
    if node.id == "sequence":
        return loader.construct_sequence(node, deep=True)
    if node.id == "mapping":
        return loader.construct_mapping(node, deep=True)
    return loader.construct_object(node, deep=True)


def _construct_literal(loader: Any, node: Any) -> dict[str, Any]:
    return {"$literal": _construct_plain_value(loader, node)}


def _construct_icacheable(loader: Any, node: Any) -> dict[str, dict[str, Any]]:
    if node.id != "mapping":
        raise ValueError("!icacheable value must be a mapping")
    return {"$icacheable": loader.construct_mapping(node, deep=True)}


def _normalize_content_type(content_type: Any) -> str | None:
    if not isinstance(content_type, str) or not content_type.strip():
        return None
    return content_type.split(";", 1)[0].strip().lower()


def _detect_resource_document_format(
    resource_name: str, content_type: Any
) -> str:
    normalized = _normalize_content_type(content_type)
    if normalized in JSON_GRAPH_CONTENT_TYPES:
        return "json"
    if normalized in YAML_GRAPH_CONTENT_TYPES:
        return "yaml"
    if resource_name.lower().endswith(YAML_GRAPH_SUFFIXES):
        return "yaml"
    content_type_label = normalized if normalized is not None else "<missing>"
    raise ValueError(
        f"YAML subgraph resource {resource_name!r} has unsupported content type "
        f"{content_type_label!r}"
    )


def _resource_text(resource: Any, resource_name: str) -> str:
    text = getattr(resource, "text", None)
    if isinstance(text, str):
        return text

    data = getattr(resource, "data", None)
    if isinstance(data, str):
        return data
    if isinstance(data, bytes):
        encoding = getattr(resource, "encoding", None) or "utf-8"
        return data.decode(encoding)

    raise ValueError(f"YAML subgraph resource {resource_name!r} has no text data")


def _load_resource_document(
    resource_name: str, resource_stack: tuple[str, ...]
) -> tuple[Graph, str | None]:
    if resource_name in resource_stack:
        cycle = " -> ".join((*resource_stack, resource_name))
        raise ValueError(f"YAML subgraph resource include cycle detected: {cycle}")

    registry = _require_resource_registry()
    try:
        resource = registry.get_resource(resource_name)
    except Exception as exc:
        raise ValueError(
            f"YAML subgraph resource {resource_name!r} could not be resolved: {exc}"
        ) from exc

    document_format = _detect_resource_document_format(
        resource_name, getattr(resource, "content_type", None)
    )
    text = _resource_text(resource, resource_name)

    if document_format == "json":
        obj = json.loads(text)
        if not isinstance(obj, dict):
            raise ValueError(
                f"YAML subgraph resource {resource_name!r} document must be an object"
            )
    else:
        obj = _load_yaml_document(
            text,
            resource_stack=(*resource_stack, resource_name),
        )

    return load_graph_document_from_dict(obj)


def _validate_subgraph_resource_mapping(mapping: dict[str, Any]) -> None:
    allowed_keys = {"resource", "deps", "params", "output"}
    unknown_keys = set(mapping) - allowed_keys
    if unknown_keys:
        raise ValueError(
            "!subgraph has unsupported keys: " + ", ".join(sorted(unknown_keys))
        )

    for key in ("resource", "deps", "params"):
        if key not in mapping:
            raise ValueError(f"!subgraph must have '{key}'")

    resource = mapping["resource"]
    if not isinstance(resource, str) or not resource:
        raise ValueError("!subgraph 'resource' must be a non-empty string")

    deps = mapping["deps"]
    if not isinstance(deps, list):
        raise ValueError("!subgraph 'deps' must be a list")
    for i, dep in enumerate(deps):
        if not isinstance(dep, str):
            raise ValueError(
                f"!subgraph deps[{i}] must be string, got {type(dep).__name__}"
            )

    if not isinstance(mapping["params"], dict):
        raise ValueError("!subgraph 'params' must be a mapping")

    output = mapping.get("output")
    if "output" in mapping and (not isinstance(output, str) or not output):
        raise ValueError("!subgraph 'output' must be a non-empty string when present")


def _construct_subgraph_resource(loader: Any, node: Any) -> dict[str, Any]:
    if node.id != "mapping":
        raise ValueError("!subgraph value must be a mapping")

    mapping = loader.construct_mapping(node, deep=True)
    _validate_subgraph_resource_mapping(mapping)

    graph, document_output = _load_resource_document(
        mapping["resource"],
        getattr(loader, "resource_stack", ()),
    )
    output = mapping.get("output", document_output)
    if output is None:
        raise ValueError(
            f"!subgraph resource {mapping['resource']!r} has no default output; "
            "provide 'output'"
        )

    resource_document = dump_graph_to_dict(graph, output=output)
    return {
        "kind": "subgraph",
        "deps": list(mapping["deps"]),
        "params": dict(mapping["params"]),
        "graph": resource_document["graph"],
        "output": output,
    }


def _make_loader(yaml: Any, resource_stack: tuple[str, ...] = ()) -> type:
    class InvariantYamlLoader(yaml.SafeLoader):
        pass

    InvariantYamlLoader.resource_stack = resource_stack
    InvariantYamlLoader.add_constructor("!ref", _construct_ref)
    InvariantYamlLoader.add_constructor("!cel", _construct_cel)
    InvariantYamlLoader.add_constructor("!decimal", _construct_decimal)
    InvariantYamlLoader.add_constructor("!tuple", _construct_tuple)
    InvariantYamlLoader.add_constructor("!literal", _construct_literal)
    InvariantYamlLoader.add_constructor("!icacheable", _construct_icacheable)
    InvariantYamlLoader.add_constructor("!subgraph", _construct_subgraph_resource)
    return InvariantYamlLoader


def _load_yaml_document(
    data: str | bytes, resource_stack: tuple[str, ...] = ()
) -> dict[str, Any]:
    yaml = _require_yaml()
    if isinstance(data, bytes):
        data = data.decode("utf-8")

    obj = yaml.load(data, Loader=_make_loader(yaml, resource_stack))
    if not isinstance(obj, dict):
        raise ValueError("YAML graph document must be an object")
    return obj


def load_graph_yaml(
    data: str | bytes, legacy_kind_inference: bool = False
) -> Graph:
    """Load a graph envelope from YAML.

    YAML tags map to the same marker objects as the JSON graph model:
    ``!ref``, ``!cel``, ``!decimal``, ``!tuple``, ``!literal``, and
    ``!icacheable``. The YAML-only ``!subgraph`` vertex tag is resolved to a
    normal SubGraphNode before graph validation.
    """

    return load_graph_from_dict(_load_yaml_document(data), legacy_kind_inference)


def load_graph_document_yaml(
    data: str | bytes, legacy_kind_inference: bool = False
) -> tuple[Graph, str | None]:
    """Load a graph document from YAML, preserving optional output."""

    return load_graph_document_from_dict(
        _load_yaml_document(data), legacy_kind_inference
    )
