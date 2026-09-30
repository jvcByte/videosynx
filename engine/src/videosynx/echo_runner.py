"""Run a snippet the way a REPL would, echoing expression results.

A teaching example like::

    letters = ["a", "b", "c"]
    letters[0]   # returns "a"

prints nothing when run as a script, because a bare expression statement is
evaluated and discarded. In a REPL it echoes ``'a'``, and that echo is the
whole point of the example. This rewrites the module so each top-level
expression statement reports its value, which is what the reader of a tutorial
sees when they type it themselves.

Assignments, imports, loops and calls to ``print`` are left alone, and the
statement's original line numbers are preserved so tracebacks still point at
the real line.
"""

from __future__ import annotations

import ast
import sys


def _echo(value) -> None:
    """Print a value the way a REPL would -- and stay quiet about None."""
    if value is not None:
        print(repr(value))


class _EchoExpressions(ast.NodeTransformer):
    def visit_Expr(self, node: ast.Expr) -> ast.AST:
        value = node.value
        # A docstring is an expression statement, but echoing it would be noise.
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return node
        # print(...) and friends already produce output; echoing None is not
        # what a REPL does, so leave them be.
        if isinstance(value, ast.Call) and isinstance(value.func, ast.Name):
            if value.func.id == "print":
                return node
        wrapper = ast.Expr(
            value=ast.Call(
                func=ast.Name(id="_echo", ctx=ast.Load()),
                args=[value],
                keywords=[],
            )
        )
        return ast.copy_location(wrapper, node)


def rewrite(source: str, filename: str) -> ast.Module:
    tree = _EchoExpressions().visit(ast.parse(source, filename=filename))
    ast.fix_missing_locations(tree)
    return tree


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: echo_runner.py SNIPPET.py", file=sys.stderr)
        return 2
    path = argv[1]
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    namespace = {"__name__": "__main__", "__file__": path, "_echo": _echo}
    try:
        exec(compile(rewrite(source, path), path, "exec"), namespace)
    except SystemExit:
        raise
    except BaseException:
        import traceback

        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
