"""Standard operations library for basic data manipulation."""

from typing import Any


def identity(value: Any) -> Any:
    """Identity operation: returns the input unchanged.

    Args:
        value: Any cacheable value.

    Returns:
        The input value unchanged.
    """
    return value


def add(a: int, b: int) -> int:
    """Add two integers.

    Args:
        a: First integer.
        b: Second integer.

    Returns:
        Sum of a and b.
    """
    return a + b


def multiply(a: int, b: int) -> int:
    """Multiply two integers.

    Args:
        a: First integer.
        b: Second integer.

    Returns:
        Product of a and b.
    """
    return a * b


def dict_get(dict_obj: dict[str, Any], key: str) -> Any:
    """Extract a value from a dictionary.

    Args:
        dict_obj: Dictionary object.
        key: String key to look up.

    Returns:
        The value at the specified key in the dictionary.

    Raises:
        KeyError: If key not in dictionary.
        TypeError: If dict_obj is not a dict.
    """
    if not isinstance(dict_obj, dict):
        raise TypeError(f"dict_get op requires dict, got {type(dict_obj)}")

    if key not in dict_obj:
        raise KeyError(f"Key '{key}' not found in dictionary")

    return dict_obj[key]


def make_dict(**kwargs: Any) -> dict[str, Any]:
    """Construct a dictionary from resolved parameters.

    This operation collects all resolved parameters (which may have been
    constructed using ref() and cel() markers) into a new dictionary artifact.

    Args:
        **kwargs: Any number of key-value pairs. Keys must be strings.
                 Values can be any cacheable type (resolved from ref/cel markers).

    Returns:
        A dictionary containing all the key-value pairs from kwargs.

    Example:
        Node(
            op_name="stdlib:make_dict",
            params={"width": cel("bg.width"), "color": ref("fg_color")},
            deps=["bg", "fg_color"],
        )
        # Returns: {"width": 144, "color": "#ff0000"}
    """
    return dict(kwargs)


def make_list(items: list[Any]) -> list[Any]:
    """Construct a list from resolved items.

    This operation takes a list parameter (which may contain ref() and cel()
    markers that get resolved during parameter resolution) and returns it as
    a new list artifact.

    Args:
        items: A list of cacheable values (resolved from ref/cel markers).

    Returns:
        A list containing all the resolved items.

    Example:
        Node(
            op_name="stdlib:make_list",
            params={"items": [ref("a"), ref("b"), cel("c + 1")]},
            deps=["a", "b", "c"],
        )
        # Returns: [<resolved a>, <resolved b>, <resolved c+1>]
    """
    return list(items)


def coalesce(values: list[Any]) -> Any:
    """Return the first non-None value from a list of candidates.

    Args:
        values: Candidate values in priority order.

    Returns:
        The first value that is not None, or None when all candidates are None.
    """
    if not isinstance(values, list):
        raise TypeError(f"coalesce op requires list, got {type(values)}")

    for value in values:
        if value is not None:
            return value
    return None


# Package of standard operations
OPS: dict[str, Any] = {
    "identity": identity,
    "add": add,
    "multiply": multiply,
    "dict_get": dict_get,
    "make_dict": make_dict,
    "make_list": make_list,
    "coalesce": coalesce,
}
