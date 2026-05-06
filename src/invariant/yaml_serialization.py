"""YAML authoring format for Invariant graphs.

YAML is a human-editable input format over the existing graph JSON model. It is
load-only: canonical serialization remains JSON through graph_serialization.py.
"""

from __future__ import annotations

from typing import Any

from invariant.graph import Graph
from invariant.graph_serialization import (
    load_graph_from_dict,
    load_graph_output_from_dict,
)

YAML_INSTALL_GUIDANCE = (
    "YAML graph loading requires PyYAML. Install it with: "
    "pip install invariant-core[yaml]"
)


def _require_yaml() -> Any:
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(YAML_INSTALL_GUIDANCE) from exc
    return yaml


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


def _make_loader(yaml: Any) -> type:
    class InvariantYamlLoader(yaml.SafeLoader):
        pass

    InvariantYamlLoader.add_constructor("!ref", _construct_ref)
    InvariantYamlLoader.add_constructor("!cel", _construct_cel)
    InvariantYamlLoader.add_constructor("!decimal", _construct_decimal)
    InvariantYamlLoader.add_constructor("!tuple", _construct_tuple)
    InvariantYamlLoader.add_constructor("!literal", _construct_literal)
    InvariantYamlLoader.add_constructor("!icacheable", _construct_icacheable)
    return InvariantYamlLoader


def _load_yaml_document(data: str | bytes) -> dict[str, Any]:
    yaml = _require_yaml()
    if isinstance(data, bytes):
        data = data.decode("utf-8")

    obj = yaml.load(data, Loader=_make_loader(yaml))
    if not isinstance(obj, dict):
        raise ValueError("YAML graph document must be an object")
    return obj


def load_graph_yaml(
    data: str | bytes, legacy_kind_inference: bool = False
) -> Graph:
    """Load a graph envelope from YAML.

    YAML tags map to the same marker objects as the JSON graph model:
    ``!ref``, ``!cel``, ``!decimal``, ``!tuple``, ``!literal``, and
    ``!icacheable``.
    """

    return load_graph_from_dict(_load_yaml_document(data), legacy_kind_inference)


def load_graph_output_yaml(
    data: str | bytes, legacy_kind_inference: bool = False
) -> tuple[Graph, str]:
    """Load a graph-output wrapper document from YAML."""

    return load_graph_output_from_dict(
        _load_yaml_document(data), legacy_kind_inference
    )
