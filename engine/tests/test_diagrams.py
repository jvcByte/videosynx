"""Diagram specs, layout, and the drawing itself.

The property most worth protecting here is that a diagram draws the *same*
every time. A hand-drawn look invites an unseeded RNG, and an unseeded RNG
would make two renders of one video differ in ways nobody could reproduce.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageColor

from pycoding.render import find_font
from pycoding.render.diagram import render_diagram
from pycoding.render.style import CLEAN, SKETCH, style_for
from pycoding.timeline import LAYOUTS, SHAPES, Diagram, Edge, Node, parse_diagram

SIZE = (600, 200)
FONT = find_font()


def flow() -> Diagram:
    return Diagram(layout="row", nodes=(
        Node("a", "Request", "box"),
        Node("b", "Queue", "cylinder"),
        Node("c", "Worker", "person"),
    ), edges=(Edge("a", "b", "enqueue"), Edge("b", "c")))


def draw(diagram: Diagram | None = None, revealed: int = 3, style=SKETCH) -> Image.Image:
    return render_diagram(diagram or flow(), SIZE, key="k", style=style,
                          revealed=revealed, font_path=FONT)


# ------------------------------------------------------------------- parsing

def test_parse_diagram_reads_a_well_formed_spec():
    diagram = parse_diagram({
        "layout": "row",
        "nodes": [{"id": "a", "label": "Request", "shape": "box"},
                  {"id": "b", "label": "Queue", "shape": "cylinder"}],
        "edges": [["a", "b", "enqueue"]],
    })
    assert diagram.layout == "row"
    assert [n.id for n in diagram.nodes] == ["a", "b"]
    assert diagram.nodes[1].shape == "cylinder"
    assert diagram.edges[0] == Edge("a", "b", "enqueue")


def test_parse_diagram_falls_back_on_an_unknown_shape_or_layout():
    """A shape nobody implemented should draw a box, not crash the render."""
    diagram = parse_diagram({"layout": "spiral",
                             "nodes": [{"id": "a", "shape": "hexagon"}]})
    assert diagram.layout == "row"
    assert diagram.nodes[0].shape == "box"


def test_parse_diagram_drops_a_repeated_id():
    """Edges address nodes by id, so a duplicate makes the drawing ambiguous."""
    diagram = parse_diagram({"nodes": [{"id": "a", "label": "one"},
                                       {"id": "a", "label": "two"}]})
    assert [n.label for n in diagram.nodes] == ["one"]


def test_parse_diagram_drops_an_edge_to_nowhere():
    diagram = parse_diagram({"nodes": [{"id": "a"}], "edges": [["a", "ghost"], ["a", "a"]]})
    assert diagram.edges == (Edge("a", "a"),)


def test_parse_diagram_needs_at_least_one_node():
    assert parse_diagram({"nodes": []}) is None
    assert parse_diagram({"edges": [["a", "b"]]}) is None
    assert parse_diagram(None) is None
    assert parse_diagram("a diagram, honestly") is None


def test_parse_diagram_bounds_repeat():
    """A repeat count is a drawing hint, and a model can send any number."""
    for given, expected in ((3, 3), (0, 1), (-4, 1), (99, 5), ("x", 1), (None, 1)):
        diagram = parse_diagram({"nodes": [{"id": "a", "repeat": given}]})
        assert diagram.nodes[0].repeat == expected, given


def test_every_declared_shape_and_layout_is_drawable():
    """The vocabulary is advertised to the model, so all of it must work."""
    for shape in SHAPES:
        draw(Diagram(layout="row", nodes=(Node("a", shape, shape),)))
    for layout in LAYOUTS:
        draw(Diagram(layout=layout, nodes=(
            Node("a", "A", "box"), Node("b", "B", "box"), Node("c", "C", "box"),
        ), edges=(Edge("a", "b"), Edge("b", "c"))))


# ------------------------------------------------------------------- pieces

def test_a_diagram_slide_reveals_one_node_per_sentence():
    from pycoding.timeline import Slide

    slide = Slide(heading="H", diagram=flow())
    assert slide.pieces == 3


def test_columns_win_over_a_diagram():
    """Both are renderable, and the renderer dispatches on columns first."""
    from pycoding.timeline import Slide

    slide = Slide(heading="H", columns=(("A", ("a",)), ("B", ("b",))), diagram=flow())
    assert slide.pieces == 2


def test_a_slide_with_nothing_to_show_reveals_nothing():
    from pycoding.timeline import Slide

    assert Slide(heading="H").pieces == 0


# ------------------------------------------------------------------ drawing

def test_a_diagram_draws_something():
    assert draw().tobytes() != Image.new("RGB", SIZE, SKETCH.paper).tobytes()


def test_an_unrevealed_node_still_shows_its_outline():
    """Ghosting is the point: the viewer sees the shape of what is coming."""
    blank = Image.new("RGB", SIZE, SKETCH.paper)
    assert draw(revealed=0).tobytes() != blank.tobytes()


def test_revealing_a_node_changes_the_frame():
    assert draw(revealed=1).tobytes() != draw(revealed=3).tobytes()


def test_a_diagram_with_no_nodes_is_just_the_background():
    empty = Diagram(layout="row", nodes=())
    assert draw(empty).tobytes() == Image.new("RGB", SIZE, SKETCH.paper).tobytes()


def test_the_output_is_exactly_the_requested_size():
    """Frame bytes must match the sink's expectation or ffmpeg rejects them."""
    for size in ((600, 200), (960, 300), (1690, 497)):
        image = render_diagram(flow(), size, key="k", style=SKETCH, revealed=3,
                               font_path=FONT)
        assert image.mode == "RGB"
        assert image.size == size


def count_near(image: Image.Image, colour: str, tolerance: int = 24) -> int:
    """Pixels close to *colour*.

    Not an exact match: the layer is scaled down with LANCZOS, which blends
    text edges into the paper. A tolerance is what makes this measurable.
    """
    target = np.array(ImageColor.getrgb(colour))
    return int((np.abs(np.asarray(image).astype(int) - target).max(axis=2) < tolerance).sum())


def label_pixels(text: str) -> int:
    """Label pixels for a two-node diagram whose only arrow carries *text*."""
    diagram = Diagram(layout="row",
                      nodes=(Node("a", "A", "box"), Node("b", "B", "box")),
                      edges=(Edge("a", "b", text),))
    return count_near(draw(diagram, revealed=2), SKETCH.dim)


def test_a_long_edge_label_is_not_truncated_to_fit_its_arrow():
    """An arrow between two close shapes is only a few dozen pixels long.

    Sizing the label to the arrow turns "enqueue" into "enqu…", which is what
    the first version of this did. Labels are sized to the diagram instead, so
    a longer one simply renders wider. (They are drawn on the last pass for
    the same reason: a wide label overlaps the shapes it sits between.)

    The baseline -- antialiased stroke edges, which land on tones close to the
    label grey -- is identical in both renders, so the difference isolates the
    label rather than the drawing.
    """
    short, long_ = label_pixels("go"), label_pixels("enqueue the request")
    assert long_ - short > 300, (
        f"a long label added only {long_ - short} pixels over a short one, "
        "which means it was cut down to the width of its arrow"
    )


def test_both_styles_draw():
    assert draw(style=CLEAN).tobytes() != draw(style=SKETCH).tobytes()


def test_an_unknown_style_name_falls_back_rather_than_raising():
    assert style_for("watercolour") is SKETCH
    assert style_for(None) is SKETCH
    assert style_for("clean") is CLEAN


def test_a_long_label_does_not_overflow_its_shape():
    """A single word wider than its shape would otherwise run straight out."""
    wide = Diagram(layout="row", nodes=(
        Node("a", "a" * 120, "box"), Node("b", "B", "box"), Node("c", "C", "box"),
    ))
    assert draw(wide).size == SIZE


# -------------------------------------------------------------- determinism

DETERMINISM_SRC = str(Path(__file__).resolve().parents[1] / "src")

DETERMINISM_SCRIPT = f"""
import hashlib, sys
sys.path.insert(0, {DETERMINISM_SRC!r})
from pycoding.render import find_font
from pycoding.render.diagram import render_diagram
from pycoding.render.style import SKETCH
from pycoding.timeline import Diagram, Edge, Node

diagram = Diagram(layout="cycle", nodes=(
    Node("a", "Collect", "box"), Node("b", "Train", "cylinder"),
    Node("c", "Evaluate", "round"), Node("d", "Deploy", "cloud", repeat=3),
), edges=(Edge("a", "b", "start"), Edge("b", "c"), Edge("c", "d"), Edge("d", "a")))
image = render_diagram(diagram, (600, 200), key="det", style=SKETCH,
                       revealed=4, font_path=find_font())
print(hashlib.sha256(image.tobytes()).hexdigest())
"""


def test_a_diagram_is_identical_across_processes():
    """The wobble is seeded from hashlib, never from Python's hash().

    hash() is salted per process, so a jitter seeded from it would redraw the
    same slide differently on every run -- invisible to an in-process test,
    which is why this one starts two interpreters under different hash seeds.
    """
    def render_with_hash_seed(seed: str) -> str:
        result = subprocess.run(
            [sys.executable, "-c", DETERMINISM_SCRIPT],
            env={**os.environ, "PYTHONHASHSEED": seed},
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()

    first, second = render_with_hash_seed("1"), render_with_hash_seed("2")
    assert first == second


def test_rendering_twice_in_one_process_gives_the_same_bytes():
    assert draw().tobytes() == draw().tobytes()
