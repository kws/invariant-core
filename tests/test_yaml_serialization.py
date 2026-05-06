"""Tests for the optional YAML graph authoring format."""

import builtins
from decimal import Decimal
from typing import Any

import pytest
from invariant import Node, SubGraphNode, SwitchNode, cel, ref
from invariant.types import Polynomial
from invariant.yaml_serialization import (
    load_graph_document_yaml,
    load_graph_yaml,
)


def _assert_ref(value: Any, dep: str) -> None:
    assert isinstance(value, ref)
    assert value.dep == dep


def _assert_cel(value: Any, expr: str) -> None:
    assert isinstance(value, cel)
    assert value.expr == expr


def test_loads_plain_graph_envelope_with_explicit_tags():
    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          source:
            kind: node
            op_name: stdlib:identity
            deps: []
            params:
              value: 5
          payload:
            kind: node
            op_name: stdlib:make_dict
            deps: [source, external]
            params:
              direct: !ref source
              calc: !cel "source + external"
              amount: !decimal "2.50"
              pair: !tuple [!ref source, !decimal "3.75"]
              literal_ref: !literal
                $ref: not-a-marker
              poly: !icacheable
                type: invariant.types.Polynomial
                value:
                  coefficients: [1, 2, 0]
        """
    )

    assert isinstance(graph["source"], Node)
    assert isinstance(graph["payload"], Node)
    payload_params = graph["payload"].params
    _assert_ref(payload_params["direct"], "source")
    _assert_cel(payload_params["calc"], "source + external")
    assert payload_params["amount"] == Decimal("2.50")
    assert isinstance(payload_params["pair"], tuple)
    _assert_ref(payload_params["pair"][0], "source")
    assert payload_params["pair"][1] == Decimal("3.75")
    assert payload_params["literal_ref"] == {"$ref": "not-a-marker"}
    assert isinstance(payload_params["poly"], Polynomial)
    assert payload_params["poly"].coefficients == (1, 2)


def test_loads_graph_document_output():
    graph, output = load_graph_document_yaml(
        """
        format: invariant-graph
        version: 1
        output: result
        graph:
          result:
            kind: node
            op_name: stdlib:identity
            deps: []
            params:
              value: !decimal "12.34"
        """
    )

    assert output == "result"
    assert graph["result"].params["value"] == Decimal("12.34")


def test_loads_graph_document_without_output():
    graph, output = load_graph_document_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          result:
            kind: node
            op_name: stdlib:identity
            deps: []
            params:
              value: 1
        """
    )

    assert output is None
    assert set(graph) == {"result"}


def test_yaml_loaders_accept_bytes():
    graph = load_graph_yaml(
        b"""
        format: invariant-graph
        version: 1
        graph:
          result:
            kind: node
            op_name: stdlib:identity
            deps: []
            params: {}
        """
    )

    assert set(graph) == {"result"}


def test_loads_nested_subgraph_node():
    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          source:
            kind: node
            op_name: stdlib:identity
            deps: []
            params:
              value: 9
          outer:
            kind: subgraph
            deps: [source]
            params:
              inner_value: !ref source
            graph:
              inner:
                kind: node
                op_name: stdlib:identity
                deps: [inner_value]
                params:
                  value: !ref inner_value
            output: inner
        """
    )

    assert isinstance(graph["outer"], SubGraphNode)
    _assert_ref(graph["outer"].params["inner_value"], "source")
    inner = graph["outer"].graph["inner"]
    assert isinstance(inner, Node)
    _assert_ref(inner.params["value"], "inner_value")


def test_loads_switch_node_with_explicit_tags():
    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          plain_status:
            kind: node
            op_name: stdlib:identity
            deps: []
            params:
              value: plain
          wide_status:
            kind: node
            op_name: stdlib:identity
            deps: []
            params:
              value: wide
          status:
            kind: switch
            selector: !cel "art == null ? 'plain' : 'wide'"
            deps: [art]
            cases:
              "plain": plain_status
              "wide": wide_status
            default: plain_status
        """
    )

    assert isinstance(graph["status"], SwitchNode)
    _assert_cel(graph["status"].selector, "art == null ? 'plain' : 'wide'")
    assert graph["status"].cases == {
        "plain": "plain_status",
        "wide": "wide_status",
    }


def test_validation_failures_come_from_graph_validation():
    with pytest.raises(ValueError, match="undeclared dependency"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph:
              broken:
                kind: node
                op_name: stdlib:identity
                deps: []
                params:
                  value: !ref missing
            """
        )


def test_legacy_kind_inference_flag_is_forwarded():
    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          result:
            op_name: stdlib:identity
            deps: []
            params:
              value: 1
        """,
        legacy_kind_inference=True,
    )

    assert isinstance(graph["result"], Node)


def test_missing_pyyaml_raises_runtime_error(monkeypatch: pytest.MonkeyPatch):
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "yaml":
            raise ImportError("missing yaml")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(RuntimeError, match=r"pip install invariant-core\[yaml\]"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph: {}
            """
        )

    with pytest.raises(RuntimeError, match=r"pip install invariant-core\[yaml\]"):
        load_graph_document_yaml(
            """
            format: invariant-graph
            version: 1
            graph: {}
            """
        )
