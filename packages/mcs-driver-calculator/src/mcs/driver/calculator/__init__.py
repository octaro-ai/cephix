"""``mcs.driver.calculator`` -- scientific-calculator MCS ToolDriver.

One tool, ``calculate``, evaluating math expression strings in an
AST-whitelist sandbox. Backed by ``math``, ``statistics`` and a
small set of builtins; no ``eval()``, no ``exec()``, no imports,
no attribute access, no comprehensions.
"""

from mcs.driver.calculator.tooldriver import CalculatorToolDriver

__all__ = ["CalculatorToolDriver"]
