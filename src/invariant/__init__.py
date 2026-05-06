"""Invariant: A deterministic execution engine for DAGs."""

from importlib.metadata import PackageNotFoundError, version

from invariant.executor import Executor
from invariant.graph import Graph, GraphResolver, GraphVertex
from invariant.graph_serialization import (
    GRAPH_DATA_URI_PREFIX,
    GRAPH_MEDIA_TYPE,
    dump_graph,
    dump_graph_data_uri,
    dump_graph_to_dict,
    dump_value_to_jsonable,
    graph_data_uri_cache_key,
    load_graph,
    load_graph_data_uri,
    load_graph_document,
    load_graph_document_from_dict,
    load_graph_from_dict,
    load_value_from_jsonable,
)
from invariant.node import Node, SubGraphNode, SwitchNode
from invariant.params import cel, ref
from invariant.registry import OpRegistry
from invariant.yaml_serialization import load_graph_document_yaml, load_graph_yaml

try:
    __version__ = version("invariant-core")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "Executor",
    "Graph",
    "GraphResolver",
    "GraphVertex",
    "GRAPH_DATA_URI_PREFIX",
    "GRAPH_MEDIA_TYPE",
    "Node",
    "OpRegistry",
    "SubGraphNode",
    "SwitchNode",
    "cel",
    "dump_graph",
    "dump_graph_data_uri",
    "dump_graph_to_dict",
    "dump_value_to_jsonable",
    "graph_data_uri_cache_key",
    "load_graph",
    "load_graph_data_uri",
    "load_graph_document",
    "load_graph_document_from_dict",
    "load_graph_document_yaml",
    "load_graph_from_dict",
    "load_graph_yaml",
    "load_value_from_jsonable",
    "ref",
    "__version__",
]
