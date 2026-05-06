"""Tests for the Invariant graph execution CLI."""

import json
import subprocess
import sys
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from invariant import (
    Node,
    SwitchNode,
    dump_graph_to_dict,
    ref,
)
from invariant.types import Polynomial

PROJECT_ROOT = Path(__file__).parent.parent


def _write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def _run_module(
    *args: str, input_text: str | None = None
) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "invariant", *args],
        input=input_text,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )


def _run_script(*args: str) -> subprocess.CompletedProcess:
    script_name = "invariant.exe" if sys.platform == "win32" else "invariant"
    script = Path(sys.executable).with_name(script_name)
    return subprocess.run(
        [str(script), *args],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
        check=False,
    )


def _simple_graph() -> dict:
    return {
        "sum": Node(
            op_name="stdlib:add",
            params={"a": 2, "b": 3},
            deps=[],
        ),
        "payload": Node(
            op_name="stdlib:make_dict",
            params={"items": [ref("sum"), 8], "sum": ref("sum")},
            deps=["sum"],
        ),
    }


def test_plain_graph_requires_pick(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Graph document has no default output" in result.stderr


def test_script_invocation_uses_pick(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_script(str(graph_path), "--pick", "payload")

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {"items": [5, 8], "sum": 5}


def test_stdin_input_works_when_graph_argument_is_omitted():
    graph_json = json.dumps(dump_graph_to_dict(_simple_graph()))

    result = _run_module("--pick", "sum", input_text=graph_json)

    assert result.returncode == 0
    assert result.stdout == "5\n"


def test_stdin_input_works_with_dash_argument():
    graph_json = json.dumps(dump_graph_to_dict(_simple_graph()))

    result = _run_module("-", "--pick", "sum", input_text=graph_json)

    assert result.returncode == 0
    assert result.stdout == "5\n"


def test_pick_emits_selected_value(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path), "--pick", "sum")

    assert result.returncode == 0
    assert result.stdout == "5\n"
    assert result.stderr == ""


def test_graph_document_default_output_is_used(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph(), output="sum"))

    result = _run_module(str(graph_path))

    assert result.returncode == 0
    assert result.stdout == "5\n"


def test_yaml_graph_document_default_output_is_used(tmp_path: Path):
    graph_path = tmp_path / "graph.yaml"
    _write_text(
        graph_path,
        """
        output: sum
        format: invariant-graph
        version: 1
        graph:
          sum:
            kind: node
            op_name: stdlib:add
            deps: []
            params:
              a: 2
              b: 3
        """,
    )

    result = _run_module(str(graph_path))

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == "5\n"


def test_old_nested_graph_with_output_shape_is_rejected(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(
        graph_path,
        {"graph": dump_graph_to_dict(_simple_graph()), "output": "sum"},
    )

    result = _run_module(str(graph_path))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Document format must be 'invariant-graph'" in result.stderr


def test_yml_graph_file_auto_detects_yaml(tmp_path: Path):
    graph_path = tmp_path / "graph.yml"
    _write_text(
        graph_path,
        """
        format: invariant-graph
        version: 1
        graph:
          sum:
            kind: node
            op_name: stdlib:add
            deps: []
            params:
              a: 2
              b: 3
        """,
    )

    result = _run_module(str(graph_path), "--pick", "sum")

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == "5\n"


def test_stdin_yaml_requires_input_format_flag():
    graph_yaml = """
    format: invariant-graph
    version: 1
    graph:
      sum:
        kind: node
        op_name: stdlib:add
        deps: []
        params:
          a: 2
          b: 3
    """

    result = _run_module(
        "--input-format", "yaml", "--pick", "sum", input_text=graph_yaml
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == "5\n"


def test_repeatable_pick_emits_requested_outputs(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph(), output="sum"))

    result = _run_module(str(graph_path), "--pick", "sum", "--pick", "payload")

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "payload": {"items": [5, 8], "sum": 5},
        "sum": 5,
    }


def test_graph_document_default_output_prunes_inactive_switch_branch(tmp_path: Path):
    graph = {
        "left": Node(op_name="stdlib:identity", params={"value": "left"}, deps=[]),
        "right": Node(
            op_name="missing_op",
            params={"value": ref("missing_context")},
            deps=["missing_context"],
        ),
        "out": SwitchNode(
            selector=ref("choice"),
            deps=["choice"],
            cases={"left": "left", "right": "right"},
        ),
    }
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(graph, output="out"))

    result = _run_module(str(graph_path), "--param", "choice=left")

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout == '"left"\n'


def test_context_file_supports_external_dependencies(tmp_path: Path):
    graph = {
        "out": Node(
            op_name="stdlib:identity",
            params={"value": ref("external")},
            deps=["external"],
        )
    }
    graph_path = tmp_path / "graph.json"
    context_path = tmp_path / "context.json"
    _write_json(graph_path, dump_graph_to_dict(graph))
    _write_json(context_path, {"external": {"$tuple": [1, {"$decimal": "2.50"}]}})

    result = _run_module(
        str(graph_path),
        "--context",
        str(context_path),
        "--pick",
        "out",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {"$tuple": [1, {"$decimal": "2.50"}]}


def test_param_supplies_external_dependency(tmp_path: Path):
    graph = {
        "out": Node(
            op_name="stdlib:identity",
            params={"value": ref("text")},
            deps=["text"],
        )
    }
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(graph))

    result = _run_module(str(graph_path), "--param", "text=My Button", "--pick", "out")

    assert result.returncode == 0
    assert json.loads(result.stdout) == "My Button"


def test_param_overrides_context_file_value(tmp_path: Path):
    graph = {
        "out": Node(
            op_name="stdlib:identity",
            params={"value": ref("width")},
            deps=["width"],
        )
    }
    graph_path = tmp_path / "graph.json"
    context_path = tmp_path / "context.json"
    _write_json(graph_path, dump_graph_to_dict(graph))
    _write_json(context_path, {"width": 72})

    result = _run_module(
        str(graph_path),
        "--context",
        str(context_path),
        "--param",
        "width=144",
        "--pick",
        "out",
    )

    assert result.returncode == 0
    assert result.stdout == "144\n"


def test_param_parses_invariant_markers_and_bare_strings(tmp_path: Path):
    graph = {
        "payload_out": Node(
            op_name="stdlib:identity",
            params={"value": ref("payload")},
            deps=["payload"],
        ),
        "color_out": Node(
            op_name="stdlib:identity",
            params={"value": ref("color")},
            deps=["color"],
        ),
    }
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(graph))

    result = _run_module(
        str(graph_path),
        "--param",
        'payload={"$tuple":[1,{"$decimal":"2.50"}]}',
        "--param",
        "color=#FF0000",
        "--pick",
        "payload_out",
        "--pick",
        "color_out",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "color_out": "#FF0000",
        "payload_out": {"$tuple": [1, {"$decimal": "2.50"}]},
    }


def test_missing_external_dependency_fails(tmp_path: Path):
    graph = {
        "out": Node(
            op_name="stdlib:coalesce",
            params={"values": [ref("override"), "default"]},
            deps=["override"],
        )
    }
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(graph))

    result = _run_module(
        str(graph_path),
        "--pick",
        "out",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "override" in result.stderr


def test_explicit_null_param_supports_coalesce_defaults(tmp_path: Path):
    graph = {
        "out": Node(
            op_name="stdlib:coalesce",
            params={"values": [ref("override"), "default"]},
            deps=["override"],
        )
    }
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(graph))

    result = _run_module(
        str(graph_path),
        "--pick",
        "out",
        "--param",
        "override=null",
    )

    assert result.returncode == 0
    assert result.stdout == '"default"\n'


def test_non_json_native_results_use_invariant_json_encoding(tmp_path: Path):
    graph = {
        "decimal": Node(
            op_name="stdlib:identity",
            params={"value": Decimal("12.34")},
            deps=[],
        ),
        "tuple": Node(
            op_name="stdlib:identity",
            params={"value": (1, Decimal("2.50"))},
            deps=[],
        ),
        "poly": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [1, 2, 0]},
            deps=[],
        ),
    }
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(graph))

    result = _run_module(
        str(graph_path),
        "--pick",
        "decimal",
        "--pick",
        "tuple",
        "--pick",
        "poly",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "decimal": {"$decimal": "12.34"},
        "poly": {
            "$icacheable": {
                "type": "invariant.types.Polynomial",
                "value": {"coefficients": [1, 2]},
            }
        },
        "tuple": {"$tuple": [1, {"$decimal": "2.50"}]},
    }


def test_output_file_auto_writes_selected_icacheable_as_binary(tmp_path: Path):
    graph = {
        "poly": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [1, 2, 0]},
            deps=[],
        )
    }
    graph_path = tmp_path / "graph.json"
    output_path = tmp_path / "poly.bin"
    _write_json(graph_path, dump_graph_to_dict(graph))

    result = _run_module(
        str(graph_path), "--pick", "poly", "--output", str(output_path)
    )

    expected = BytesIO()
    Polynomial([1, 2]).to_stream(expected)
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert output_path.read_bytes() == expected.getvalue()


def test_output_file_can_force_json_for_selected_icacheable(tmp_path: Path):
    graph = {
        "poly": Node(
            op_name="poly:from_coefficients",
            params={"coefficients": [1, 2, 0]},
            deps=[],
        )
    }
    graph_path = tmp_path / "graph.json"
    output_path = tmp_path / "poly.json"
    _write_json(graph_path, dump_graph_to_dict(graph))

    result = _run_module(
        str(graph_path),
        "--pick",
        "poly",
        "--output",
        str(output_path),
        "--output-format",
        "json",
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "$icacheable": {
            "type": "invariant.types.Polynomial",
            "value": {"coefficients": [1, 2]},
        }
    }


def test_output_file_auto_writes_multiple_outputs_as_json(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    output_path = tmp_path / "results.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(
        str(graph_path),
        "--pick",
        "sum",
        "--pick",
        "payload",
        "--output",
        str(output_path),
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert json.loads(output_path.read_text(encoding="utf-8")) == {
        "payload": {"items": [5, 8], "sum": 5},
        "sum": 5,
    }


def test_binary_output_format_requires_selected_icacheable(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    output_path = tmp_path / "sum.bin"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(
        str(graph_path),
        "--pick",
        "sum",
        "--output",
        str(output_path),
        "--output-format",
        "binary",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "invariant: error:" in result.stderr
    assert "not an ICacheable value" in result.stderr
    assert not output_path.exists()


def test_binary_output_format_rejects_multiple_outputs(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    output_path = tmp_path / "results.bin"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(
        str(graph_path),
        "--pick",
        "sum",
        "--pick",
        "payload",
        "--output",
        str(output_path),
        "--output-format",
        "binary",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "requires exactly one selected output" in result.stderr
    assert not output_path.exists()


def test_all_flag_is_not_supported(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph(), output="sum"))

    result = _run_module(str(graph_path), "--all")

    assert result.returncode == 2
    assert "unrecognized arguments: --all" in result.stderr


def test_missing_pick_returns_nonzero_with_concise_error(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path), "--pick", "missing")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "invariant: error:" in result.stderr
    assert "Output node 'missing' is not in graph" in result.stderr


def test_invalid_input_returns_nonzero_with_concise_error(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    graph_path.write_text("{not json", encoding="utf-8")

    result = _run_module(str(graph_path))

    assert result.returncode == 1
    assert result.stdout == ""
    assert "invariant: error:" in result.stderr


def test_invalid_param_returns_nonzero_with_concise_error(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path), "--param", "missing-equals")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "invariant: error:" in result.stderr
    assert "--param must be in KEY=VALUE form" in result.stderr
