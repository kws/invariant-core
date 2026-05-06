"""Graph serialization: JSON wire format for Invariant graphs.

Encodes graphs (Node, SubGraphNode, SwitchNode) and params (ref, cel, Decimal, tuple,
ICacheable) for storage and transmission. Distinct from artifact serialization
in store/codec.py.
"""

import base64
import importlib
import json
from decimal import Decimal
from io import BytesIO
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from invariant.graph import Graph
from invariant.node import Node, SubGraphNode, SwitchNode
from invariant.params import cel, ref
from invariant.protocol import ICacheable

SUPPORTED_VERSIONS = {1}
FORMAT_ID = "invariant-graph"
GRAPH_MEDIA_TYPE = "application/vnd.invariant.graph+json"
GRAPH_DATA_URI_PREFIX = f"data:{GRAPH_MEDIA_TYPE};base64,"

RESERVED_KEYS = frozenset(
    {"$ref", "$cel", "$decimal", "$tuple", "$literal", "$icacheable"}
)


def _encode_param_value(value: Any) -> Any:
    """Recursively encode a parameter value to JSON-serializable form."""
    # ref marker
    if isinstance(value, ref):
        return {"$ref": value.dep}

    # cel marker
    if isinstance(value, cel):
        return {"$cel": value.expr}

    # Decimal
    if isinstance(value, Decimal):
        return {"$decimal": str(value)}

    # tuple
    if isinstance(value, tuple):
        return {"$tuple": [_encode_param_value(item) for item in value]}

    # ICacheable
    if isinstance(value, ICacheable):
        type_name = f"{value.__class__.__module__}.{value.__class__.__name__}"
        if hasattr(value, "to_json_value") and callable(value.to_json_value):
            return {"$icacheable": {"type": type_name, "value": value.to_json_value()}}
        stream = BytesIO()
        value.to_stream(stream)
        payload_b64 = base64.b64encode(stream.getvalue()).decode("ascii")
        return {"$icacheable": {"type": type_name, "payload_b64": payload_b64}}

    # dict
    if isinstance(value, dict):
        encoded = {k: _encode_param_value(v) for k, v in value.items()}
        # Collision: plain dict that would decode as marker -> wrap in $literal
        if len(encoded) == 1:
            (single_key,) = encoded.keys()
            if single_key in RESERVED_KEYS:
                return {"$literal": encoded}
        return encoded

    # list
    if isinstance(value, list):
        return [_encode_param_value(item) for item in value]

    # Primitives: None, bool, int, str
    return value


def _decode_param_value(obj: Any, literal_mode: bool = False) -> Any:
    """Recursively decode a JSON value to Python parameter value."""
    # In literal mode, never treat dicts as markers
    if literal_mode:
        if isinstance(obj, dict):
            return {
                k: _decode_param_value(v, literal_mode=True) for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [_decode_param_value(item, literal_mode=True) for item in obj]
        return obj

    # Single-key dict with reserved key -> marker or escape
    if isinstance(obj, dict):
        if len(obj) == 1:
            (key, val) = next(iter(obj.items()))
            if key == "$ref":
                return ref(val)
            if key == "$cel":
                return cel(val)
            if key == "$decimal":
                return Decimal(val)
            if key == "$tuple":
                return tuple(_decode_param_value(item) for item in val)
            if key == "$literal":
                return _decode_param_value(val, literal_mode=True)
            if key == "$icacheable":
                return _decode_icacheable(val)
        # Multi-key or non-reserved: recursive decode
        return {k: _decode_param_value(v) for k, v in obj.items()}

    if isinstance(obj, list):
        return [_decode_param_value(item) for item in obj]

    return obj


def _decode_icacheable(obj: dict) -> Any:
    """Decode $icacheable object to ICacheable instance."""
    if not isinstance(obj, dict):
        raise ValueError("$icacheable value must be an object")
    type_name = obj.get("type")
    if not type_name or not isinstance(type_name, str):
        raise ValueError("$icacheable must have non-empty string 'type'")
    if "payload_b64" in obj and "value" in obj:
        raise ValueError(
            "$icacheable must have exactly one of 'payload_b64' or 'value'"
        )
    if "payload_b64" not in obj and "value" not in obj:
        raise ValueError("$icacheable must have 'payload_b64' or 'value'")

    module_path, class_name = type_name.rsplit(".", 1)
    try:
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
    except (ImportError, AttributeError) as e:
        raise ValueError(
            f"$icacheable type '{type_name}' could not be imported: {e}"
        ) from e

    if "value" in obj:
        if not hasattr(cls, "from_json_value"):
            raise ValueError(
                f"$icacheable type '{type_name}' has 'value' but no "
                "from_json_value method"
            )
        return cls.from_json_value(obj["value"])

    # payload_b64
    try:
        payload = base64.b64decode(obj["payload_b64"])
    except Exception as e:
        raise ValueError(f"$icacheable payload_b64 is invalid base64: {e}") from e
    stream = BytesIO(payload)
    try:
        return cls.from_stream(stream)
    except Exception as e:
        raise ValueError(
            f"$icacheable from_stream failed for '{type_name}': {e}"
        ) from e


def _encode_params(params: dict[str, Any]) -> dict[str, Any]:
    """Encode params dict with sorted keys for determinism."""
    return dict(sorted((k, _encode_param_value(v)) for k, v in params.items()))


def _decode_params(obj: dict) -> dict[str, Any]:
    """Decode params dict."""
    return {k: _decode_param_value(v) for k, v in obj.items()}


def dump_value_to_jsonable(value: Any) -> Any:
    """Serialize a cacheable value to the graph JSON marker encoding."""
    return _encode_param_value(value)


def load_value_from_jsonable(obj: Any) -> Any:
    """Deserialize a value from the graph JSON marker encoding."""
    return _decode_param_value(obj)


def _encode_vertex(vertex: Node | SubGraphNode | SwitchNode) -> dict:
    """Encode a single graph vertex to JSON object."""
    if isinstance(vertex, Node):
        result: dict = {
            "kind": "node",
            "op_name": vertex.op_name,
            "params": _encode_params(vertex.params),
            "deps": sorted(vertex.deps),
        }
        if not vertex.cache:
            result["cache"] = False
        return result
    if isinstance(vertex, SubGraphNode):
        return {
            "kind": "subgraph",
            "params": _encode_params(vertex.params),
            "deps": sorted(vertex.deps),
            "graph": _encode_graph(vertex.graph),
            "output": vertex.output,
        }

    # SwitchNode
    result = {
        "kind": "switch",
        "selector": _encode_param_value(vertex.selector),
        "deps": sorted(vertex.deps),
        "cases": dict(sorted(vertex.cases.items())),
    }
    if vertex.default is not None:
        result["default"] = vertex.default
    return {
        key: result[key]
        for key in ("kind", "selector", "deps", "cases", "default")
        if key in result
    }


def _decode_vertex(
    obj: dict, legacy_kind_inference: bool = False
) -> Node | SubGraphNode | SwitchNode:
    """Decode a JSON object to a graph vertex. Validates before construction."""
    if not isinstance(obj, dict):
        raise ValueError("Vertex must be an object")

    kind = obj.get("kind")
    if kind is None and legacy_kind_inference:
        if "op_name" in obj and "graph" not in obj:
            kind = "node"
        elif "graph" in obj and "output" in obj:
            kind = "subgraph"
        else:
            raise ValueError(
                "Vertex has no 'kind' and cannot infer from op_name/graph/output"
            )
    if kind is None:
        raise ValueError("Vertex must have 'kind'")
    if kind not in ("node", "subgraph", "switch"):
        raise ValueError(f"Vertex has unsupported kind: {kind!r}")

    if kind == "node":
        _validate_node(obj, expected_kind=kind)
        return Node(
            op_name=obj["op_name"].strip(),
            params=_decode_params(obj["params"]),
            deps=list(obj["deps"]),
            cache=obj.get("cache", True),
        )
    if kind == "subgraph":
        _validate_subgraph(obj, legacy_kind_inference)
        return SubGraphNode(
            params=_decode_params(obj["params"]),
            deps=list(obj["deps"]),
            graph=_decode_graph(obj["graph"], legacy_kind_inference),
            output=obj["output"],
        )
    if kind == "switch":
        _validate_switch(obj)
        return SwitchNode(
            selector=_decode_param_value(obj["selector"]),
            deps=list(obj["deps"]),
            cases=dict(obj["cases"]),
            default=obj.get("default"),
        )
    raise ValueError(f"Vertex has unsupported kind: {kind!r}")


def _validate_node(obj: dict, expected_kind: str | None = None) -> None:
    """Validate node object before construction."""
    kind = expected_kind if expected_kind is not None else obj.get("kind")
    if kind != "node":
        raise ValueError("Node must have kind 'node'")
    op_name = obj.get("op_name")
    if not isinstance(op_name, str):
        raise ValueError("Node must have string 'op_name'")
    if not op_name.strip():
        raise ValueError("Node op_name cannot be empty")
    if "params" not in obj or not isinstance(obj["params"], dict):
        raise ValueError("Node must have 'params' object")
    if "deps" not in obj or not isinstance(obj["deps"], list):
        raise ValueError("Node must have 'deps' array")
    for i, dep in enumerate(obj["deps"]):
        if not isinstance(dep, str):
            raise ValueError(f"Node deps[{i}] must be string, got {type(dep).__name__}")
    cache_val = obj.get("cache")
    if cache_val is not None and not isinstance(cache_val, bool):
        raise ValueError("Node 'cache' must be boolean when present")


def _validate_subgraph(obj: dict, legacy_kind_inference: bool = False) -> None:
    """Validate subgraph object before construction."""
    kind = obj.get("kind")
    if not legacy_kind_inference and kind != "subgraph":
        raise ValueError("SubGraphNode must have kind 'subgraph'")
    if "params" not in obj or not isinstance(obj["params"], dict):
        raise ValueError("SubGraphNode must have 'params' object")
    if "deps" not in obj or not isinstance(obj["deps"], list):
        raise ValueError("SubGraphNode must have 'deps' array")
    for i, dep in enumerate(obj["deps"]):
        if not isinstance(dep, str):
            raise ValueError(
                f"SubGraphNode deps[{i}] must be string, got {type(dep).__name__}"
            )
    if "graph" not in obj or not isinstance(obj["graph"], dict):
        raise ValueError("SubGraphNode must have 'graph' object")
    output = obj.get("output")
    if not isinstance(output, str):
        raise ValueError("SubGraphNode must have string 'output'")
    if output not in obj["graph"]:
        raise ValueError(
            f"SubGraphNode output '{output}' must be key in graph. "
            f"Graph keys: {list(obj['graph'].keys())}"
        )
    for node_id, vertex_obj in obj["graph"].items():
        _validate_vertex_for_kind(vertex_obj, node_id, legacy_kind_inference)


def _validate_switch(obj: dict) -> None:
    """Validate switch object before construction."""
    if obj.get("kind") != "switch":
        raise ValueError("SwitchNode must have kind 'switch'")
    if "selector" not in obj:
        raise ValueError("SwitchNode must have 'selector'")
    if "deps" not in obj or not isinstance(obj["deps"], list):
        raise ValueError("SwitchNode must have 'deps' array")
    for i, dep in enumerate(obj["deps"]):
        if not isinstance(dep, str):
            raise ValueError(
                f"SwitchNode deps[{i}] must be string, got {type(dep).__name__}"
            )
    if "cases" not in obj or not isinstance(obj["cases"], dict):
        raise ValueError("SwitchNode must have 'cases' object")
    if not obj["cases"]:
        raise ValueError("SwitchNode cases must not be empty")
    for case_key, target in obj["cases"].items():
        if not isinstance(case_key, str):
            raise ValueError("SwitchNode cases keys must be strings")
        if not isinstance(target, str) or not target:
            raise ValueError("SwitchNode cases values must be non-empty strings")
    default = obj.get("default")
    if default is not None and (not isinstance(default, str) or not default):
        raise ValueError("SwitchNode default must be a non-empty string when present")


def _validate_vertex_for_kind(
    vertex_obj: Any, node_id: str, legacy_kind_inference: bool = False
) -> None:
    """Validate a vertex object has valid kind and structure."""
    if not isinstance(vertex_obj, dict):
        raise ValueError(f"Vertex '{node_id}' must be an object")
    kind = vertex_obj.get("kind")
    if kind is None and legacy_kind_inference:
        if "op_name" in vertex_obj and "graph" not in vertex_obj:
            kind = "node"
        elif "graph" in vertex_obj and "output" in vertex_obj:
            kind = "subgraph"
        else:
            raise ValueError(
                f"Vertex '{node_id}' has no 'kind' and cannot infer from "
                "op_name/graph/output"
            )
    if kind == "node":
        _validate_node(vertex_obj, expected_kind="node")
    elif kind == "subgraph":
        _validate_subgraph(vertex_obj, legacy_kind_inference)
    elif kind == "switch":
        _validate_switch(vertex_obj)
    else:
        raise ValueError(f"Vertex '{node_id}' has unsupported kind: {kind!r}")


def _encode_graph(graph: Graph) -> dict:
    """Encode graph to JSON object with sorted keys."""
    return dict(sorted((k, _encode_vertex(v)) for k, v in graph.items()))


def _decode_graph(obj: dict, legacy_kind_inference: bool = False) -> Graph:
    """Decode graph from JSON object."""
    if not isinstance(obj, dict):
        raise ValueError("Graph must be an object")
    result: Graph = {}
    for node_id, vertex_obj in obj.items():
        result[node_id] = _decode_vertex(vertex_obj, legacy_kind_inference)
    _validate_switch_targets(result)
    return result


def _validate_switch_targets(graph: Graph) -> None:
    """Validate that switch branch targets are graph-local node IDs."""
    node_ids = set(graph)
    for node_id, vertex in graph.items():
        if not isinstance(vertex, SwitchNode):
            continue
        targets = list(vertex.cases.values())
        if vertex.default is not None:
            targets.append(vertex.default)
        for target in targets:
            if target not in node_ids:
                raise ValueError(
                    f"SwitchNode '{node_id}' targets '{target}' which must be "
                    f"a key in graph. Graph keys: {list(graph.keys())}"
                )


def _validate_output(graph: Graph, output: str | None) -> None:
    if output is None:
        return
    if not isinstance(output, str) or not output:
        raise ValueError("Document 'output' must be a non-empty string when present")
    if output not in graph:
        raise ValueError(
            f"Document output '{output}' must be key in graph. "
            f"Graph keys: {list(graph.keys())}"
        )


def _validate_output_arg(graph: Graph, output: str | None) -> None:
    if output is None:
        return
    if not isinstance(output, str) or not output:
        raise ValueError("output must be a non-empty string when present")
    if output not in graph:
        raise ValueError(
            f"output '{output}' must be a key in graph. "
            f"Graph keys: {list(graph.keys())}"
        )


def _validate_envelope(obj: dict) -> None:
    """Validate top-level envelope."""
    if not isinstance(obj, dict):
        raise ValueError("Document must be a JSON object")
    fmt = obj.get("format")
    if fmt != FORMAT_ID:
        raise ValueError(f"Document format must be '{FORMAT_ID}', got {fmt!r}")
    version = obj.get("version")
    if version not in SUPPORTED_VERSIONS:
        raise ValueError(
            f"Document version {version} is not supported. "
            f"Supported: {sorted(SUPPORTED_VERSIONS)}"
        )
    if "graph" not in obj:
        raise ValueError("Document must have 'graph'")
    if not isinstance(obj["graph"], dict):
        raise ValueError("Document 'graph' must be an object")
    if "output" in obj:
        output = obj["output"]
        if not isinstance(output, str) or not output:
            raise ValueError(
                "Document 'output' must be a non-empty string when present"
            )


def dump_graph_to_dict(graph: Graph, *, output: str | None = None) -> dict:
    """Serialize graph to envelope dict. Deterministic (sorted keys)."""
    _validate_output_arg(graph, output)
    document = {
        "format": FORMAT_ID,
        "version": 1,
    }
    if output is not None:
        document["output"] = output
    document["graph"] = _encode_graph(graph)
    return document


def dump_graph(graph: Graph, *, output: str | None = None) -> str:
    """Serialize graph to JSON string. Deterministic output."""
    return json.dumps(dump_graph_to_dict(graph, output=output), sort_keys=True)


def load_graph_document_from_dict(
    obj: dict, legacy_kind_inference: bool = False
) -> tuple[Graph, str | None]:
    """Load graph document from envelope dict, preserving optional output."""
    _validate_envelope(obj)
    graph = _decode_graph(obj["graph"], legacy_kind_inference)
    output = obj.get("output")
    _validate_output(graph, output)
    return graph, output


def load_graph_from_dict(obj: dict, legacy_kind_inference: bool = False) -> Graph:
    """Load graph from envelope dict, discarding optional document output."""
    graph, _output = load_graph_document_from_dict(obj, legacy_kind_inference)
    return graph


def load_graph_document(
    data: str | bytes, legacy_kind_inference: bool = False
) -> tuple[Graph, str | None]:
    """Deserialize JSON string or bytes to graph document."""
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    obj = json.loads(data)
    return load_graph_document_from_dict(obj, legacy_kind_inference)


def load_graph(data: str | bytes, legacy_kind_inference: bool = False) -> Graph:
    """Deserialize JSON string or bytes to graph, discarding document output."""
    graph, _output = load_graph_document(data, legacy_kind_inference)
    return graph


def _encode_graph_document_payload(graph: Graph, output: str | None = None) -> str:
    payload = json.dumps(
        dump_graph_to_dict(graph, output=output),
        separators=(",", ":"),
        sort_keys=True,
    )
    return base64.b64encode(payload.encode("utf-8")).decode("ascii")


def _query_value_to_string(value: Any) -> str:
    if isinstance(value, str):
        try:
            json.loads(value)
        except json.JSONDecodeError:
            return value
    return json.dumps(
        dump_value_to_jsonable(value),
        separators=(",", ":"),
        sort_keys=True,
    )


def _query_value_from_string(value: str) -> Any:
    try:
        return load_value_from_jsonable(json.loads(value))
    except json.JSONDecodeError:
        return value


def dump_graph_data_uri(
    graph: Graph,
    *,
    output: str | None = None,
    context: dict[str, Any] | None = None,
) -> str:
    """Serialize a graph document plus optional query context as a data URI."""

    encoded = _encode_graph_document_payload(graph, output)
    uri = f"{GRAPH_DATA_URI_PREFIX}{encoded}"
    if not context:
        return uri

    for key in context:
        if not isinstance(key, str) or not key:
            raise ValueError("context keys must be non-empty strings")
    query_pairs = [
        (key, _query_value_to_string(value))
        for key, value in sorted(context.items())
    ]
    return f"{uri}?{urlencode(query_pairs)}"


def graph_data_uri_cache_key(data: str) -> str | None:
    """Return the static graph data URI without query context."""

    if not isinstance(data, str):
        return None

    parts = urlsplit(data)
    graph_path_prefix = f"{GRAPH_MEDIA_TYPE};base64,"
    if parts.scheme != "data" or not parts.path.startswith(graph_path_prefix):
        return None

    if parts.fragment:
        raise ValueError("Invariant graph data URIs must not include fragments")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _decode_query_context(query: str) -> dict[str, Any]:
    context: dict[str, Any] = {}
    for key, value in parse_qsl(query, keep_blank_values=True):
        if not key:
            raise ValueError("Invariant graph data URI query keys must be non-empty")
        if key in context:
            raise ValueError(
                f"Invariant graph data URI query key {key!r} is duplicated"
            )
        context[key] = _query_value_from_string(value)
    return context


def load_graph_data_uri(
    data: str, legacy_kind_inference: bool = False
) -> tuple[Graph, str | None, dict[str, Any]] | None:
    """Decode an Invariant graph data URI with optional query context."""

    cache_key = graph_data_uri_cache_key(data)
    if cache_key is None:
        return None

    parts = urlsplit(data)
    encoded = parts.path[len(f"{GRAPH_MEDIA_TYPE};base64,") :]
    try:
        payload = base64.b64decode(encoded, validate=True)
        obj = json.loads(payload.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"Invalid Invariant graph data URI payload: {exc}") from exc

    graph, output = load_graph_document_from_dict(obj, legacy_kind_inference)
    context = _decode_query_context(parts.query)
    return graph, output, context
