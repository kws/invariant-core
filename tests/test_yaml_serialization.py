"""Tests for the optional YAML graph authoring format."""

import builtins
import json
import sys
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from invariant import Node, SubGraphNode, SwitchNode, cel, dump_graph_to_dict, ref
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


class _FakeResourceRegistry:
    def __init__(self, resources: dict[str, Any]):
        self.resources = resources

    def get_resource(self, name: str) -> Any:
        try:
            resource = self.resources[name]
        except KeyError as exc:
            raise KeyError(name) from exc
        if isinstance(resource, Exception):
            raise resource
        return resource


def _resource(text: str, content_type: str | None) -> Any:
    return SimpleNamespace(
        text=text,
        data=text.encode("utf-8"),
        content_type=content_type,
        encoding="utf-8",
    )


def _install_fake_resources(
    monkeypatch: pytest.MonkeyPatch, resources: dict[str, Any]
) -> None:
    module = SimpleNamespace(
        get_default_registry=lambda: _FakeResourceRegistry(resources)
    )
    monkeypatch.setitem(sys.modules, "justmyresource", module)


def _component_document(output: str | None = "out") -> dict[str, Any]:
    graph = {
        "out": Node(
            op_name="stdlib:identity",
            params={"value": ref("value")},
            deps=["value"],
        ),
        "alt": Node(
            op_name="stdlib:identity",
            params={"value": "alternate"},
            deps=[],
        ),
    }
    return dump_graph_to_dict(graph, output=output)


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


def test_subgraph_tag_grafts_json_resource_with_default_output(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:identity": _resource(
                json.dumps(_component_document(output="out")),
                "application/vnd.invariant.graph+json",
            )
        },
    )

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
              value: parent
          component: !subgraph
            resource: components:identity
            deps: [source]
            params:
              value: !ref source
        """
    )

    assert isinstance(graph["component"], SubGraphNode)
    assert graph["component"].output == "out"
    _assert_ref(graph["component"].params["value"], "source")
    inner = graph["component"].graph["out"]
    assert isinstance(inner, Node)
    _assert_ref(inner.params["value"], "value")

    canonical = json.dumps(dump_graph_to_dict(graph, output="component"))
    assert "components:identity" not in canonical
    assert "!subgraph" not in canonical


def test_subgraph_tag_explicit_output_overrides_resource_default(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:identity": _resource(
                json.dumps(_component_document(output="out")),
                "application/json",
            )
        },
    )

    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          component: !subgraph
            resource: components:identity
            deps: []
            params: {}
            output: alt
        """
    )

    assert isinstance(graph["component"], SubGraphNode)
    assert graph["component"].output == "alt"


def test_subgraph_tag_explicit_output_allows_resource_without_default(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:identity": _resource(
                json.dumps(_component_document(output=None)),
                "application/json",
            )
        },
    )

    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          component: !subgraph
            resource: components:identity
            deps: []
            params: {}
            output: out
        """
    )

    assert isinstance(graph["component"], SubGraphNode)
    assert graph["component"].output == "out"


def test_subgraph_tag_loads_yaml_resource_by_content_type(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:yaml": _resource(
                """
                format: invariant-graph
                version: 1
                output: out
                graph:
                  out:
                    kind: node
                    op_name: stdlib:identity
                    deps: [value]
                    params:
                      value: !ref value
                """,
                "application/vnd.invariant.graph+yaml",
            )
        },
    )

    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          component: !subgraph
            resource: components:yaml
            deps: [source]
            params:
              value: !ref source
        """
    )

    assert isinstance(graph["component"], SubGraphNode)
    _assert_ref(graph["component"].graph["out"].params["value"], "value")


def test_subgraph_tag_loads_yaml_resource_by_suffix(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components/badge.yml": _resource(
                """
                format: invariant-graph
                version: 1
                output: out
                graph:
                  out:
                    kind: node
                    op_name: stdlib:identity
                    deps: []
                    params:
                      value: suffix
                """,
                "text/plain",
            )
        },
    )

    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          component: !subgraph
            resource: components/badge.yml
            deps: []
            params: {}
        """
    )

    assert isinstance(graph["component"], SubGraphNode)
    assert graph["component"].output == "out"


def test_subgraph_tag_loads_nested_resource_subgraphs(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:inner": _resource(
                json.dumps(_component_document(output="out")),
                "application/json",
            ),
            "components:outer": _resource(
                """
                format: invariant-graph
                version: 1
                output: nested
                graph:
                  nested: !subgraph
                    resource: components:inner
                    deps: [value]
                    params:
                      value: !ref value
                """,
                "application/yaml",
            ),
        },
    )

    graph = load_graph_yaml(
        """
        format: invariant-graph
        version: 1
        graph:
          component: !subgraph
            resource: components:outer
            deps: [source]
            params:
              value: !ref source
        """
    )

    outer = graph["component"]
    assert isinstance(outer, SubGraphNode)
    nested = outer.graph["nested"]
    assert isinstance(nested, SubGraphNode)
    assert nested.output == "out"


def test_subgraph_tag_detects_resource_cycles(monkeypatch: pytest.MonkeyPatch):
    _install_fake_resources(
        monkeypatch,
        {
            "components:a": _resource(
                """
                format: invariant-graph
                version: 1
                output: child
                graph:
                  child: !subgraph
                    resource: components:b
                    deps: []
                    params: {}
                """,
                "application/yaml",
            ),
            "components:b": _resource(
                """
                format: invariant-graph
                version: 1
                output: child
                graph:
                  child: !subgraph
                    resource: components:a
                    deps: []
                    params: {}
                """,
                "application/yaml",
            ),
        },
    )

    with pytest.raises(ValueError, match="include cycle"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph:
              component: !subgraph
                resource: components:a
                deps: []
                params: {}
            """
        )


def test_subgraph_tag_requires_output_when_resource_has_no_default(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:identity": _resource(
                json.dumps(_component_document(output=None)),
                "application/json",
            )
        },
    )

    with pytest.raises(ValueError, match="has no default output"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph:
              component: !subgraph
                resource: components:identity
                deps: []
                params: {}
            """
        )


def test_subgraph_tag_rejects_unknown_resource_content_type(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:identity": _resource(
                json.dumps(_component_document(output="out")),
                "text/plain",
            )
        },
    )

    with pytest.raises(ValueError, match="unsupported content type"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph:
              component: !subgraph
                resource: components:identity
                deps: []
                params: {}
            """
        )


def test_subgraph_tag_reports_resource_lookup_errors(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(monkeypatch, {})

    with pytest.raises(ValueError, match="could not be resolved"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph:
              component: !subgraph
                resource: components:missing
                deps: []
                params: {}
            """
        )


def test_subgraph_tag_validation_failures_come_from_graph_validation(
    monkeypatch: pytest.MonkeyPatch,
):
    _install_fake_resources(
        monkeypatch,
        {
            "components:identity": _resource(
                json.dumps(_component_document(output="out")),
                "application/json",
            )
        },
    )

    with pytest.raises(ValueError, match="undeclared dependency"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph:
              component: !subgraph
                resource: components:identity
                deps: []
                params:
                  value: !ref missing
            """
        )


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


def test_missing_justmyresource_raises_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
):
    real_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "justmyresource":
            raise ImportError("missing justmyresource")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delitem(sys.modules, "justmyresource", raising=False)

    with pytest.raises(RuntimeError, match=r"pip install invariant-core\[resources\]"):
        load_graph_yaml(
            """
            format: invariant-graph
            version: 1
            graph:
              component: !subgraph
                resource: components:identity
                deps: []
                params: {}
            """
        )
