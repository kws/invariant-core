"""CEL expression engine for evaluating ${...} expressions in node parameters.

This module provides expression evaluation using the Common Expression Language (CEL).
Expressions are embedded in parameter values using ${...} delimiters and are evaluated
with dependency artifacts exposed as variables.

Expression Syntax
-----------------

Variable References:
    - `${x}` - References the entire artifact. For artifacts with a `.value` attribute,
      this returns the value directly.
    - `${x.value}` - Accesses the `value` field of artifact `x`. This is the most common
      pattern for accessing numeric or string values from artifacts.

Integer Arithmetic:
    - `${x.value + 1}` - Add integers
    - `${x.value + y.value}` - Add two artifact values
    - `${x.value * 2}` - Multiply
    - `${x.value - y.value}` - Subtract

Decimal Arithmetic (Avoiding Floats):
    Per the Strict Numeric Policy, native float types are forbidden in cacheable data
    because IEEE 754 floats are non-deterministic across architectures. Use the `decimal()`
    function for fractional arithmetic:

    - `${decimal("3.14")}` - Create a Decimal from a string
    - `${decimal("1.5") + decimal("2.5")}` - Decimal arithmetic (returns Decimal)
    - `${decimal(x.value)}` - Convert an integer to Decimal
    - `${decimal("3.14") * 2}` - Decimal multiplication

Built-in Functions:
    - `min(a, b)` - Returns the minimum of two comparable values
    - `max(a, b)` - Returns the maximum of two comparable values
    - `decimal(value)` - Converts a value (int, string, or Decimal) to Decimal

    Examples:
    - `${min(x, y)}` - Returns the artifact with the smaller value
    - `${min(x.value, y.value)}` - Returns the smaller numeric value
    - `${max(x.value, 10)}` - Returns the larger of x.value or 10

String Interpolation:
    - `"prefix_${x.value}_suffix"` - Mixed text and expressions
    - `"Result: ${x.value + y.value}"` - String concatenation with expressions

Error Cases:
    - Expressions that return float (double) values raise ValueError. Use `decimal()`
      for fractional arithmetic.
    - References to undeclared dependencies raise ValueError.
    - Invalid expression syntax raises ValueError with details.

Examples
--------

Basic variable access:
    >>> params = {"width": "${background}"}
    >>> deps = {"background": 100}
    >>> resolve_params(params, deps)
    {"width": 100}

Arithmetic with multiple dependencies:
    >>> params = {"sum": "${x + y}"}
    >>> deps = {"x": 3, "y": 7}
    >>> resolve_params(params, deps)
    {"sum": 10}

Decimal arithmetic (avoiding floats):
    >>> params = {"result": "${decimal(\"3.14\") + decimal(\"2.86\")}"}
    >>> deps = {}
    >>> resolve_params(params, deps)
    {"result": Decimal("6.00")}

Using min/max for canonicalization:
    >>> params = {"a": "${min(x, y)}", "b": "${max(x, y)}"}
    >>> deps = {"x": 7, "y": 3}
    >>> resolved = resolve_params(params, deps)
    >>> resolved["a"]  # Returns 3 (the smaller value)
    3
    >>> resolved["b"]  # Returns 7 (the larger value)
    7

String interpolation:
    >>> params = {"message": "Width is ${width}px"}
    >>> deps = {"width": 200}
    >>> resolve_params(params, deps)
    {"message": "Width is 200px"}
"""

import re
from decimal import Decimal
from functools import lru_cache
from typing import Any

import celpy
import celpy.celparser
import celpy.celtypes as celtypes
import celpy.evaluation

from invariant.params import cel, ref
from invariant.protocol import ICacheable


def resolve_params(
    params: dict[str, Any], dependencies: dict[str, Any]
) -> dict[str, Any]:
    """Resolve ${...} CEL expressions in parameter values.

    Walks through the params dictionary, finds values containing ${...} delimiters,
    evaluates the CEL expression with dependency artifacts exposed as variables,
    and replaces the expression with the resolved value.

    Args:
        params: Dictionary of parameter name -> value. Values may contain ${...} expressions.
        dependencies: Dictionary mapping dependency IDs to their artifacts (native types or ICacheable).
                    These are exposed as variables in CEL expressions.

    Returns:
        Dictionary with all ${...} expressions resolved to their evaluated values.

    Raises:
        ValueError: If expression evaluation fails, undeclared dependencies are referenced,
                   or expression result is a float (double).
    """
    resolved = {}
    for key, value in params.items():
        resolved[key] = _resolve_value(value, dependencies)
    return resolved


def _resolve_value(value: Any, dependencies: dict[str, Any]) -> Any:
    """Recursively resolve expressions in a value.

    Handles ref() markers (artifact passthrough), cel() markers (CEL expressions),
    ${...} string interpolation, and nested structures (dicts, lists).

    Args:
        value: The value to resolve. May be:
            - ref(dep): Resolves to the artifact from dependency (native type or ICacheable)
            - cel(expr): Evaluates CEL expression and returns computed value
            - str with ${...}: String interpolation with CEL expressions
            - dict/list: Recursively resolves nested values
            - Other: Returns as-is
        dependencies: Dictionary of dependency artifacts (native types or ICacheable).

    Returns:
        Resolved value with all markers and expressions evaluated.

    Raises:
        ValueError: If ref() references undeclared dependency.
    """
    # Handle ref() marker - artifact passthrough
    if isinstance(value, ref):
        if value.dep not in dependencies:
            raise ValueError(
                f"ref('{value.dep}') references undeclared dependency '{value.dep}'. "
                f"Available dependencies: {list(dependencies.keys())}"
            )
        return dependencies[value.dep]

    # Handle cel() marker - CEL expression evaluation
    if isinstance(value, cel):
        return _evaluate_cel(value.expr, dependencies)

    # Handle string with ${...} interpolation
    if isinstance(value, str):
        if "${" in value and "}" in value:
            # Extract and evaluate CEL expression
            return _evaluate_expression(value, dependencies)
        return value

    # Handle nested structures
    if isinstance(value, dict):
        return {k: _resolve_value(v, dependencies) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_value(item, dependencies) for item in value]

    # Primitive value, return as-is
    return value


def _evaluate_expression(expr_string: str, dependencies: dict[str, Any]) -> Any:
    """Evaluate a CEL expression string.

    Args:
        expr_string: String potentially containing ${...} expressions.
        dependencies: Dictionary of dependency artifacts.

    Returns:
        Resolved value after evaluating all expressions.

    Raises:
        ValueError: If expression is invalid or references undeclared dependencies.
    """
    # Find all ${...} expressions
    pattern = r"\$\{([^}]+)\}"
    matches = re.findall(pattern, expr_string)

    if not matches:
        # No expressions found, return as-is
        return expr_string

    if len(matches) == 1 and expr_string.strip() == f"${{{matches[0]}}}":
        # Single expression covering the entire string - evaluate it
        cel_expr = matches[0].strip()
        return _evaluate_cel(cel_expr, dependencies)
    else:
        # Multiple expressions or mixed with text - evaluate each and substitute
        result = expr_string
        for match in matches:
            cel_expr = match.strip()
            evaluated = _evaluate_cel(cel_expr, dependencies)
            # Convert to string for substitution
            result = result.replace(f"${{{match}}}", str(evaluated))
        return result


@lru_cache(maxsize=1024)
def _compiled_expression(expression):
    # Cache syntax only; programs and dependency activations remain per call.
    return celpy.Environment().compile(expression)


def _evaluate_cel(expression: str, dependencies: dict[str, Any]) -> Any:
    """Evaluate a single CEL expression.

    Args:
        expression: The CEL expression to evaluate (without ${...} delimiters).
        dependencies: Dictionary of dependency artifacts (native types or ICacheable).

    Returns:
        The evaluated result. Must be a type that can be hashed (no floats).

    Raises:
        ValueError: If expression is invalid, references undeclared deps, or returns float.
    """
    # Build CEL environment
    env = celpy.Environment()

    # Convert dependencies to CEL-compatible types
    var_declarations: dict[str, Any] = {}
    for dep_id, artifact in dependencies.items():
        # Convert artifact to CEL-compatible type
        cel_value = _value_to_cel(artifact)
        var_declarations[dep_id] = cel_value

    # Compile the expression
    try:
        ast = (
            _compiled_expression(expression)
            if len(expression) <= 4096
            else env.compile(expression)
        )
    except celpy.celparser.CELParseError as e:
        raise ValueError(f"Failed to parse CEL expression '{expression}': {e}") from e

    # Register custom functions: decimal(), min(), max()
    custom_functions = {
        "decimal": _decimal_function,
        "min": _min_function,
        "max": _max_function,
    }

    # Create program with custom functions
    program = env.program(ast, functions=custom_functions)

    # Create activation with variables
    activation: dict[str, Any] = {}
    for var_name, var_value in var_declarations.items():
        activation[var_name] = var_value

    # Evaluate
    try:
        result = program.evaluate(activation)
    except celpy.evaluation.CELEvalError as e:
        raise ValueError(
            f"Failed to evaluate CEL expression '{expression}': {e}"
        ) from e

    # Check for float results (forbidden per Strict Numeric Policy)
    if isinstance(result, float):
        raise ValueError(
            f"Expression '{expression}' returned a float (double), which is forbidden. "
            f"Use decimal() for fractional arithmetic."
        )

    # If result is a MapType (from ICacheable domain type), extract the value for simple variable references
    if isinstance(result, celtypes.MapType):
        # Check if this is a simple variable reference like ${x}
        # If the map has a 'value' key, extract it
        value_key = celtypes.StringType("value")
        if value_key in result:
            result = result[value_key]
        else:
            # Return the map as-is (might be used in further expressions)
            return _cel_to_python(result, expression)

    # Convert result to appropriate Python type
    return _cel_to_python(result, expression)


def _value_to_cel(value: Any) -> Any:
    """Convert a value (native type or ICacheable) to a CEL-compatible type.

    Native types are exposed directly. ICacheable domain types are converted to MapType
    for field access.

    Args:
        value: The value to convert (int, str, Decimal, dict, list, ICacheable, etc.).

    Returns:
        CEL-compatible value (IntType, StringType, MapType, etc.).
    """
    # Native types - expose directly
    if isinstance(value, int):
        return celtypes.IntType(value)
    if isinstance(value, str):
        return celtypes.StringType(value)
    if isinstance(value, bool):
        return celtypes.BoolType(value)
    if isinstance(value, Decimal):
        # Decimal is passed through as-is (CEL doesn't have native Decimal)
        return value
    if isinstance(value, dict):
        # Convert dict to CEL MapType recursively
        result: dict[celtypes.StringType, Any] = {}
        for key, val in value.items():
            result[celtypes.StringType(key)] = _value_to_cel(val)
        return celtypes.MapType(result)
    if isinstance(value, (list, tuple)):
        # Convert list/tuple to CEL ListType recursively
        return celtypes.ListType([_value_to_cel(item) for item in value])
    if value is None:
        # None is represented as null in CEL
        return None

    # ICacheable domain types - convert to MapType for field access
    if isinstance(value, ICacheable):
        return _icacheable_to_cel_map(value)

    # Fallback: convert to string
    return celtypes.StringType(str(value))


def _icacheable_to_cel_map(artifact: ICacheable) -> celtypes.MapType:
    """Convert an ICacheable domain type to a CEL-compatible MapType.

    Exposes all public attributes of the artifact for field access.

    Args:
        artifact: The ICacheable artifact to convert.

    Returns:
        MapType representation suitable for CEL evaluation.
    """
    result: dict[celtypes.StringType, Any] = {}

    # Expose all public attributes
    for attr_name in dir(artifact):
        if not attr_name.startswith("_") and not callable(
            getattr(artifact, attr_name, None)
        ):
            try:
                attr_value = getattr(artifact, attr_name)
                # Convert attribute value to CEL type recursively
                result[celtypes.StringType(attr_name)] = _value_to_cel(attr_value)
            except AttributeError:
                pass

    return celtypes.MapType(result)


def _cel_to_python(cel_value: Any, expression: str) -> Any:
    """Convert a CEL value to a Python type.

    Handles conversion of CEL types (IntType, StringType, etc.) to Python types.

    Args:
        cel_value: The value returned from CEL evaluation.
        expression: The original expression (for error messages).

    Returns:
        Python value (int, str, Decimal, ICacheable, etc.)

    Raises:
        ValueError: If value is a float (forbidden).
    """
    # Handle CEL types
    if isinstance(cel_value, celtypes.IntType):
        return int(cel_value)
    elif isinstance(cel_value, celtypes.StringType):
        return str(cel_value)
    elif isinstance(cel_value, celtypes.BoolType):
        return bool(cel_value)
    elif isinstance(cel_value, float):
        # This should have been caught earlier, but double-check
        raise ValueError(
            f"Expression '{expression}' returned float, which is forbidden. "
            f"Use decimal() for fractional values."
        )
    elif isinstance(cel_value, Decimal):
        return cel_value
    elif isinstance(cel_value, celtypes.MapType):
        # Convert MapType to dict
        result_dict: dict[str, Any] = {}
        for key, value in cel_value.items():
            if isinstance(key, celtypes.StringType):
                result_dict[str(key)] = _cel_to_python(value, expression)
            else:
                result_dict[str(key)] = _cel_to_python(value, expression)
        return result_dict
    elif isinstance(cel_value, (int, str, bool)):
        # Already Python types
        return cel_value
    elif isinstance(cel_value, dict):
        # Plain dict - convert recursively
        return {k: _cel_to_python(v, expression) for k, v in cel_value.items()}
    elif isinstance(cel_value, list):
        # Plain list - convert recursively
        return [_cel_to_python(item, expression) for item in cel_value]
    else:
        # Unknown type - return as-is (might be a CEL-specific type or already Python)
        return cel_value


# Custom CEL functions
def _decimal_function(value: Any) -> Decimal:
    """CEL function: decimal(value) - constructs a Decimal from int, string, or Decimal.

    Args:
        value: Can be IntType, StringType, int, str, Decimal, or MapType with value field.

    Returns:
        Decimal value.

    Raises:
        ValueError: If value cannot be converted to Decimal.
    """
    # Handle CEL types
    if isinstance(value, celtypes.IntType):
        return Decimal(str(int(value)))
    elif isinstance(value, celtypes.StringType):
        return Decimal(str(value))
    elif isinstance(value, celtypes.MapType):
        # Extract value from MapType
        value_key = celtypes.StringType("value")
        if value_key in value:
            return _decimal_function(value[value_key])
        else:
            raise ValueError("Cannot extract value from MapType for decimal conversion")
    # Handle Python types
    elif isinstance(value, Decimal):
        return value
    elif isinstance(value, int):
        return Decimal(str(value))
    elif isinstance(value, str):
        return Decimal(value)
    elif isinstance(value, ICacheable) and hasattr(value, "value"):
        # Extract value from ICacheable
        return Decimal(str(value.value))
    else:
        raise ValueError(f"Cannot convert {type(value)} to Decimal")


def _min_function(a: Any, b: Any) -> Any:
    """CEL function: min(a, b) - returns the minimum of two comparable values.

    Handles both direct values and MapType artifacts (extracts .value for comparison).

    Args:
        a: First value (can be IntType, StringType, MapType, etc.)
        b: Second value (can be IntType, StringType, MapType, etc.)

    Returns:
        The minimum of a and b. If both are MapType, extracts .value for comparison.
    """
    # Handle MapType arguments - extract value for comparison
    a_val = _extract_comparison_value(a)
    b_val = _extract_comparison_value(b)

    # Compare and return the original type (not the extracted value)
    if a_val < b_val:
        return a
    return b


def _max_function(a: Any, b: Any) -> Any:
    """CEL function: max(a, b) - returns the maximum of two comparable values.

    Handles both direct values and MapType artifacts (extracts .value for comparison).

    Args:
        a: First value (can be IntType, StringType, MapType, etc.)
        b: Second value (can be IntType, StringType, MapType, etc.)

    Returns:
        The maximum of a and b. If both are MapType, extracts .value for comparison.
    """
    # Handle MapType arguments - extract value for comparison
    a_val = _extract_comparison_value(a)
    b_val = _extract_comparison_value(b)

    # Compare and return the original type (not the extracted value)
    if a_val > b_val:
        return a
    return b


def _extract_comparison_value(value: Any) -> Any:
    """Extract a comparable value from a CEL type for min/max comparison.

    Args:
        value: Can be MapType, IntType, StringType, or other comparable type.

    Returns:
        A value suitable for comparison (<, >, etc.)
    """
    if isinstance(value, celtypes.MapType):
        # Extract .value field from MapType
        value_key = celtypes.StringType("value")
        if value_key in value:
            return _extract_comparison_value(value[value_key])
        else:
            # If no value field, try to compare the map itself (not ideal but works)
            return value
    elif isinstance(value, celtypes.IntType):
        return int(value)
    elif isinstance(value, celtypes.StringType):
        return str(value)
    elif isinstance(value, celtypes.BoolType):
        return bool(value)
    else:
        # Already a comparable Python type
        return value
