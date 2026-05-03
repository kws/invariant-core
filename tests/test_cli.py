"""Tests for the Invariant graph execution CLI."""

import json
import subprocess
import sys
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from invariant import Node, dump_graph_output_to_dict, dump_graph_to_dict, ref
from invariant.types import Polynomial

PROJECT_ROOT = Path(__file__).parent.parent


def _write_json(path: Path, obj: object) -> None:
    path.write_text(json.dumps(obj), encoding="utf-8")


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


def test_module_invocation_emits_full_result_object(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path))

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "payload": {"items": [5, 8], "sum": 5},
        "sum": 5,
    }


def test_script_invocation_emits_full_result_object(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_script(str(graph_path))

    assert result.returncode == 0
    assert result.stderr == ""
    assert json.loads(result.stdout) == {
        "payload": {"items": [5, 8], "sum": 5},
        "sum": 5,
    }


def test_stdin_input_works_when_graph_argument_is_omitted():
    graph_json = json.dumps(dump_graph_to_dict(_simple_graph()))

    result = _run_module(input_text=graph_json)

    assert result.returncode == 0
    assert json.loads(result.stdout)["sum"] == 5


def test_stdin_input_works_with_dash_argument():
    graph_json = json.dumps(dump_graph_to_dict(_simple_graph()))

    result = _run_module("-", input_text=graph_json)

    assert result.returncode == 0
    assert json.loads(result.stdout)["sum"] == 5


def test_pick_emits_selected_value(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path), "--pick", "sum")

    assert result.returncode == 0
    assert result.stdout == "5\n"
    assert result.stderr == ""


def test_graph_output_wrapper_defaults_to_output_key(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_output_to_dict(_simple_graph(), "sum"))

    result = _run_module(str(graph_path))

    assert result.returncode == 0
    assert result.stdout == "5\n"


def test_graph_output_wrapper_all_emits_full_context(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_output_to_dict(_simple_graph(), "sum"))

    result = _run_module(str(graph_path), "--all")

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "payload": {"items": [5, 8], "sum": 5},
        "sum": 5,
    }


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

    result = _run_module(str(graph_path), "--context", str(context_path))

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "external": {"$tuple": [1, {"$decimal": "2.50"}]},
        "out": {"$tuple": [1, {"$decimal": "2.50"}]},
    }


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

    result = _run_module(str(graph_path), "--param", "text=My Button")

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "out": "My Button",
        "text": "My Button",
    }


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
    )

    assert result.returncode == 0
    assert json.loads(result.stdout) == {
        "color": "#FF0000",
        "color_out": "#FF0000",
        "payload": {"$tuple": [1, {"$decimal": "2.50"}]},
        "payload_out": {"$tuple": [1, {"$decimal": "2.50"}]},
    }


def test_missing_external_dependency_is_null_for_coalesce_defaults(tmp_path: Path):
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

    result = _run_module(str(graph_path))

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


def test_output_file_auto_writes_full_context_as_json(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    output_path = tmp_path / "results.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path), "--output", str(output_path))

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


def test_missing_pick_returns_nonzero_with_concise_error(tmp_path: Path):
    graph_path = tmp_path / "graph.json"
    _write_json(graph_path, dump_graph_to_dict(_simple_graph()))

    result = _run_module(str(graph_path), "--pick", "missing")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "invariant: error:" in result.stderr
    assert "Key 'missing' not found" in result.stderr


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
