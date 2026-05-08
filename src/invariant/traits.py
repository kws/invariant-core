"""Operation execution traits.

Traits are portable op metadata. Executors and schedulers may use them to route
work, but traits are not part of artifact identity.
"""

from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any


class OpTrait(str, Enum):
    """Built-in operation execution traits."""

    BLOCKING = "blocking"
    IO_BOUND = "io-bound"
    CPU_BOUND = "cpu-bound"
    THREAD_SAFE = "thread-safe"
    PROCESS_SAFE = "process-safe"


TraitLike = str | Enum

_OP_TRAITS_ATTR = "__invariant_op_traits__"


def normalize_trait(trait: TraitLike) -> str:
    """Normalize a trait enum or string to its wire/storage string."""
    value = trait.value if isinstance(trait, Enum) else trait
    if not isinstance(value, str):
        value = str(value)
    if not value:
        raise ValueError("Operation trait cannot be empty")
    return value


def normalize_traits(traits: Iterable[TraitLike] | None = None) -> frozenset[str]:
    """Normalize a trait iterable to a stable set of strings."""
    if traits is None:
        return frozenset()
    return frozenset(normalize_trait(trait) for trait in traits)


def op_traits(*traits: TraitLike) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Attach execution traits to an operation callable."""
    normalized = normalize_traits(traits)

    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        existing = normalize_traits(getattr(fn, _OP_TRAITS_ATTR, frozenset()))
        setattr(fn, _OP_TRAITS_ATTR, existing | normalized)
        return fn

    return decorate


def decorated_traits(op: Callable[..., Any]) -> frozenset[str]:
    """Return traits attached by ``@op_traits``."""
    return normalize_traits(getattr(op, _OP_TRAITS_ATTR, frozenset()))


__all__ = [
    "OpTrait",
    "TraitLike",
    "decorated_traits",
    "normalize_trait",
    "normalize_traits",
    "op_traits",
]
