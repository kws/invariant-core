"""Tests for lazy SwitchNode execution."""

import hashlib
from decimal import Decimal
from typing import BinaryIO

import pytest
from invariant import Node, SubGraphNode, SwitchNode, cel, ref
from invariant.executor import Executor


class SizedArtifact:
    """Minimal cacheable artifact with public dimensions for CEL selectors."""

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height

    def get_stable_hash(self) -> str:
        payload = f"{self.width}x{self.height}".encode("ascii")
        return hashlib.sha256(payload).hexdigest()

    def to_stream(self, stream: BinaryIO) -> None:
        stream.write(self.width.to_bytes(4, "big", signed=True))
        stream.write(self.height.to_bytes(4, "big", signed=True))

    @classmethod
    def from_stream(cls, stream: BinaryIO) -> "SizedArtifact":
        width = int.from_bytes(stream.read(4), "big", signed=True)
        height = int.from_bytes(stream.read(4), "big", signed=True)
        return cls(width, height)


def _switch_graph(selector=None):
    if selector is None:
        selector = ref("choice")
    return {
        "left": Node(op_name="identity", params={"value": "left"}, deps=[]),
        "right": Node(
            op_name="missing_op",
            params={"value": ref("missing_context")},
            deps=["missing_context"],
            cache=False,
        ),
        "out": SwitchNode(
            selector=selector,
            deps=["choice"],
            cases={"left": "left", "right": "right"},
        ),
    }


def test_execute_prunes_inactive_switch_branch(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)

    assert executor.execute(
        _switch_graph(),
        ["out"],
        context={"choice": "left"},
    ) == {"out": "left"}


def test_execute_selected_branch_missing_dependency_raises(registry, store):
    registry.register("identity", lambda value: value)
    registry.register("missing_op", lambda value: value)
    executor = Executor(registry, store)

    with pytest.raises(ValueError, match="missing_context"):
        executor.execute(_switch_graph(), ["out"], context={"choice": "right"})


def test_execute_selected_branch_missing_op_raises(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)

    with pytest.raises(ValueError, match="unregistered op"):
        executor.execute(
            {
                "left": Node(op_name="identity", params={"value": "left"}, deps=[]),
                "right": Node(op_name="missing_op", params={"value": "right"}, deps=[]),
                "out": SwitchNode(
                    selector=ref("choice"),
                    deps=["choice"],
                    cases={"left": "left", "right": "right"},
                ),
            },
            ["out"],
            context={"choice": "right"},
        )


def test_execute_unmatched_selector_without_default_raises(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)

    with pytest.raises(ValueError, match="no case"):
        executor.execute(_switch_graph(), ["out"], context={"choice": "missing"})


def test_execute_uses_default_target(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)

    graph = {
        "fallback": Node(op_name="identity", params={"value": "fallback"}, deps=[]),
        "out": SwitchNode(
            selector=ref("choice"),
            deps=["choice"],
            cases={"known": "fallback"},
            default="fallback",
        ),
    }

    assert executor.execute(graph, ["out"], context={"choice": "other"}) == {
        "out": "fallback"
    }


def test_active_switch_requires_all_targets_to_exist(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)
    graph = {
        "left": Node(op_name="identity", params={"value": "left"}, deps=[]),
        "out": SwitchNode(
            selector=ref("choice"),
            deps=["choice"],
            cases={"left": "left", "right": "missing_branch"},
        ),
    }

    with pytest.raises(ValueError, match="missing_branch"):
        executor.execute(graph, ["out"], context={"choice": "left"})


def test_unrequested_switch_target_is_not_validated(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)
    graph = {
        "used": Node(op_name="identity", params={"value": "used"}, deps=[]),
        "unused": SwitchNode(
            selector="missing",
            deps=[],
            cases={"missing": "missing_branch"},
        ),
    }

    assert executor.execute(graph, ["used"]) == {"used": "used"}


def test_execute_detects_active_switch_cycle(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)

    graph = {
        "out": SwitchNode(selector="loop", deps=[], cases={"loop": "loop"}),
        "loop": Node(op_name="identity", params={"value": ref("out")}, deps=["out"]),
    }

    with pytest.raises(ValueError, match="cycles on active path"):
        executor.execute(graph, ["out"])


def test_execute_multiple_outputs_shares_dependencies(registry, store):
    calls: list[str] = []

    def source() -> str:
        calls.append("source")
        return "source"

    def append(value: str, suffix: str) -> str:
        calls.append(suffix)
        return value + suffix

    registry.register("source", source)
    registry.register("append", append)
    executor = Executor(registry, store)
    graph = {
        "source": Node(op_name="source", params={}, deps=[]),
        "left": Node(
            op_name="append",
            params={"value": ref("source"), "suffix": "-left"},
            deps=["source"],
        ),
        "right": Node(
            op_name="append",
            params={"value": ref("source"), "suffix": "-right"},
            deps=["source"],
        ),
    }

    results = executor.execute(graph, ["left", "right"])

    assert results == {"left": "source-left", "right": "source-right"}
    assert calls == ["source", "-left", "-right"]


def test_unrequested_vertices_are_not_executed_or_validated(registry, store):
    calls: list[str] = []
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)
    graph = {
        "used": Node(op_name="identity", params={"value": "used"}, deps=[]),
        "unused_missing_op": Node(op_name="missing_op", params={}, deps=[]),
        "unused_missing_dep": Node(
            op_name="identity",
            params={"value": ref("missing")},
            deps=["missing"],
        ),
        "unused_cache_false": Node(
            op_name="identity",
            params={"value": "unused"},
            deps=[],
            cache=False,
        ),
    }

    assert executor.execute(graph, ["used"]) == {"used": "used"}
    assert calls == []


def test_explicitly_requested_inactive_branch_executes_as_output(registry, store):
    registry.register("identity", lambda value: value)
    registry.register("missing_op", lambda value: value)
    executor = Executor(registry, store)

    assert executor.execute(
        _switch_graph(),
        ["right"],
        context={"choice": "left", "missing_context": "right"},
    ) == {"right": "right"}


@pytest.mark.parametrize(
    ("selector", "deps", "context", "cases", "expected"),
    [
        (ref("flag"), ["flag"], {"flag": True}, {"true": "yes"}, "yes"),
        (ref("flag"), ["flag"], {"flag": False}, {"false": "no"}, "no"),
        (ref("value"), ["value"], {"value": None}, {"null": "none"}, "none"),
        (ref("n"), ["n"], {"n": 2}, {"2": "two"}, "two"),
        (
            ref("amount"),
            ["amount"],
            {"amount": Decimal("2.50")},
            {"2.50": "money"},
            "money",
        ),
        (
            cel("value == null ? 'missing' : 'present'"),
            ["value"],
            {"value": None},
            {"missing": "none"},
            "none",
        ),
    ],
)
def test_switch_selector_normalization(
    registry,
    store,
    selector,
    deps,
    context,
    cases,
    expected,
):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)
    graph = {
        "yes": Node(op_name="identity", params={"value": "yes"}, deps=[]),
        "no": Node(op_name="identity", params={"value": "no"}, deps=[]),
        "none": Node(op_name="identity", params={"value": "none"}, deps=[]),
        "two": Node(op_name="identity", params={"value": "two"}, deps=[]),
        "money": Node(op_name="identity", params={"value": "money"}, deps=[]),
        "out": SwitchNode(selector=selector, deps=deps, cases=cases),
    }

    assert executor.execute(graph, ["out"], context=context) == {"out": expected}


def test_switch_selector_rejects_composite_result(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)

    graph = {
        "target": Node(op_name="identity", params={"value": "target"}, deps=[]),
        "out": SwitchNode(
            selector=ref("value"),
            deps=["value"],
            cases={"target": "target"},
        ),
    }

    with pytest.raises(ValueError, match="unsupported list"):
        executor.execute(graph, ["out"], context={"value": ["target"]})


def test_switch_selector_can_use_artifact_dimensions(registry, store):
    registry.register("identity", lambda value: value)
    executor = Executor(registry, store)
    graph = {
        "wide": Node(op_name="identity", params={"value": "wide"}, deps=[]),
        "compact": Node(op_name="identity", params={"value": "compact"}, deps=[]),
        "out": SwitchNode(
            selector=cel(
                "image.width > 160 || image.height > 100 ? 'wide' : 'compact'"
            ),
            deps=["image"],
            cases={"wide": "wide", "compact": "compact"},
        ),
    }

    assert executor.execute(
        graph,
        ["out"],
        context={"image": SizedArtifact(width=200, height=40)},
    ) == {"out": "wide"}


def test_subgraph_output_uses_output_scoped_switch_execution(registry, store):
    registry.register("identity", lambda value: value)
    inner = {
        "left": Node(op_name="identity", params={"value": "inner-left"}, deps=[]),
        "right": Node(op_name="missing_op", params={"value": "inner-right"}, deps=[]),
        "out": SwitchNode(
            selector=ref("choice"),
            deps=["choice"],
            cases={"left": "left", "right": "right"},
        ),
    }
    graph = {
        "sub": SubGraphNode(
            params={"choice": ref("choice")},
            deps=["choice"],
            graph=inner,
            output="out",
        )
    }
    executor = Executor(registry, store)

    assert executor.execute(
        graph,
        ["sub"],
        context={"choice": "left"},
    ) == {"sub": "inner-left"}
