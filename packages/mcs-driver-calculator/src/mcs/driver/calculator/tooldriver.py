"""``CalculatorToolDriver`` -- scientific-calculator MCS ToolDriver.

Evaluates math expression strings safely. The implementation
splits cleanly into three pieces:

1. ``_PARSE`` -- Python's ``ast`` module as **parser only**: turns
   ``"sqrt(9) + 3*(5+6)/4"`` into an ``ast.Expression`` tree.
   ``eval()`` and ``exec()`` are never called. ``ast.literal_eval``
   is not used either -- it rejects every operator including
   binary ``*``, so it cannot serve as a calculator.

2. ``_FUNCTIONS`` and ``_CONSTANTS`` -- hand-curated dictionaries
   pointing at ``math`` / ``statistics`` / builtins entries by
   name. The walker only ever resolves a function or constant
   identifier by indexing into these dicts. No ``getattr``,
   no module attribute access at evaluation time.

3. ``_eval(node)`` -- a recursive walker that hard-checks each
   AST node type against a whitelist. Allowed: ``Expression``,
   numeric ``Constant``, ``BinOp`` / ``UnaryOp`` with whitelisted
   operators, ``Name`` (only if the identifier is in
   ``_CONSTANTS``), ``Call`` (only when ``func`` is a bare
   ``Name`` mapped in ``_FUNCTIONS`` and there are no keyword
   arguments), and ``List`` / ``Tuple`` (but only inside a
   ``Call``, so ``mean([1,2,3])`` works while ``[1,2,3]`` as a
   standalone expression is rejected). Anything else --
   ``Attribute``, ``Subscript``, ``Lambda``, ``Compare``,
   ``BoolOp``, ``IfExp``, comprehensions, f-strings, ... --
   raises ``ValueError`` before any Python value gets created.

The driver never raises out of ``execute_tool``: any parser /
walker / domain error comes back as ``{"expression": ..., "error":
...}`` so the LLM (or any caller) sees a structured failure
instead of a Python traceback.
"""

from __future__ import annotations

import ast
import math
import statistics
from dataclasses import dataclass
from typing import Any, Callable

from mcs.driver.core import (
    DriverBinding,
    DriverMeta,
    MCSToolDriver,
    Tool,
    ToolParameter,
)


_TOOL_CALCULATE = "calculate"


# ---- whitelists -------------------------------------------------------------

_BIN_OPS: dict[type[ast.operator], Callable[[Any, Any], Any]] = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[Any], Any]] = {
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: +a,
}


def _statistics_aware(fn: Callable[..., Any]) -> Callable[..., Any]:
    """statistics.{mean,median,stdev,...} take ONE iterable argument;
    the walker passes whatever the expression supplied. Pass through
    unchanged; the wrapper is here as a marker for clarity."""
    return fn


_FUNCTIONS: dict[str, Callable[..., Any]] = {
    # square / root
    "sqrt": math.sqrt,
    "sqr": lambda x: x * x,
    "cbrt": math.cbrt,
    "pow": pow,
    # trig + inverse
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan,
    "atan2": math.atan2,
    # hyperbolic + inverse
    "sinh": math.sinh, "cosh": math.cosh, "tanh": math.tanh,
    "asinh": math.asinh, "acosh": math.acosh, "atanh": math.atanh,
    # log / exp
    "log": math.log, "log2": math.log2, "log10": math.log10,
    "exp": math.exp, "expm1": math.expm1, "log1p": math.log1p,
    # deg/rad
    "degrees": math.degrees, "radians": math.radians,
    # combinatorics
    "factorial": math.factorial, "gcd": math.gcd, "lcm": math.lcm,
    "comb": math.comb, "perm": math.perm,
    # special
    "gamma": math.gamma, "lgamma": math.lgamma,
    "erf": math.erf, "erfc": math.erfc,
    # rounding / sign
    "floor": math.floor, "ceil": math.ceil, "trunc": math.trunc,
    "round": round, "abs": abs,
    # statistics (one-iterable input)
    "mean": _statistics_aware(statistics.mean),
    "median": _statistics_aware(statistics.median),
    "mode": _statistics_aware(statistics.mode),
    "stdev": _statistics_aware(statistics.stdev),
    "variance": _statistics_aware(statistics.variance),
    # selection
    "min": min, "max": max,
}

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
    "tau": math.tau,
    "inf": math.inf,
    "nan": math.nan,
}


# ---- driver -----------------------------------------------------------------


@dataclass(frozen=True)
class _CalculatorDriverMeta(DriverMeta):
    id: str = "mcs.driver.calculator.v1"
    name: str = "Calculator ToolDriver"
    version: str = "0.1.0"
    bindings: tuple[DriverBinding, ...] = (
        DriverBinding(capability="calculator", adapter="*", spec_format="Custom"),
    )
    supported_llms: None = None
    capabilities: tuple[str, ...] = ("orchestratable",)


_DESCRIPTION = (
    "Evaluate a mathematical expression string and return the numeric result.\n"
    "\n"
    "Python expression syntax with an explicit allowlist. Conventions:\n"
    "- Use `**` for power, NOT `^` (which is Python XOR).\n"
    "- Use `factorial(n)` for n!; the `!` glyph is not Python syntax.\n"
    "- Implicit multiplication is not supported -- write `2*(3+4)`, not `2(3+4)`.\n"
    "- Use ASCII names: `sqrt`, `pi` -- not the `√` or `π` glyphs.\n"
    "\n"
    "Available functions:\n"
    "  square/root: sqrt, sqr (= x*x), cbrt, pow\n"
    "  trig: sin cos tan asin acos atan atan2\n"
    "  hyperbolic: sinh cosh tanh asinh acosh atanh\n"
    "  log/exp: log log2 log10 exp expm1 log1p\n"
    "  deg/rad: degrees radians\n"
    "  combinatorics: factorial gcd lcm comb perm\n"
    "  special: gamma lgamma erf erfc\n"
    "  rounding/sign: floor ceil trunc round abs\n"
    "  statistics (one list argument): mean median mode stdev variance\n"
    "  selection: min max\n"
    "Constants: pi e tau inf nan\n"
    "\n"
    "Response: {expression, result, formatted} on success "
    "(result as number, formatted as string), {expression, error} on any failure."
)


class CalculatorToolDriver(MCSToolDriver):
    """Sandboxed scientific-calculator ToolDriver."""

    meta: DriverMeta = _CalculatorDriverMeta()

    def list_tools(self) -> list[Tool]:
        return [
            Tool(
                name=_TOOL_CALCULATE,
                title="Calculate expression",
                description=_DESCRIPTION,
                parameters=[
                    ToolParameter(
                        name="expression",
                        description=(
                            "The math expression to evaluate, e.g. "
                            "`sqrt(9) + 3*(5+6)/4`."
                        ),
                        required=True,
                        schema={"type": "string"},
                    ),
                ],
            ),
        ]

    def execute_tool(
        self, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        if tool_name != _TOOL_CALCULATE:
            raise ValueError(
                f"CalculatorToolDriver: unknown tool {tool_name!r}; "
                f"available: {[t.name for t in self.list_tools()]}"
            )
        expression = arguments.get("expression")
        if not isinstance(expression, str):
            return {
                "expression": expression,
                "error": "`expression` must be a string.",
            }

        try:
            tree = ast.parse(expression, mode="eval")
        except SyntaxError as exc:
            hint = self._syntax_hint(expression)
            msg = f"syntax error: {exc.msg}"
            if hint:
                msg = f"{msg}; {hint}"
            return {"expression": expression, "error": msg}

        try:
            result = self._eval(tree.body, in_call=False)
        except _CalcError as exc:
            return {"expression": expression, "error": str(exc)}
        except ZeroDivisionError:
            return {"expression": expression, "error": "division by zero"}
        except ValueError as exc:
            # math domain errors, factorial(-1), etc.
            return {"expression": expression, "error": f"value error: {exc}"}
        except OverflowError as exc:
            return {"expression": expression, "error": f"overflow: {exc}"}
        except TypeError as exc:
            return {"expression": expression, "error": f"type error: {exc}"}

        return {
            "expression": expression,
            "result": result,
            "formatted": str(result),
        }

    # ---- walker ------------------------------------------------------------

    def _eval(self, node: ast.AST, *, in_call: bool) -> Any:
        """Recursive whitelist walker.

        ``in_call`` is True when ``_eval`` is being called for one
        of a ``Call``'s arguments. That single flag is what lets
        ``mean([1,2,3])`` work while ``[1,2,3]`` as a standalone
        expression is still rejected -- the only path that can
        produce a list/tuple value is "argument to a whitelisted
        function".
        """
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return node.value
            raise _CalcError(
                f"constant of type {type(node.value).__name__!r} not allowed"
            )

        if isinstance(node, ast.BinOp):
            op_type = type(node.op)
            if op_type is ast.BitXor:
                raise _CalcError(
                    "'^' is Python XOR; use '**' for power"
                )
            op = _BIN_OPS.get(op_type)
            if op is None:
                raise _CalcError(
                    f"operator {op_type.__name__!r} not allowed"
                )
            left = self._eval(node.left, in_call=False)
            right = self._eval(node.right, in_call=False)
            return op(left, right)

        if isinstance(node, ast.UnaryOp):
            op = _UNARY_OPS.get(type(node.op))
            if op is None:
                raise _CalcError(
                    f"unary operator {type(node.op).__name__!r} not allowed"
                )
            return op(self._eval(node.operand, in_call=False))

        if isinstance(node, ast.Name):
            if node.id in _CONSTANTS:
                return _CONSTANTS[node.id]
            if node.id in _FUNCTIONS:
                raise _CalcError(
                    f"bare reference to function {node.id!r}; call it as {node.id}(...)"
                )
            raise _CalcError(f"unknown name {node.id!r}")

        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name):
                raise _CalcError(
                    "only top-level function names are allowed; "
                    "implicit multiplication and attribute calls "
                    "are not supported"
                )
            if node.keywords:
                raise _CalcError("keyword arguments are not allowed")
            fn = _FUNCTIONS.get(node.func.id)
            if fn is None:
                raise _CalcError(f"unknown function {node.func.id!r}")
            args = [self._eval(arg, in_call=True) for arg in node.args]
            return fn(*args)

        if isinstance(node, (ast.List, ast.Tuple)):
            if not in_call:
                raise _CalcError(
                    f"{type(node).__name__} literal only allowed as a "
                    "function argument"
                )
            return [self._eval(item, in_call=False) for item in node.elts]

        raise _CalcError(f"AST node not allowed: {type(node).__name__}")

    # ---- hints -------------------------------------------------------------

    @staticmethod
    def _syntax_hint(expression: str) -> str:
        """Best-effort follow-up for the common Python-syntax-vs-math gotchas.

        Runs only on ``SyntaxError``, where we already know the
        expression did not parse. Cheap substring probes; the
        hints are advisory.
        """
        if "!" in expression:
            return "use `factorial(n)` instead of `n!`"
        # `2(...)` implicit multiplication: a digit immediately
        # followed by `(`. AST raises SyntaxError before our walker
        # gets a chance to give the prettier message.
        for i, ch in enumerate(expression):
            if ch == "(" and i > 0 and expression[i - 1].isdigit():
                return (
                    "implicit multiplication not supported -- "
                    "write `2*(...)` instead of `2(...)`"
                )
        return ""


class _CalcError(Exception):
    """Internal sandboxing / walker failure converted to a soft
    ``error`` field in the response."""
