"""Importable operations used by process scheduler tests."""

from decimal import Decimal


def add_one(value: int) -> int:
    """Add one to an integer."""
    return value + 1


def add_decimal(value: Decimal) -> Decimal:
    """Add a decimal amount."""
    return value + Decimal("1.25")
