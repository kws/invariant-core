"""Tests for graph serialization (JSON wire format)."""

import base64
import hashlib
from decimal import Decimal
from typing import BinaryIO

import pytest
from invariant import Node, SubGraphNode, SwitchNode, cel, ref
from invariant.graph_serialization import (
    GRAPH_DATA_URI_PREFIX,
    SUPPORTED_VERSIONS,
    dump_graph,
    dump_graph_data_uri,
    dump_graph_to_dict,
    graph_data_uri_cache_key,
    load_graph,
    load_graph_data_uri,
    load_graph_document,
    load_graph_document_from_dict,
    load_graph_from_dict,
)
from invariant.protocol import ICacheable
from invariant.types import Polynomial


class MinimalICacheable:
    """ICacheable without IJsonRepresentable - uses payload_b64 only."""

    def __init__(self, value: int) -> None:
        self.value = value

    def get_stable_hash(self) -> str:
        return hashlib.sha256(str(self.value).encode()).hexdigest()

    def to_stream(self, stream: BinaryIO) -> None:
        stream.write(self.value.to_bytes(8, byteorder="big", signed=True))

    @classmethod
    def from_stream(cls, stream: BinaryIO) -> "MinimalICacheable":
        return cls(int.from_bytes(stream.read(8), byteorder="big", signed=True))


# Register as ICacheable for isinstance (protocol is structural)
assert isinstance(MinimalICacheable(1), ICacheable)


def _graphs_equal(g1: dict, g2: dict) -> bool:
    """Structural equality for graphs."""
    if set(g1.keys()) != set(g2.keys()):
        return False
    for k in g1:
        v1, v2 = g1[k], g2[k]
        if type(v1) is not type(v2):
            return False
        if isinstance(v1, Node):
            if v1.op_name != v2.op_name or v1.deps != v2.deps or v1.cache != v2.cache:
                return False
            if v1.params != v2.params:
                return False
        elif isinstance(v1, SubGraphNode):
            if v1.deps != v2.deps or v1.output != v2.output:
                return False
            if v1.params != v2.params:
                return False
            if not _graphs_equal(v1.graph, v2.graph):
                return False
        else:
            if (
                v1.selector != v2.selector
                or v1.deps != v2.deps
                or v1.cases != v2.cases
                or v1.default != v2.default
            ):
                return False
    return True


class TestRoundTrip:
    """Round-trip serialization."""

    def test_simple_graph(self):
        """Graph with two nodes round-trips."""
        graph = {
            "a": Node(op_name="stdlib:identity", params={"value": 5}, deps=[]),
            "b": Node(op_name="stdlib:add", params={"a": ref("a"), "b": 3}, deps=["a"]),
        }
        s = dump_graph(graph)
        g2 = load_graph(s)
        assert _graphs_equal(graph, g2)

    def test_complete_example(self):
        """Spec Section 8 example: x, y, sum subgraph, double."""
        inner = {
            "sum": Node(
                op_name="stdlib:add",
                params={"a": ref("left"), "b": ref("right")},
                deps=["left", "right"],
            ),
        }
        graph = {
            "x": Node(op_name="stdlib:identity", params={"value": 5}, deps=[]),
            "y": Node(op_name="stdlib:identity", params={"value": 3}, deps=[]),
            "sum": SubGraphNode(
                params={"left": ref("x"), "right": ref("y")},
                deps=["x", "y"],
                graph=inner,
                output="sum",
            ),
            "double": Node(
                op_name="stdlib:multiply",
                params={"a": ref("sum"), "b": 2},
                deps=["sum"],
            ),
        }
        s = dump_graph(graph)
        g2 = load_graph(s)
        assert _graphs_equal(graph, g2)

    def test_cache_false_round_trip(self):
        """Graph with cache=False round-trips."""
        graph = {
            "ephemeral": Node(
                op_name="op",
                params={"value": 1},
                deps=[],
                cache=False,
            ),
        }
        s = dump_graph(graph)
        g2 = load_graph(s)
        assert _graphs_equal(graph, g2)
        assert g2["ephemeral"].cache is False

    def test_switch_round_trip(self):
        """Graph with SwitchNode round-trips."""
        graph = {
            "plain": Node(op_name="op", params={"value": "plain"}, deps=[]),
            "wide": Node(op_name="op", params={"value": "wide"}, deps=[]),
            "choose": SwitchNode(
                selector=cel("image == null ? 'plain' : 'wide'"),
                deps=["image"],
                cases={"plain": "plain", "wide": "wide"},
                default="plain",
            ),
        }

        g2 = load_graph(dump_graph(graph))

        assert _graphs_equal(graph, g2)
        assert dump_graph_to_dict(graph)["graph"]["choose"] == {
            "kind": "switch",
            "selector": {"$cel": "image == null ? 'plain' : 'wide'"},
            "deps": ["image"],
            "cases": {"plain": "plain", "wide": "wide"},
            "default": "plain",
        }

    def test_cache_backwards_compatibility(self):
        """JSON without cache key decodes to cache=True."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "a": {
                    "kind": "node",
                    "op_name": "op",
                    "params": {},
                    "deps": [],
                },
            },
        }
        g = load_graph_from_dict(doc)
        assert g["a"].cache is True

    def test_cache_omit_when_true(self):
        """Encoded Node with cache=True does not include cache in output."""
        graph = {"a": Node(op_name="op", params={}, deps=[])}
        d = dump_graph_to_dict(graph)
        node_obj = d["graph"]["a"]
        assert "cache" not in node_obj


class TestParamEncoding:
    """Parameter value encoding."""

    def test_ref(self):
        """ref marker encodes to $ref."""
        graph = {"a": Node(op_name="op", params={"x": ref("b")}, deps=["b"])}
        s = dump_graph(graph)
        assert '"$ref"' in s
        g2 = load_graph(s)
        assert isinstance(g2["a"].params["x"], ref)
        assert g2["a"].params["x"].dep == "b"

    def test_cel(self):
        """cel marker encodes to $cel."""
        graph = {"a": Node(op_name="op", params={"w": cel("x + 1")}, deps=["x"])}
        s = dump_graph(graph)
        assert '"$cel"' in s
        g2 = load_graph(s)
        assert isinstance(g2["a"].params["w"], cel)
        assert g2["a"].params["w"].expr == "x + 1"

    def test_decimal(self):
        """Decimal encodes to $decimal."""
        graph = {"a": Node(op_name="op", params={"p": Decimal("19.99")}, deps=[])}
        s = dump_graph(graph)
        assert '"$decimal"' in s
        g2 = load_graph(s)
        assert g2["a"].params["p"] == Decimal("19.99")

    def test_tuple(self):
        """Tuple encodes to $tuple."""
        graph = {"a": Node(op_name="op", params={"coords": (1, 2)}, deps=[])}
        s = dump_graph(graph)
        assert '"$tuple"' in s
        g2 = load_graph(s)
        assert g2["a"].params["coords"] == (1, 2)

    def test_tuple_with_markers(self):
        """Tuple can contain ref and cel."""
        graph = {
            "a": Node(
                op_name="op",
                params={"pair": (ref("x"), cel("y + 1"))},
                deps=["x", "y"],
            )
        }
        g2 = load_graph(dump_graph(graph))
        assert g2["a"].params["pair"][0].dep == "x"
        assert g2["a"].params["pair"][1].expr == "y + 1"

    def test_nested_structures(self):
        """Nested dicts and lists encode recursively."""
        graph = {
            "a": Node(
                op_name="op",
                params={"nested": {"inner": [1, ref("b"), {"k": cel("x")}]}},
                deps=["b", "x"],
            )
        }
        g2 = load_graph(dump_graph(graph))
        assert g2["a"].params["nested"]["inner"][0] == 1
        assert g2["a"].params["nested"]["inner"][1].dep == "b"
        assert g2["a"].params["nested"]["inner"][2]["k"].expr == "x"

    def test_literal_escape(self):
        """Plain dict that looks like marker wraps in $literal."""
        from invariant.graph_serialization import (
            _decode_param_value,
            _encode_param_value,
        )

        literal_dict = {"$ref": "x"}  # Plain dict, not ref("x")
        encoded = _encode_param_value(literal_dict)
        assert "$literal" in encoded
        assert encoded["$literal"] == {"$ref": "x"}
        decoded = _decode_param_value(encoded)
        assert decoded == {"$ref": "x"}


class TestICacheable:
    """ICacheable encoding (Polynomial)."""

    def test_polynomial_value_form(self):
        """Polynomial with IJsonRepresentable uses value not payload_b64."""
        graph = {
            "p": Node(op_name="op", params={"poly": Polynomial([1, 2, 3])}, deps=[])
        }
        s = dump_graph(graph)
        assert '"value"' in s
        assert "payload_b64" not in s
        assert "coefficients" in s
        g2 = load_graph(s)
        assert g2["p"].params["poly"].coefficients == (1, 2, 3)

    def test_icacheable_payload_b64_form(self):
        """ICacheable without IJsonRepresentable uses payload_b64."""
        graph = {
            "a": Node(
                op_name="op",
                params={"obj": MinimalICacheable(42)},
                deps=[],
            )
        }
        s = dump_graph(graph)
        assert "payload_b64" in s
        assert "value" not in s
        g2 = load_graph(s)
        assert isinstance(g2["a"].params["obj"], MinimalICacheable)
        assert g2["a"].params["obj"].value == 42


class TestValidation:
    """Validation rejects malformed input."""

    def test_envelope_format(self):
        """Reject wrong format."""
        with pytest.raises(ValueError, match="format must be"):
            load_graph_from_dict({"format": "other", "version": 1, "graph": {}})

    def test_envelope_version(self):
        """Reject unsupported version."""
        with pytest.raises(ValueError, match="version.*not supported"):
            load_graph_from_dict(
                {"format": "invariant-graph", "version": 99, "graph": {}}
            )

    def test_envelope_missing_graph(self):
        """Reject missing graph."""
        with pytest.raises(ValueError, match="'graph'"):
            load_graph_from_dict({"format": "invariant-graph", "version": 1})

    def test_node_wrong_kind(self):
        """Reject vertex with unsupported kind."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "a": {"kind": "invalid", "op_name": "op", "params": {}, "deps": []},
            },
        }
        with pytest.raises(ValueError, match="unsupported kind"):
            load_graph_from_dict(doc)

    def test_node_missing_op_name(self):
        """Reject node without op_name."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {"a": {"kind": "node", "params": {}, "deps": []}},
        }
        with pytest.raises(ValueError, match="op_name"):
            load_graph_from_dict(doc)

    def test_node_cache_must_be_bool(self):
        """Reject node with non-boolean cache."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "a": {
                    "kind": "node",
                    "op_name": "op",
                    "params": {},
                    "deps": [],
                    "cache": "yes",
                },
            },
        }
        with pytest.raises(ValueError, match="cache.*boolean"):
            load_graph_from_dict(doc)

    def test_subgraph_output_not_in_graph(self):
        """Reject subgraph with output not in graph."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "sub": {
                    "kind": "subgraph",
                    "params": {},
                    "deps": [],
                    "graph": {
                        "a": {"kind": "node", "op_name": "op", "params": {}, "deps": []}
                    },
                    "output": "missing",
                },
            },
        }
        with pytest.raises(ValueError, match="output.*must be key"):
            load_graph_from_dict(doc)

    def test_switch_cases_must_be_object(self):
        """Reject switch with malformed cases."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "out": {
                    "kind": "switch",
                    "selector": "left",
                    "deps": [],
                    "cases": [],
                },
            },
        }

        with pytest.raises(ValueError, match="cases.*object"):
            load_graph_from_dict(doc)

    def test_switch_target_must_be_in_graph(self):
        """Reject switch target that is not graph-local."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "out": {
                    "kind": "switch",
                    "selector": "left",
                    "deps": [],
                    "cases": {"left": "missing"},
                },
            },
        }

        with pytest.raises(ValueError, match="must be .*key in graph"):
            load_graph_from_dict(doc)

    def test_switch_ref_selector_must_be_declared_dep(self):
        """Reject ref markers in selector that are not declared deps."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "target": {"kind": "node", "op_name": "op", "params": {}, "deps": []},
                "out": {
                    "kind": "switch",
                    "selector": {"$ref": "missing"},
                    "deps": [],
                    "cases": {"left": "target"},
                },
            },
        }

        with pytest.raises(ValueError, match="undeclared dependency"):
            load_graph_from_dict(doc)

    def test_icacheable_both_payload_and_value(self):
        """Reject $icacheable with both payload_b64 and value."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "a": {
                    "kind": "node",
                    "op_name": "op",
                    "params": {
                        "p": {
                            "$icacheable": {
                                "type": "invariant.types.Polynomial",
                                "payload_b64": "AAAAAAA=",
                                "value": {"coefficients": [1, 2, 3]},
                            }
                        }
                    },
                    "deps": [],
                },
            },
        }
        with pytest.raises(ValueError, match="exactly one"):
            load_graph_from_dict(doc)

    def test_icacheable_polynomial_missing_coefficients(self):
        """Reject Polynomial value form without coefficients key."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "a": {
                    "kind": "node",
                    "op_name": "op",
                    "params": {
                        "p": {
                            "$icacheable": {
                                "type": "invariant.types.Polynomial",
                                "value": {},
                            }
                        }
                    },
                    "deps": [],
                },
            },
        }
        with pytest.raises(ValueError, match="coefficients"):
            load_graph_from_dict(doc)

    def test_icacheable_invalid_type(self):
        """Reject $icacheable with non-importable type."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "a": {
                    "kind": "node",
                    "op_name": "op",
                    "params": {
                        "p": {
                            "$icacheable": {
                                "type": "nonexistent.module.FakeClass",
                                "value": {},
                            }
                        }
                    },
                    "deps": [],
                },
            },
        }
        with pytest.raises(ValueError, match="could not be imported"):
            load_graph_from_dict(doc)


class TestDeterminism:
    """Deterministic output."""

    def test_identical_output(self):
        """Same graph produces identical JSON bytes."""
        graph = {
            "z": Node(op_name="op", params={"a": 1, "b": 2, "c": 3}, deps=[]),
            "a": Node(op_name="op", params={"x": ref("z"), "y": 5}, deps=["z"]),
            "m": Node(op_name="op", params={"p": Decimal("1.5"), "q": (1, 2)}, deps=[]),
        }
        s1 = dump_graph(graph)
        s2 = dump_graph(graph)
        assert s1 == s2

    def test_dict_equals_dump(self):
        """dump_graph_to_dict + json.dumps(sort_keys=True) equals dump_graph."""
        graph = {
            "a": Node(op_name="op", params={"x": 1, "y": 2}, deps=[]),
            "b": Node(op_name="op", params={"z": ref("a")}, deps=["a"]),
        }
        import json

        d = dump_graph_to_dict(graph)
        s_from_dict = json.dumps(d, sort_keys=True)
        s_direct = dump_graph(graph)
        assert s_from_dict == s_direct


class TestLoadGraphInput:
    """load_graph accepts str and bytes."""

    def test_str_input(self):
        """load_graph accepts str."""
        graph = {"a": Node(op_name="op", params={}, deps=[])}
        s = dump_graph(graph)
        g2 = load_graph(s)
        assert _graphs_equal(graph, g2)

    def test_bytes_input(self):
        """load_graph accepts bytes, decodes utf-8."""
        graph = {"a": Node(op_name="op", params={}, deps=[])}
        s = dump_graph(graph)
        g2 = load_graph(s.encode("utf-8"))
        assert _graphs_equal(graph, g2)


class TestLegacyKindInference:
    """Optional legacy kind inference."""

    def test_legacy_node_without_kind(self):
        """Vertex with op_name and no graph infers as node."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "a": {"op_name": "op", "params": {}, "deps": []},
            },
        }
        g = load_graph_from_dict(doc, legacy_kind_inference=True)
        assert isinstance(g["a"], Node)
        assert g["a"].op_name == "op"

    def test_legacy_subgraph_without_kind(self):
        """Vertex with graph and output infers as subgraph."""
        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {
                "sub": {
                    "params": {},
                    "deps": [],
                    "graph": {
                        "a": {
                            "kind": "node",
                            "op_name": "op",
                            "params": {},
                            "deps": [],
                        },
                    },
                    "output": "a",
                },
            },
        }
        g = load_graph_from_dict(doc, legacy_kind_inference=True)
        assert isinstance(g["sub"], SubGraphNode)
        assert g["sub"].output == "a"

    def test_load_graph_accepts_legacy_kind_inference(self):
        """load_graph passes legacy_kind_inference to load_graph_from_dict."""
        import json

        doc = {
            "format": "invariant-graph",
            "version": 1,
            "graph": {"a": {"op_name": "op", "params": {}, "deps": []}},
        }
        with pytest.raises(ValueError, match="kind"):
            load_graph(json.dumps(doc))  # No legacy, fails
        g = load_graph(json.dumps(doc), legacy_kind_inference=True)
        assert isinstance(g["a"], Node)


class TestNestedSubgraphs:
    """Nested SubGraphNode support."""

    def test_nested_subgraph_roundtrip(self):
        """Graph with nested subgraph round-trips."""
        innermost = {
            "x": Node(op_name="op", params={"v": 1}, deps=[]),
        }
        inner = {
            "inner": SubGraphNode(
                params={},
                deps=[],
                graph=innermost,
                output="x",
            ),
        }
        graph = {
            "outer": SubGraphNode(
                params={},
                deps=[],
                graph=inner,
                output="inner",
            ),
        }
        g2 = load_graph(dump_graph(graph))
        assert isinstance(g2["outer"], SubGraphNode)
        assert isinstance(g2["outer"].graph["inner"], SubGraphNode)
        assert g2["outer"].graph["inner"].output == "x"


class TestGraphDocumentOutput:
    def test_graph_document_output_round_trip(self):
        graph = {
            "bg": Node(
                op_name="stdlib:identity",
                params={"value": 5},
                deps=[],
            )
        }

        document = dump_graph_to_dict(graph, output="bg")
        result_graph, result_output = load_graph_document_from_dict(document)

        assert result_output == "bg"
        assert _graphs_equal(graph, result_graph)
        assert load_graph_from_dict(document)["bg"].params["value"] == 5

    def test_graph_document_output_is_optional(self):
        graph = {
            "output": Node(
                op_name="stdlib:identity",
                params={"value": 5},
                deps=[],
            )
        }

        result_graph, result_output = load_graph_document_from_dict(
            dump_graph_to_dict(graph)
        )

        assert result_output is None
        assert _graphs_equal(graph, result_graph)

    def test_graph_document_output_must_be_in_graph(self):
        graph = {
            "bg": Node(
                op_name="stdlib:identity",
                params={"value": 5},
                deps=[],
            )
        }

        with pytest.raises(ValueError, match="must be a key in graph"):
            dump_graph_to_dict(graph, output="missing")

        doc = dump_graph_to_dict(graph) | {"output": "missing"}
        with pytest.raises(ValueError, match="must be key in graph"):
            load_graph_document_from_dict(doc)

    def test_load_graph_document_accepts_bytes(self):
        graph = {
            "bg": Node(
                op_name="stdlib:identity",
                params={"value": 5},
                deps=[],
            )
        }

        result_graph, result_output = load_graph_document(
            dump_graph(graph, output="bg").encode("utf-8")
        )

        assert result_output == "bg"
        assert _graphs_equal(graph, result_graph)

    def test_graph_data_uri_round_trip(self):
        graph = {
            "bg": Node(
                op_name="stdlib:identity",
                params={"value": 5},
                deps=[],
            )
        }

        data_uri = dump_graph_data_uri(
            graph,
            output="bg",
            context={
                "text": "Kitchen",
                "width": 144,
                "missing": None,
                "output": "ordinary-context-value",
                "literal_number": "5",
            },
        )
        parsed = load_graph_data_uri(data_uri)

        assert data_uri.startswith(GRAPH_DATA_URI_PREFIX)
        assert graph_data_uri_cache_key(data_uri) == data_uri.split("?", 1)[0]
        assert parsed is not None
        result_graph, result_output, context = parsed
        assert result_output == "bg"
        assert _graphs_equal(graph, result_graph)
        assert context == {
            "literal_number": "5",
            "missing": None,
            "output": "ordinary-context-value",
            "text": "Kitchen",
            "width": 144,
        }

    def test_graph_data_uri_json_fallback_query_decoding(self):
        graph = {
            "bg": Node(
                op_name="stdlib:identity",
                params={"value": 5},
                deps=[],
            )
        }

        uri = (
            dump_graph_data_uri(graph)
            + '?label=Kitchen&count=5&enabled=true&missing=null&items=[1,2]'
            + '&payload={"$tuple":[1,{"$decimal":"2.50"}]}'
        )

        parsed = load_graph_data_uri(uri)

        assert parsed is not None
        _graph, _output, context = parsed
        assert context == {
            "label": "Kitchen",
            "count": 5,
            "enabled": True,
            "missing": None,
            "items": [1, 2],
            "payload": (1, Decimal("2.50")),
        }

    def test_graph_data_uri_invalid_returns_none(self):
        assert load_graph_data_uri("data:image/png;base64,abc") is None
        assert graph_data_uri_cache_key("data:image/png;base64,abc") is None

    def test_graph_data_uri_matching_malformed_values_raise(self):
        graph = {
            "bg": Node(
                op_name="stdlib:identity",
                params={"value": 5},
                deps=[],
            )
        }

        with pytest.raises(ValueError, match="payload"):
            load_graph_data_uri(f"{GRAPH_DATA_URI_PREFIX}not-base64")
        bad_json = base64.b64encode(b"{not json").decode("ascii")
        with pytest.raises(ValueError, match="payload"):
            load_graph_data_uri(f"{GRAPH_DATA_URI_PREFIX}{bad_json}")
        with pytest.raises(ValueError, match="duplicated"):
            load_graph_data_uri(dump_graph_data_uri(graph) + "?a=1&a=2")
        with pytest.raises(ValueError, match="non-empty"):
            load_graph_data_uri(dump_graph_data_uri(graph) + "?=1")
        with pytest.raises(ValueError, match="fragments"):
            load_graph_data_uri(dump_graph_data_uri(graph) + "#frag")


class TestConstants:
    """Module constants."""

    def test_supported_versions(self):
        """SUPPORTED_VERSIONS contains 1."""
        assert 1 in SUPPORTED_VERSIONS
