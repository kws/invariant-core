"""Shared operation invocation helpers."""

import inspect
from collections.abc import Callable
from threading import RLock
from typing import Any
from weakref import WeakKeyDictionary

from invariant.cacheable import is_cacheable

_SIGNATURES = WeakKeyDictionary()
_SIGNATURE_LOCK = RLock()


def _signature(op):
    # Weak keys never retain closures and their resource payloads between jobs.
    try:
        with _SIGNATURE_LOCK:
            signature = _SIGNATURES.get(op)
            if signature is None:
                signature = inspect.signature(op)
                if len(_SIGNATURES) >= 512:
                    _SIGNATURES.pop(next(iter(_SIGNATURES)))
                _SIGNATURES[op] = signature
            return signature
    except TypeError:
        return inspect.signature(op)


def invocation_kwargs(op, op_name, manifest):
    sig = _signature(op)
    kwargs = {}
    for name, param in sig.parameters.items():
        if name in manifest:
            kwargs[name] = manifest[name]
        elif param.default is inspect.Parameter.empty and param.kind not in {
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.VAR_POSITIONAL,
        }:
            raise ValueError(f"Op '{op_name}': missing required parameter '{name}'")
    if any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
        kwargs.update(
            {key: value for key, value in manifest.items() if key not in kwargs}
        )
    return kwargs


def validate_result(result, op_name):
    if not is_cacheable(result):
        raise TypeError(
            f"Op '{op_name}' returned {type(result).__name__}, which is not a cacheable type"
        )
    return result


async def invoke_op_async(op, op_name, manifest):
    result = op(**invocation_kwargs(op, op_name, manifest))
    if inspect.isawaitable(result):
        result = await result
    return validate_result(result, op_name)


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
    result = op(**invocation_kwargs(op, op_name, manifest))
    if inspect.iscoroutine(result):
        result.close()
        raise TypeError(
            f"Op '{op_name}' is asynchronous; use AsyncExecutor with InlineScheduler"
        )
    return validate_result(result, op_name)


__all__ = ["invoke_op", "invoke_op_async"]
