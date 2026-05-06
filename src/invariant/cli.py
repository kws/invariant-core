"""Command-line interface for executing serialized Invariant graphs."""

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from invariant.executor import Executor
from invariant.graph import Graph
from invariant.graph_serialization import (
    FORMAT_ID,
    dump_value_to_jsonable,
    load_graph_from_dict,
    load_graph_output_from_dict,
    load_value_from_jsonable,
)
from invariant.protocol import ICacheable
from invariant.registry import OpRegistry
from invariant.store.null import NullStore
from invariant.yaml_serialization import _load_yaml_document


@dataclass(frozen=True)
class _CliOutput:
    value: Any
    is_context: bool
    selected_key: str | None


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="invariant",
        description="Execute a serialized Invariant graph and emit JSON results.",
    )
    parser.add_argument(
        "graph",
        nargs="?",
        default="-",
        help="Path to graph JSON or YAML. Reads stdin when omitted or '-'.",
    )
    parser.add_argument(
        "--input-format",
        choices=["auto", "json", "yaml"],
        default="auto",
        help=(
            "Graph input format. auto detects .yaml/.yml files; stdin defaults "
            "to JSON."
        ),
    )
    parser.add_argument(
        "--context",
        metavar="CONTEXT_FILE",
        help="Path to a JSON object containing external context values.",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help=(
            "Override or add one external context value. VALUE accepts JSON "
            "scalars/objects, Invariant JSON markers, and bare strings. "
            "Missing external graph dependencies are supplied as null."
        ),
    )
    parser.add_argument(
        "--pick",
        metavar="KEY",
        help="Emit only one key from the execution result.",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Emit the full execution context.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Emit indented JSON.",
    )
    parser.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Write output to FILE instead of stdout.",
    )
    parser.add_argument(
        "--output-format",
        choices=["auto", "json", "binary"],
        default="auto",
        help=(
            "File output format. auto writes selected ICacheable values as binary "
            "and everything else as JSON."
        ),
    )
    return parser


def _read_graph_arg(graph_arg: str, stdin: TextIO) -> str:
    if graph_arg == "-":
        return stdin.read()
    return Path(graph_arg).read_text(encoding="utf-8")


def _detect_input_format(graph_arg: str, input_format: str) -> str:
    if input_format != "auto":
        return input_format
    if graph_arg == "-":
        return "json"
    suffix = Path(graph_arg).suffix.lower()
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    return "json"


def _load_input_document(
    data: str, *, graph_arg: str = "-", input_format: str = "auto"
) -> tuple[Graph, str | None]:
    detected_format = _detect_input_format(graph_arg, input_format)
    obj = (
        _load_yaml_document(data)
        if detected_format == "yaml"
        else json.loads(data)
    )
    if not isinstance(obj, dict):
        raise ValueError("Graph document must be an object")

    if obj.get("format") == FORMAT_ID:
        return load_graph_from_dict(obj), None

    return load_graph_output_from_dict(obj)


def _load_context(path: str | None) -> dict[str, Any]:
    if path is None:
        return {}

    obj = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(obj, dict):
        raise ValueError("Context document must be a JSON object")

    return {key: load_value_from_jsonable(value) for key, value in obj.items()}


def _parse_param_value(value: str) -> Any:
    try:
        return load_value_from_jsonable(json.loads(value))
    except json.JSONDecodeError:
        return value


def _parse_param(param: str) -> tuple[str, Any]:
    key, separator, value = param.partition("=")
    if separator == "" or not key:
        raise ValueError("--param must be in KEY=VALUE form")
    return key, _parse_param_value(value)


def _load_params(params: list[str]) -> dict[str, Any]:
    return dict(_parse_param(param) for param in params)


def _external_deps(graph: Graph) -> set[str]:
    graph_keys = set(graph)
    return {
        dep
        for vertex in graph.values()
        for dep in vertex.deps
        if dep not in graph_keys
    }


def _encode_result_context(results: dict[str, Any]) -> dict[str, Any]:
    return {key: dump_value_to_jsonable(value) for key, value in results.items()}


def _select_output(
    results: dict[str, Any],
    *,
    pick: str | None,
    wrapper_output: str | None,
    emit_all: bool,
) -> _CliOutput:
    if emit_all:
        return _CliOutput(results, is_context=True, selected_key=None)

    selected_key = pick if pick is not None else wrapper_output
    if selected_key is None:
        return _CliOutput(results, is_context=True, selected_key=None)

    if selected_key not in results:
        available = ", ".join(sorted(results))
        raise ValueError(f"Key '{selected_key}' not found. Available keys: {available}")

    return _CliOutput(
        results[selected_key], is_context=False, selected_key=selected_key
    )


def _execute_cli(args: argparse.Namespace, stdin: TextIO) -> _CliOutput:
    graph, wrapper_output = _load_input_document(
        _read_graph_arg(args.graph, stdin),
        graph_arg=args.graph,
        input_format=args.input_format,
    )
    context = _load_context(args.context)
    context.update(_load_params(args.param))
    for dep in _external_deps(graph):
        context.setdefault(dep, None)

    registry = OpRegistry()
    registry.clear()
    registry.auto_discover()

    executor = Executor(registry, NullStore())
    results = executor.execute(graph, context=context)
    return _select_output(
        results,
        pick=args.pick,
        wrapper_output=wrapper_output,
        emit_all=args.all,
    )


def _jsonable_output(output: _CliOutput) -> Any:
    if output.is_context:
        return _encode_result_context(output.value)
    return dump_value_to_jsonable(output.value)


def _write_json_output(
    output: _CliOutput, stream: TextIO, *, pretty: bool
) -> None:
    json.dump(
        _jsonable_output(output),
        stream,
        indent=2 if pretty else None,
        separators=None if pretty else (",", ":"),
        sort_keys=True,
    )
    stream.write("\n")


def _write_binary_output(output: _CliOutput, path: Path) -> None:
    if output.is_context:
        raise ValueError("Binary output requires a single selected result")

    value = output.value
    if not isinstance(value, ICacheable):
        selected = f" '{output.selected_key}'" if output.selected_key else ""
        raise ValueError(
            f"Output{selected} is {type(value).__name__}, not an ICacheable value"
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    to_file = getattr(value, "to_file", None)
    if callable(to_file):
        to_file(path)
        return

    with path.open("wb") as stream:
        value.to_stream(stream)


def _write_output_file(
    output: _CliOutput,
    *,
    path: Path,
    output_format: str,
    pretty: bool,
) -> None:
    if output_format == "binary" or (
        output_format == "auto"
        and not output.is_context
        and isinstance(output.value, ICacheable)
    ):
        _write_binary_output(output, path)
        return

    if output_format == "auto" or output_format == "json":
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as stream:
            _write_json_output(output, stream, pretty=pretty)
        return

    raise ValueError(f"Unsupported output format: {output_format}")


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        output = _execute_cli(args, sys.stdin)
        if args.output:
            _write_output_file(
                output,
                path=Path(args.output),
                output_format=args.output_format,
                pretty=args.pretty,
            )
        else:
            _write_json_output(output, sys.stdout, pretty=args.pretty)
    except Exception as e:
        print(f"invariant: error: {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
