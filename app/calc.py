"""
USB AI — safe arithmetic evaluator for the /api/calc endpoint.

We walk the AST ourselves instead of using eval() so the LLM-driven calculator
can surface only the operators/functions we whitelist. Imported by main.py and
the test suite so there is one source of truth (no green-but-lying copy).
"""
import ast
import math
import operator

# ponytail: single dispatch table — BinOp / UnaryOp / Call all read from here.
_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub,
    ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.Pow: operator.pow, ast.Mod: operator.mod,
    ast.USub: operator.neg, ast.UAdd: operator.pos,
}

# Whitelisted callables — add a name here and nowhere else.
_FUNCS = {
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "sqrt": math.sqrt, "log": math.log, "log10": math.log10,
    "exp": math.exp, "fabs": math.fabs, "floor": math.floor,
    "ceil": math.ceil, "degrees": math.degrees, "radians": math.radians,
    "abs": abs, "round": round,
    "min": min, "max": max, "sum": sum, "pow": pow,
    "int": int, "float": float,
    "pi": math.pi, "e": math.e,  # treated as 0-arg Call OR as Name — covered below
}

_NAMES = ("pi", "e")


def eval_node(node):
    """Recursively evaluate an AST node with the whitelist above."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.BinOp):
        return _OPS[type(node.op)](eval_node(node.left), eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        return _OPS[type(node.op)](eval_node(node.operand))
    if isinstance(node, ast.Name):
        if node.id in _NAMES:
            return getattr(math, node.id)
        raise ValueError(f"Unsupported name: {node.id}")
    if isinstance(node, ast.List):
        return [eval_node(x) for x in node.elts]
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("Unsupported call target")
        fn_name = node.func.id
        if fn_name not in _FUNCS:
            raise ValueError(f"Unsupported function: {fn_name}")
        if len(node.args) > 2:
            raise ValueError("Too many arguments")
        fn = _FUNCS[fn_name]
        # pi/e used as zero-arg calls: pi() — accept and ignore the call shape.
        if callable(fn):
            if fn_name in ("pi", "e") and not node.args:
                return fn
            return fn(*[eval_node(a) for a in node.args])
        return fn  # non-callable (shouldn't reach here — _FUNCS are all callable)
    raise ValueError(f"Unsupported expression: {type(node).__name__}")


def evaluate(expression: str):
    """Parse + evaluate. Returns (result, result_str) or raises ValueError."""
    tree = ast.parse(expression, mode="eval")
    result = eval_node(tree.body)
    if isinstance(result, float) and (math.isinf(result) or math.isnan(result)):
        raise ValueError("Result is infinite or NaN")
    return result, str(result)
