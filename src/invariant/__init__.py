"""Invariant: A deterministic execution engine for DAGs."""

from importlib.metadata import PackageNotFoundError, version

from invariant.async_executor import AsyncExecutor
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
from invariant.registry import OpBinding, OpRegistry
from invariant.scheduler import (
    InlineScheduler,
    InvocationRequest,
    ProcessPoolScheduler,
    RoutingScheduler,
    ThreadPoolScheduler,
)
from invariant.traits import OpTrait, op_traits
from invariant.yaml_serialization import load_graph_document_yaml, load_graph_yaml

try:
    __version__ = version("invariant-core")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "AsyncExecutor",
    "Executor",
    "Graph",
    "GraphResolver",
    "GraphVertex",
    "GRAPH_DATA_URI_PREFIX",
    "GRAPH_MEDIA_TYPE",
    "InlineScheduler",
    "InvocationRequest",
    "Node",
    "OpBinding",
    "OpRegistry",
    "OpTrait",
    "ProcessPoolScheduler",
    "RoutingScheduler",
    "SubGraphNode",
    "SwitchNode",
    "ThreadPoolScheduler",
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
    "op_traits",
    "ref",
    "__version__",
]
