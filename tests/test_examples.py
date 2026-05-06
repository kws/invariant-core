"""Unit tests for example scripts.

These tests ensure that the example scripts continue to work correctly
when code changes are made to the invariant package. Tests execute the
scripts as external Python processes using subprocess for accurate testing.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Get the project root directory (parent of tests/)
PROJECT_ROOT = Path(__file__).parent.parent
EXAMPLES_DIR = PROJECT_ROOT / "examples"
SERIALIZED_EXAMPLES_DIR = EXAMPLES_DIR / "serialized"


class TestCommutativeCanonicalizationExample:
    """Tests for examples/commutative_canonicalization.py."""

    @pytest.fixture
    def script_path(self):
        """Return the path to the commutative canonicalization example script."""
        return EXAMPLES_DIR / "commutative_canonicalization.py"

    def test_default_arguments(self, script_path):
        """Test example with default arguments (x=7, y=3)."""
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Both sum_xy and sum_yx resolve to manifest {a: 3, b: 7}" in output
        assert "sum_xy result: 10" in output
        assert "sum_yx result: 10" in output
        assert "Results are equal: True" in output
        assert result.returncode == 0

    def test_custom_arguments(self, script_path):
        """Test example with custom arguments (x=10, y=5)."""
        result = subprocess.run(
            [sys.executable, str(script_path), "--x", "10", "--y", "5"],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Both sum_xy and sum_yx resolve to manifest {a: 5, b: 10}" in output
        assert "sum_xy result: 15" in output
        assert "sum_yx result: 15" in output
        assert "Results are equal: True" in output
        assert result.returncode == 0

    def test_swapped_values(self, script_path):
        """Test with swapped values (x=3, y=7) to verify canonicalization works."""
        result = subprocess.run(
            [sys.executable, str(script_path), "--x", "3", "--y", "7"],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        # Should still canonicalize to min=3, max=7
        assert "✓ Both sum_xy and sum_yx resolve to manifest {a: 3, b: 7}" in output
        assert "sum_xy result: 10" in output
        assert "sum_yx result: 10" in output
        assert result.returncode == 0

    def test_negative_numbers(self, script_path):
        """Test with negative numbers."""
        result = subprocess.run(
            [sys.executable, str(script_path), "--x", "-5", "--y", "10"],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        # min(-5, 10) = -5, max(-5, 10) = 10
        assert "✓ Both sum_xy and sum_yx resolve to manifest {a: -5, b: 10}" in output
        assert "sum_xy result: 5" in output
        assert "sum_yx result: 5" in output
        assert result.returncode == 0

    def test_same_values(self, script_path):
        """Test with same values to verify canonicalization still works."""
        result = subprocess.run(
            [sys.executable, str(script_path), "--x", "5", "--y", "5"],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        # min(5, 5) = 5, max(5, 5) = 5
        assert "✓ Both sum_xy and sum_yx resolve to manifest {a: 5, b: 5}" in output
        assert "sum_xy result: 10" in output
        assert "sum_yx result: 10" in output
        assert result.returncode == 0


class TestPolynomialDistributiveExample:
    """Tests for examples/polynomial_distributive.py."""

    @pytest.fixture
    def script_path(self):
        """Return the path to the polynomial distributive example script."""
        return EXAMPLES_DIR / "polynomial_distributive.py"

    def test_default_arguments(self, script_path):
        """Test example with default arguments."""
        result = subprocess.run(
            [sys.executable, str(script_path)],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Distributive law verified: (p + q) * r == p*r + q*r" in output
        assert "LHS coefficients: [4, 6, 2]" in output
        assert "RHS coefficients: [4, 6, 2]" in output
        assert "✓ Numeric equality at x=5: 84 == 84" in output
        assert "✓ Second derivative evaluated at x=5: 4" in output
        assert result.returncode == 0

    def test_custom_arguments(self, script_path):
        """Test with custom polynomial coefficients and evaluation point."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--p-coeffs",
                "2,1",
                "--q-coeffs",
                "1,0",
                "--r-coeffs",
                "3,2",
                "--x",
                "10",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Distributive law verified: (p + q) * r == p*r + q*r" in output
        assert "✓ Numeric equality at x=10:" in output
        assert "✓ Second derivative evaluated at x=10:" in output
        assert result.returncode == 0

    def test_negative_coefficients(self, script_path):
        """Test with negative coefficients."""
        # Use = syntax to avoid argparse issues with negative numbers
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--p-coeffs=1,-2,1",
                "--q-coeffs=-1,0,1",
                "--r-coeffs=1,1",
                "--x",
                "3",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Distributive law verified: (p + q) * r == p*r + q*r" in output
        assert "✓ Numeric equality at x=3:" in output
        assert result.returncode == 0

    def test_single_coefficient(self, script_path):
        """Test with single coefficient polynomials (constants)."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--p-coeffs",
                "5",
                "--q-coeffs",
                "3",
                "--r-coeffs",
                "2",
                "--x",
                "7",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Distributive law verified: (p + q) * r == p*r + q*r" in output
        # (5 + 3) * 2 == 5*2 + 3*2 == 16
        assert "✓ Numeric equality at x=7:" in output
        assert result.returncode == 0

    def test_zero_coefficients(self, script_path):
        """Test with polynomials containing zero coefficients."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--p-coeffs",
                "1,0,1",
                "--q-coeffs",
                "0,2,0",
                "--r-coeffs",
                "1,0",
                "--x",
                "4",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Distributive law verified: (p + q) * r == p*r + q*r" in output
        assert "✓ Numeric equality at x=4:" in output
        assert result.returncode == 0

    def test_different_evaluation_point(self, script_path):
        """Test with different evaluation point while keeping default polynomials."""
        result = subprocess.run(
            [
                sys.executable,
                str(script_path),
                "--x",
                "0",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        output = result.stdout
        assert "✓ Distributive law verified: (p + q) * r == p*r + q*r" in output
        assert "✓ Numeric equality at x=0:" in output
        # At x=0, polynomials evaluate to their constant term
        # LHS and RHS should still be equal
        assert result.returncode == 0


class TestSerializedGraphExamples:
    """Tests for serialized graph examples."""

    def test_commutative_canonicalization_json_executes(self):
        """Serialized commutative graph matches the default Python example."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "invariant",
                str(SERIALIZED_EXAMPLES_DIR / "commutative_canonicalization.json"),
                "--pick",
                "x",
                "--pick",
                "y",
                "--pick",
                "sum_xy",
                "--pick",
                "sum_yx",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        output = json.loads(result.stdout)
        assert output["x"] == 7
        assert output["y"] == 3
        assert output["sum_xy"] == 10
        assert output["sum_yx"] == 10

    def test_polynomial_distributive_json_executes(self):
        """Serialized polynomial graph matches the default Python example."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "invariant",
                str(SERIALIZED_EXAMPLES_DIR / "polynomial_distributive.json"),
                "--pick",
                "eval_lhs",
                "--pick",
                "eval_rhs",
                "--pick",
                "eval_d2",
                "--pick",
                "lhs",
                "--pick",
                "rhs",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        output = json.loads(result.stdout)
        assert output["eval_lhs"] == 84
        assert output["eval_rhs"] == 84
        assert output["eval_d2"] == 4
        assert output["lhs"] == {
            "$icacheable": {
                "type": "invariant.types.Polynomial",
                "value": {"coefficients": [4, 6, 2]},
            }
        }
        assert output["rhs"] == output["lhs"]

    def test_serialized_graph_pick_output(self):
        """Serialized examples support CLI --pick for a single node output."""
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "invariant",
                str(SERIALIZED_EXAMPLES_DIR / "polynomial_distributive.json"),
                "--pick",
                "eval_lhs",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        assert result.stdout == "84\n"
