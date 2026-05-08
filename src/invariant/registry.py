"""OpRegistry for mapping operation names to callables."""

import importlib
import types
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any

from invariant.traits import TraitLike, decorated_traits, normalize_traits

# Type alias for op packages: dict mapping short names to op callables
OpPackage = dict[str, Callable[..., Any]]


@dataclass(frozen=True)
class OpBinding:
    """Registered operation plus scheduler metadata."""

    name: str
    op: Callable[..., Any]
    traits: frozenset[str]
    implementation_ref: str | None = None


def import_implementation_ref(ref: str) -> Callable[..., Any]:
    """Import a callable from ``module.path:qualname``."""
    if ":" not in ref:
        raise ValueError(
            "implementation_ref must use 'module.path:qualname' format"
        )

    module_name, qualname = ref.split(":", 1)
    if not module_name or not qualname:
        raise ValueError(
            "implementation_ref must use 'module.path:qualname' format"
        )

    obj: Any = importlib.import_module(module_name)
    for part in qualname.split("."):
        if not part:
            raise ValueError(f"Invalid implementation_ref {ref!r}")
        obj = getattr(obj, part)

    if not callable(obj):
        raise TypeError(f"implementation_ref {ref!r} does not resolve to a callable")
    return obj


def infer_implementation_ref(op: Callable[..., Any]) -> str | None:
    """Infer an importable implementation ref for a top-level callable.

    Local functions, lambdas, ``__main__`` callables, and dynamically replaced
    attributes return ``None``. Process and remote schedulers require an exact
    worker-resolvable reference and should reject missing refs.
    """
    module_name = getattr(op, "__module__", None)
    qualname = getattr(op, "__qualname__", None)
    if (
        not module_name
        or not qualname
        or module_name == "__main__"
        or "<locals>" in qualname
        or qualname == "<lambda>"
    ):
        return None

    ref = f"{module_name}:{qualname}"
    try:
        imported = import_implementation_ref(ref)
    except Exception:
        return None
    if imported is not op:
        return None
    return ref


class OpRegistry:
    """Singleton registry mapping string identifiers to executable Python callables.

    Decouples the "string" name in the graph definition from the actual Python code.
    """

    _instance: "OpRegistry | None" = None
    _initialized: bool = False

    def __new__(cls) -> "OpRegistry":
        """Ensure singleton pattern."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        """Initialize the registry (only once)."""
        if not OpRegistry._initialized:
            self._bindings: dict[str, OpBinding] = {}
            OpRegistry._initialized = True

    def register(
        self,
        name: str,
        op: Callable[..., Any],
        *,
        traits: Iterable[TraitLike] | None = None,
        implementation_ref: str | None = None,
    ) -> None:
        """Register an operation.

        Args:
            name: The string identifier for the operation.
            op: The callable that implements the operation.
                Should be a plain Python function with typed parameters.
            traits: Optional execution traits for scheduler routing.
            implementation_ref: Optional worker-resolvable implementation
                reference in ``module.path:qualname`` form.

        Raises:
            ValueError: If name is empty or already registered.
        """
        if not name:
            raise ValueError("Operation name cannot be empty")
        if name in self._bindings:
            raise ValueError(f"Operation '{name}' is already registered")

        if implementation_ref is not None:
            import_implementation_ref(implementation_ref)
        else:
            implementation_ref = infer_implementation_ref(op)

        merged_traits = decorated_traits(op) | normalize_traits(traits)
        self._bindings[name] = OpBinding(
            name=name,
            op=op,
            traits=merged_traits,
            implementation_ref=implementation_ref,
        )

    def get(self, name: str) -> Callable[..., Any]:
        """Get an operation by name.

        Args:
            name: The string identifier for the operation.

        Returns:
            The callable that implements the operation.

        Raises:
            KeyError: If operation is not registered.
        """
        if name not in self._bindings:
            raise KeyError(f"Operation '{name}' is not registered")
        return self._bindings[name].op

    def get_binding(self, name: str) -> OpBinding:
        """Get the full operation binding by name."""
        if name not in self._bindings:
            raise KeyError(f"Operation '{name}' is not registered")
        return self._bindings[name]

    def traits(self, name: str) -> frozenset[str]:
        """Get normalized execution traits for an operation."""
        return self.get_binding(name).traits

    def implementation_ref(self, name: str) -> str | None:
        """Get the worker-resolvable implementation reference for an operation."""
        return self.get_binding(name).implementation_ref

    def has(self, name: str) -> bool:
        """Check if an operation is registered.

        Args:
            name: The string identifier for the operation.

        Returns:
            True if registered, False otherwise.
        """
        return name in self._bindings

    def clear(self) -> None:
        """Clear all registered operations (mainly for testing)."""
        self._bindings.clear()

    def register_package(self, prefix: str, ops: OpPackage | Any) -> None:
        """Register all ops from a package under a common prefix.

        Args:
            prefix: The namespace prefix (e.g. "poly").
            ops: Either a dict mapping short names to callables (OpPackage),
                 or a Python module that has an OPS dict attribute.

        Raises:
            ValueError: If prefix is empty, ops is invalid, or any operation
                name is already registered.
            AttributeError: If ops is a module but doesn't have an OPS attribute.
        """
        if not prefix:
            raise ValueError("Package prefix cannot be empty")

        # Extract the ops dict from the input
        ops_dict: OpPackage
        if isinstance(ops, dict):
            ops_dict = ops
        elif isinstance(ops, types.ModuleType):
            # It's a module - check for OPS attribute
            if not hasattr(ops, "OPS"):
                raise AttributeError(
                    f"Module {ops.__name__} does not have an OPS attribute"
                )
            ops_dict = ops.OPS
            if not isinstance(ops_dict, dict):
                raise ValueError(f"OPS attribute must be a dict, got {type(ops_dict)}")
        elif hasattr(ops, "OPS"):
            # Object with OPS attribute (not a module)
            ops_dict = ops.OPS
            if not isinstance(ops_dict, dict):
                raise ValueError(f"OPS attribute must be a dict, got {type(ops_dict)}")
        else:
            raise ValueError(
                f"ops must be a dict or module with OPS attribute, got {type(ops)}"
            )

        # Register each op with the prefix
        for name, op in ops_dict.items():
            full_name = f"{prefix}:{name}"
            self.register(full_name, op)

    def auto_discover(self) -> None:
        """Discover and register op packages from entry points.

        Scans the 'invariant.ops' entry point group. Each entry point
        should resolve to either:
          - A dict[str, Callable] (the OPS dict directly)
          - A callable that returns such a dict

        The entry point name becomes the package prefix.

        Raises:
            ValueError: If any operation name is already registered
                (via register_package).
        """
        eps = entry_points(group="invariant.ops")

        for ep in eps:
            try:
                # Load the entry point
                loaded = ep.load()

                # Extract the ops dict
                ops_dict: OpPackage
                if isinstance(loaded, dict):
                    ops_dict = loaded
                elif callable(loaded):
                    # Callable that returns the dict
                    result = loaded()
                    if not isinstance(result, dict):
                        continue  # Skip invalid entry points
                    ops_dict = result
                else:
                    continue  # Skip invalid entry points

                # Register the package using the entry point name as prefix
                self.register_package(ep.name, ops_dict)
            except Exception:
                # Skip invalid entry points silently
                continue
