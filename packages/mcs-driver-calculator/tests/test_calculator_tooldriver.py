"""Tests for the AST-sandboxed :class:`CalculatorToolDriver`."""

from __future__ import annotations

import math

import pytest

from mcs.driver.core import MCSToolDriver, Tool

from mcs.driver.calculator import CalculatorToolDriver


@pytest.fixture
def driver() -> CalculatorToolDriver:
    return CalculatorToolDriver()


def _eval(driver: CalculatorToolDriver, expr: str) -> dict:
    return driver.execute_tool("calculate", {"expression": expr})


# ---- identity ----------------------------------------------------------------


def test_is_an_mcs_tool_driver() -> None:
    assert isinstance(CalculatorToolDriver(), MCSToolDriver)


def test_list_tools_advertises_calculate() -> None:
    driver = CalculatorToolDriver()
    tools = driver.list_tools()
    assert [t.name for t in tools] == ["calculate"]
    tool = tools[0]
    assert isinstance(tool, Tool)
    assert "**" in (tool.description or "")  # convention is in the description
    param_names = {p.name for p in (tool.parameters or ())}
    assert param_names == {"expression"}


# ---- happy paths -------------------------------------------------------------


def test_basic_arithmetic(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "2+2")["result"] == 4


def test_precedence_and_parens(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "3*(5+6)/4")["result"] == pytest.approx(8.25)


def test_sqrt_and_sqr(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "sqrt(9)")["result"] == 3.0
    assert _eval(driver, "sqr(9)")["result"] == 81


def test_factorial(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "factorial(5)")["result"] == 120


def test_pi_and_degrees(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "pi")["result"] == pytest.approx(math.pi)
    assert _eval(driver, "degrees(pi)")["result"] == pytest.approx(180.0)


def test_floor_pow_round(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "floor(3.7)")["result"] == 3
    assert _eval(driver, "pow(2, 10)")["result"] == 1024


def test_hyperbolic(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "sinh(0)")["result"] == 0.0


def test_combinatorics(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "gcd(12, 18)")["result"] == 6
    assert _eval(driver, "comb(5, 2)")["result"] == 10


def test_statistics_with_list_literal(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "mean([1,2,3,4])")["result"] == pytest.approx(2.5)


def test_gamma(driver: CalculatorToolDriver) -> None:
    # gamma(5) == 4! == 24
    assert _eval(driver, "gamma(5)")["result"] == pytest.approx(24.0)


def test_unary_minus_chain(driver: CalculatorToolDriver) -> None:
    assert _eval(driver, "-(-5)")["result"] == 5


# ---- result shape ------------------------------------------------------------


def test_result_shape_carries_number_and_formatted(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "1/3")
    assert out["expression"] == "1/3"
    assert out["result"] == pytest.approx(1 / 3)
    assert out["formatted"] == str(1 / 3)


def test_integer_result_formatted_as_int_string(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "2+2")
    assert out["result"] == 4
    assert out["formatted"] == "4"


# ---- operator quirks (friendly errors) --------------------------------------


def test_caret_is_xor_message(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "2^3")
    assert "error" in out
    assert "XOR" in out["error"]
    assert "**" in out["error"]


def test_factorial_glyph_hint(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "5!")
    assert "error" in out
    assert "factorial" in out["error"]


def test_implicit_multiplication_hint(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "2(3+4)")
    assert "error" in out
    assert "implicit multiplication" in out["error"]


# ---- escape attempts (sandbox integrity) ------------------------------------


@pytest.mark.parametrize("expr", [
    "__import__('os').system('echo pwn')",
    "().__class__.__bases__[0].__subclasses__()",
    "(lambda: 1)()",
    "[i for i in range(3)]",
    "math.pi",
    "'hi' * 1000",
    "1 if True else 2",
    "1 < 2",
    "mean.__globals__",
    "True and False",
    "f'{1+1}'",
])
def test_escape_attempts_return_error_not_crash(
    driver: CalculatorToolDriver, expr: str
) -> None:
    out = _eval(driver, expr)
    assert "error" in out, f"escape attempt {expr!r} returned {out!r}"
    assert "result" not in out


def test_attribute_access_blocked(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "(3).real")
    assert "error" in out


def test_bool_constant_blocked(driver: CalculatorToolDriver) -> None:
    """``True`` parses as ast.Constant(value=True) -- blocked so the
    user does not get surprised by True+1==2 arithmetic."""
    out = _eval(driver, "True")
    assert "error" in out


def test_string_constant_blocked(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "'hello'")
    assert "error" in out


def test_complex_literal_blocked(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "1+2j")
    assert "error" in out


def test_standalone_list_blocked(driver: CalculatorToolDriver) -> None:
    """List literals only allowed as Call arguments."""
    out = _eval(driver, "[1,2,3]")
    assert "error" in out


def test_keyword_argument_blocked(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "pow(2, exp=10)")
    assert "error" in out


# ---- numerical edge cases ---------------------------------------------------


def test_division_by_zero_soft_fails(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "1/0")
    assert "error" in out
    assert "zero" in out["error"].lower()


def test_sqrt_negative_soft_fails(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "sqrt(-1)")
    assert "error" in out


def test_factorial_negative_soft_fails(driver: CalculatorToolDriver) -> None:
    out = _eval(driver, "factorial(-3)")
    assert "error" in out


# ---- input contract ----------------------------------------------------------


def test_non_string_expression_returns_error(driver: CalculatorToolDriver) -> None:
    out = driver.execute_tool("calculate", {"expression": 42})
    assert "error" in out


def test_unknown_tool_raises(driver: CalculatorToolDriver) -> None:
    with pytest.raises(ValueError):
        driver.execute_tool("not-a-tool", {})
