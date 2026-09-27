"""VS Code Dark+ palette and Pygments token mapping."""

from __future__ import annotations

from functools import lru_cache

from pygments import lex
from pygments.lexers import get_lexer_by_name
from pygments.token import Token

# Editor chrome
BG = "#1e1e1e"
TITLE_BG = "#3c3c3c"
TAB_BG = "#252526"
PANEL_BG = "#181818"
STATUS_BG = "#007acc"
BORDER = "#2b2b2b"
GUTTER_FG = "#858585"
FG = "#d4d4d4"
CURSOR = "#aeafad"
ERROR_FG = "#f48771"

# Traffic lights
DOT_COLORS = ("#ff5f57", "#febc2e", "#28c840")

# Token class -> colour. Matched by walking up the token's parent chain.
TOKEN_COLORS = {
    Token.Keyword: "#569cd6",
    Token.Keyword.Namespace: "#c586c0",
    Token.Operator.Word: "#c586c0",
    Token.Name.Builtin: "#dcdcaa",
    Token.Name.Function: "#dcdcaa",
    Token.Name.Class: "#4ec9b0",
    Token.Name.Decorator: "#dcdcaa",
    Token.Name: "#9cdcfe",
    Token.String: "#ce9178",
    Token.Comment: "#6a9955",
    Token.Number: "#b5cea8",
}


@lru_cache(maxsize=4096)
def color_for(token) -> str:
    """Resolve a token to a colour by walking its parent chain."""
    t = token
    while t is not None:
        if t in TOKEN_COLORS:
            return TOKEN_COLORS[t]
        t = t.parent
    return FG


def highlight(code: str, language: str = "python") -> list[list[tuple[str, str]]]:
    """Tokenize *code* once into per-line ``(text, colour)`` spans.

    Tokenizing the whole snippet up front -- rather than re-lexing the visible
    prefix on every frame -- is what stops multi-line constructs (triple-quoted
    strings, continuation lines) from changing colour mid-typing and making the
    video visibly reflow.
    """
    lexer = get_lexer_by_name(language, stripnl=False, ensurenl=False)
    lines: list[list[tuple[str, str]]] = [[]]
    for token, text in lex(code, lexer):
        colour = color_for(token)
        for i, part in enumerate(text.split("\n")):
            if i:
                lines.append([])
            if part:
                lines[-1].append((part, colour))

    # Pygments normalises leading/trailing newlines, so the token stream can
    # have fewer lines than the source. Left uncorrected, a snippet's trailing
    # blank line disappears and the cursor sits on the wrong row. Reconcile
    # against the source rather than trusting the lexer.
    expected = code.count("\n") + 1
    del lines[expected:]
    while len(lines) < expected:
        lines.append([])
    return lines


def truncate_line(spans: list[tuple[str, str]], n_chars: int) -> list[tuple[str, str]]:
    """Chop a line's spans down to the first *n_chars* characters."""
    if n_chars <= 0:
        return []
    out: list[tuple[str, str]] = []
    used = 0
    for text, colour in spans:
        if used >= n_chars:
            break
        take = min(len(text), n_chars - used)
        out.append((text[:take], colour))
        used += take
    return out


def visible_lines(
    spans: list[list[tuple[str, str]]], code: str, n_chars: int
) -> list[list[tuple[str, str]]]:
    """The spans for the first *n_chars* characters of *code*, as lines.

    This is how typing is rendered: highlight once, then slice. Slicing is O(1)
    in the number of frames rather than re-lexing per frame.
    """
    prefix = code[:n_chars]
    line_idx = prefix.count("\n")
    col = len(prefix) - (prefix.rfind("\n") + 1)
    lines = [list(spans[i]) for i in range(min(line_idx, len(spans)))]
    if line_idx < len(spans):
        lines.append(truncate_line(spans[line_idx], col))
    return lines
