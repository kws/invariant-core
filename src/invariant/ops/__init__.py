"""Standard operations library."""

from invariant.ops import poly, stdlib
from invariant.ops.stdlib import (
    add,
    coalesce,
    dict_get,
    identity,
    multiply,
)

__all__ = [
    "poly",
    "stdlib",
    "identity",
    "add",
    "multiply",
    "dict_get",
    "coalesce",
]
