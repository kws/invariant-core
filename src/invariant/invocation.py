"""Shared operation invocation helpers."""

import inspect
from collections.abc import Callable
from typing import Any

from invariant.cacheable import is_cacheable


def invoke_op(op: Callable[..., Any], op_name: str, manifest: dict[str, Any]) -> Any:
    """Invoke an operation with kwargs dispatch and return validation.

    Args:
        op: The callable operation to invoke.
        op_name: The name of the operation (for error messages).
        manifest: The manifest dictionary mapping parameter names to values.

    Returns:
        The operation result (native type or ICacheable domain type).

    Raises:
        ValueError: If required parameters are missing.
        TypeError: If return value is not cacheable.
    """
    sig = inspect.signature(op)
    kwargs: dict[str, Any] = {}

    for name, param in sig.parameters.items():
        if name in manifest:
            kwargs[name] = manifest[name]
        elif (
            param.default is not inspect.Parameter.empty
            or param.kind == inspect.Parameter.VAR_KEYWORD
        ):
            pass
        else:
            raise ValueError(f"Op '{op_name}': missing required parameter '{name}'")

    has_var_kwargs = any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    if has_var_kwargs:
        for key, val in manifest.items():
            if key not in kwargs:
                kwargs[key] = val

    result = op(**kwargs)

    if not is_cacheable(result):
        raise TypeError(
            f"Op '{op_name}' returned {type(result).__name__}, "
            f"which is not a cacheable type"
        )

    return result


__all__ = ["invoke_op"]
