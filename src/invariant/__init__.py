"""Invariant: A deterministic execution engine for DAGs."""

from importlib.metadata import PackageNotFoundError, version

from invariant.executor import Executor
from invariant.graph import Graph, GraphResolver, GraphVertex
from invariant.graph_serialization import (
    GRAPH_OUTPUT_DATA_URI_PREFIX,
    GRAPH_OUTPUT_MEDIA_TYPE,
    dump_graph,
    dump_graph_output_data_uri,
    dump_graph_output_to_dict,
    dump_graph_to_dict,
    load_graph,
    load_graph_output_data_uri,
    load_graph_output_from_dict,
    load_graph_from_dict,
)
from invariant.node import Node, SubGraphNode
from invariant.params import cel, ref
from invariant.registry import OpRegistry

try:
    __version__ = version("invariant-core")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "Executor",
    "Graph",
    "GraphResolver",
    "GraphVertex",
    "GRAPH_OUTPUT_DATA_URI_PREFIX",
    "GRAPH_OUTPUT_MEDIA_TYPE",
    "Node",
    "OpRegistry",
    "SubGraphNode",
    "cel",
    "dump_graph",
    "dump_graph_output_data_uri",
    "dump_graph_output_to_dict",
    "dump_graph_to_dict",
    "load_graph",
    "load_graph_output_data_uri",
    "load_graph_output_from_dict",
    "load_graph_from_dict",
    "ref",
    "__version__",
]
