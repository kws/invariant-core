"""Tests for standard operations library."""

import pytest
from invariant.ops.stdlib import (
    add,
    coalesce,
    dict_get,
    identity,
    make_dict,
    make_list,
    multiply,
)


class TestIdentity:
    """Tests for identity operation."""

    def test_identity_string(self):
        """Test identity with string."""
        value = "test"
        result = identity(value)
        assert result == value
        assert isinstance(result, str)

    def test_identity_integer(self):
        """Test identity with integer."""
        value = 42
        result = identity(value)
        assert result == value
        assert isinstance(result, int)


class TestAdd:
    """Tests for add operation."""

    def test_add_integers(self):
        """Test adding two integers."""
        result = add(a=1, b=2)
        assert isinstance(result, int)
        assert result == 3

    def test_add_negative(self):
        """Test adding negative integers."""
        result = add(a=-5, b=3)
        assert result == -2

    def test_add_zero(self):
        """Test adding with zero."""
        result = add(a=42, b=0)
        assert result == 42


class TestMultiply:
    """Tests for multiply operation."""

    def test_multiply_integers(self):
        """Test multiplying two integers."""
        result = multiply(a=3, b=4)
        assert isinstance(result, int)
        assert result == 12

    def test_multiply_negative(self):
        """Test multiplying negative integers."""
        result = multiply(a=-2, b=3)
        assert result == -6

    def test_multiply_zero(self):
        """Test multiplying by zero."""
        result = multiply(a=42, b=0)
        assert result == 0


class TestDictGet:
    """Tests for dict_get operation."""

    def test_dict_get(self):
        """Test extracting value from dict."""
        result = dict_get(dict_obj={"a": 1, "b": 2}, key="a")
        assert result == 1

    def test_dict_get_missing_key(self):
        """Test that missing key raises KeyError."""
        with pytest.raises(KeyError):
            dict_get(dict_obj={"a": 1}, key="missing")

    def test_dict_get_not_dict(self):
        """Test that non-dict raises TypeError."""
        with pytest.raises(TypeError):
            dict_get(dict_obj="not a dict", key="a")


class TestMakeDict:
    """Tests for make_dict operation."""

    def test_make_dict_basic(self):
        """Test constructing a dict from kwargs."""
        result = make_dict(a=1, b=2, c="test")
        assert isinstance(result, dict)
        assert result == {"a": 1, "b": 2, "c": "test"}

    def test_make_dict_empty(self):
        """Test constructing an empty dict."""
        result = make_dict()
        assert isinstance(result, dict)
        assert result == {}

    def test_make_dict_nested(self):
        """Test constructing a dict with nested values."""
        result = make_dict(
            width=144,
            height=72,
            metadata={"color": "red", "size": "large"},
        )
        assert result["width"] == 144
        assert result["height"] == 72
        assert result["metadata"] == {"color": "red", "size": "large"}


class TestMakeList:
    """Tests for make_list operation."""

    def test_make_list_basic(self):
        """Test constructing a list from items."""
        result = make_list(items=[1, 2, 3])
        assert isinstance(result, list)
        assert result == [1, 2, 3]

    def test_make_list_empty(self):
        """Test constructing an empty list."""
        result = make_list(items=[])
        assert isinstance(result, list)
        assert result == []

    def test_make_list_mixed_types(self):
        """Test constructing a list with mixed types."""
        result = make_list(items=[1, "test", True, None])
        assert result == [1, "test", True, None]

    def test_make_list_nested(self):
        """Test constructing a list with nested structures."""
        result = make_list(items=[[1, 2], {"a": 1}, "string"])
        assert result == [[1, 2], {"a": 1}, "string"]


class TestCoalesce:
    """Tests for coalesce operation."""

    def test_coalesce_returns_first_non_none(self):
        """Test selecting the first non-None value."""
        assert coalesce(values=[None, "override", "default"]) == "override"

    def test_coalesce_can_return_falsey_values(self):
        """Test that falsey values are still selected."""
        assert coalesce(values=[None, 0, "default"]) == 0

    def test_coalesce_all_none(self):
        """Test coalesce returns None when no candidate is present."""
        assert coalesce(values=[None, None]) is None

    def test_coalesce_requires_list(self):
        """Test that non-list candidates raise TypeError."""
        with pytest.raises(TypeError):
            coalesce(values="not a list")
