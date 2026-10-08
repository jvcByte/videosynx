"""How a concept slide is drawn.

``sketch`` is a hand-drawn pencil look on paper; ``clean`` is the flat dark
panel the engine shipped with. Style is resolved once per render and handed to
the Editor, so a video has one look throughout rather than one per slide.

The hand-drawn feel comes from the *strokes* -- wobbling lines drawn twice --
not from the typeface. That is deliberate: it means sketch slides need no font
beyond the monospace face already loaded, so there is nothing new to install
and nothing that can silently fall back to a proportional font.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Style:
    name: str
    paper: str        # what a slide sits on
    ink: str          # primary stroke and body text
    dim: str          # secondary text, and the closing note
    accent: str       # the rule under the heading, and shape titles
    panel: str        # fill behind a shape
    panel_line: str   # a drawn shape's outline
    ghost: str        # the outline of a shape not revealed yet
    # The only two colours here that mean something rather than decorate.
    # `ink` and `dim` say how loud a thing is; these say whether it is all
    # right, which is the whole of what a picture of a system going wrong has
    # to communicate. Chosen dark enough to read on `paper` and to look like
    # the same hand drew them.
    warn: str         # busy, strained, filling up
    bad: str          # overloaded, failed, the thing that broke
    jitter: float     # hand-drawn wobble in px; 0 draws ruled
    stroke: int       # stroke width in px, before supersampling
    label_size: int   # diagram label size


SKETCH = Style(
    name="sketch",
    paper="#f4f1e8",
    ink="#2b2b2b",
    dim="#6f6a60",
    accent="#c2410c",
    panel="#fffdf7",
    panel_line="#3f3b35",
    ghost="#cdc7ba",
    warn="#a16207",
    bad="#b91c1c",
    jitter=1.7,
    stroke=3,
    label_size=22,
)

CLEAN = Style(
    name="clean",
    paper="#101418",
    ink="#ffffff",
    dim="#7d8590",
    accent="#007acc",
    panel="#1a2027",
    panel_line="#3d444d",
    ghost="#2a3138",
    warn="#d29922",
    bad="#f85149",
    jitter=0.0,
    stroke=2,
    label_size=20,
)

STYLES = {style.name: style for style in (SKETCH, CLEAN)}
DEFAULT_STYLE = "sketch"


def style_for(name: str | None) -> Style:
    """Resolve a style name, falling back rather than raising.

    An unknown style is a cosmetic problem. Failing a render over it -- after
    the code and narration have already been generated and paid for -- would
    not be worth the job.
    """
    return STYLES.get(name or "", SKETCH)
