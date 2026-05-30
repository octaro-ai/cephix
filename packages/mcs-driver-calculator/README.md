# mcs-driver-calculator

`CalculatorToolDriver` -- a scientific-calculator MCS ToolDriver. One tool, `calculate`, that evaluates a math expression string and returns the numeric result.

## Sandbox

The expression is **parsed** with `ast.parse(expr, mode="eval")` (just the parser -- never `eval()` or `exec()`), then walked through a recursive interpreter that only accepts an explicit allowlist of:

- AST nodes (`Expression`, `Constant`, `BinOp`, `UnaryOp`, `Name`, `Call`, plus `List` / `Tuple` for `statistics`-style arguments).
- Operators (`+ - * / // % **`, unary `+ -`). `^` is **not** in the list (Python XOR) -- the error message tells the caller to use `**`.
- Function names (hand-curated map from `math` + `statistics` + builtins; see below).
- Constant names (`pi`, `e`, `tau`, `inf`, `nan`).

Anything else (`Attribute`, `Subscript`, `Lambda`, comprehensions, `JoinedStr`, `BoolOp`, `Compare`, `IfExp`, ...) raises a `ValueError` before the walker ever touches a Python runtime symbol. The interpreter never resolves a name through `getattr` -- only through the two whitelist dicts.

## Available functions

- **square / root**: `sqrt`, `sqr` (= x*x), `cbrt`, `pow`
- **trig**: `sin cos tan asin acos atan atan2`
- **hyperbolic**: `sinh cosh tanh asinh acosh atanh`
- **log / exp**: `log log2 log10 exp expm1 log1p`
- **deg/rad**: `degrees radians`
- **combinatorics**: `factorial gcd lcm comb perm`
- **special**: `gamma lgamma erf erfc`
- **rounding / sign**: `floor ceil trunc round abs`
- **statistics**: `mean median mode stdev variance` (accept list literals: `mean([1,2,3,4])`)
- **selection**: `min max`
- **constants**: `pi e tau inf nan`

## Python conventions

- Use `**` for power, not `^` (Python XOR).
- Use `factorial(n)` for `n!` (the `!` glyph is not Python syntax).
- Implicit multiplication is not supported: write `2*(3+4)`, not `2(3+4)`.
- Use ASCII names: `sqrt`, `pi` -- not the `√` or `π` glyphs.

## Result shape

```python
driver.execute_tool("calculate", {"expression": "sqrt(9) + 3*(5+6)/4"})
# {'expression': 'sqrt(9) + 3*(5+6)/4', 'result': 11.25, 'formatted': '11.25'}

driver.execute_tool("calculate", {"expression": "2^3"})
# {'expression': '2^3', 'error': "'^' is Python XOR; use '**' for power"}

driver.execute_tool("calculate", {"expression": "__import__('os').system('echo pwn')"})
# {'expression': "...", 'error': 'AST node not allowed: Attribute'}
# Nothing executes.
```

`result` stays a number for downstream arithmetic; `formatted` is `str(result)` for direct display. The driver never raises -- syntax errors, disallowed nodes, division by zero, and math-domain errors all come back as the `error` field.
