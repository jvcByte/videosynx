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

from videosynx.render import find_font
from PIL import ImageFont

from videosynx.render.diagram import (
    _fit,
    _node_label_anchor,
    _text_rect,
    place_edge_label,
    render_diagram,
)
from videosynx.render.style import CLEAN, SKETCH, style_for
from videosynx.timeline import (
    DRAW_STEPS,
    SPF,
    LAYOUTS,
    SHAPES,
    Diagram,
    Edge,
    Explanation,
    Node,
    Phrase,
    Slide,
    parse_diagram,
    plan_explanation,
)

SIZE = (600, 200)
FONT = find_font()


def flow() -> Diagram:
    return Diagram(layout="row", nodes=(
        Node("a", "Request", "box"),
        Node("b", "Queue", "cylinder"),
        Node("c", "Worker", "person"),
    ), edges=(Edge("a", "b", "enqueue"), Edge("b", "c")))


def draw(diagram: Diagram | None = None, revealed: int = 3, style=SKETCH,
         step: int = DRAW_STEPS - 1) -> Image.Image:
    return render_diagram(diagram or flow(), SIZE, key="k", style=style,
                          revealed=revealed, font_path=FONT, step=step)


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
    from videosynx.timeline import Slide

    slide = Slide(heading="H", diagram=flow())
    assert slide.pieces == 3


def test_columns_win_over_a_diagram():
    """Both are renderable, and the renderer dispatches on columns first."""
    from videosynx.timeline import Slide

    slide = Slide(heading="H", columns=(("A", ("a",)), ("B", ("b",))), diagram=flow())
    assert slide.pieces == 2


def test_a_slide_with_nothing_to_show_reveals_nothing():
    from videosynx.timeline import Slide

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


# --------------------------------------------------------- explain columns

def column_lines(text: str, width: int = 1280, height: int = 720):
    """What the slide would actually put on screen for a one-column slide."""
    from videosynx.render.editor import Editor
    from videosynx.timeline import Slide

    editor = Editor(width, height, FONT)
    slide = Slide(heading="A Heading", columns=(("", (text,)),), revealed=1)
    heading_h = int(editor.f_title.size * 1.2)
    rule_gap = int(editor.f_title.size * 0.25) + 4 + int(editor.f_title.size * 0.75)
    budget = editor.caption_top - int(height * 0.07) - heading_h - rule_gap
    _, wrapped, box_h, _ = editor._layout_columns(slide, budget)
    return wrapped[0], box_h, budget


def test_a_long_column_keeps_every_word():
    """A sentence too long for two lines used to lose its tail.

    The wrap was capped at two lines per entry, so the rest was discarded --
    measured against the cap, never against the slide. On screen the sentence
    simply stopped, mid-clause, while the voice read the rest of it.

    This is the real one from a rendered lesson: at 720p, the free tier's
    resolution, its last two words were the ones being dropped.
    """
    text = ('End Transition: Don\'t end with a generic "That\'s all for this '
            'lesson." End by establishing the mental habit the next lessons can '
            'build on: before fine-tuning, diagnose the problem and choose the '
            'strategy.')
    lines, box_h, budget = column_lines(text)

    assert " ".join(lines).split() == text.split(), (
        "words were dropped from the column: "
        f"{set(text.split()) - set(' '.join(lines).split())}"
    )
    assert box_h <= budget, "the box was allowed to run past the caption band"


def test_a_slide_full_of_long_sentences_still_fits():
    """The worst case the parser actually produces: four long sentences in one
    column is what a slide holds, so it is the one that has to fit."""
    sentence = (
        "The engineer must weigh how much labelled data exists, how close the "
        "task is to what the model already knows, how much compute the run can "
        "afford, and how much the difference in quality is worth to the "
        "business making the decision."
    )
    lines, box_h, budget = column_lines(" ".join([sentence] * 4))
    assert box_h <= budget, f"{box_h} does not fit {budget}"


def test_words_are_kept_even_when_nothing_can_make_them_fit():
    """Past the smallest type there is nowhere left to shrink to.

    The guarantee that still holds is that no word is discarded. Overflowing
    the slide is visible and looks wrong; a sentence that stops mid-clause
    looks finished and is a lie, which is the failure this replaced.
    """
    text = " ".join(["transfer learning"] * 220)
    lines, _, _ = column_lines(text)
    assert " ".join(lines).split() == text.split(), "words were dropped"


def test_an_ordinary_column_is_left_at_full_size():
    """The fit must not shrink everything -- only what does not fit."""
    from videosynx.render.editor import Editor
    from videosynx.timeline import Slide

    editor = Editor(1280, 720, FONT)
    slide = Slide(heading="H", columns=(("", ("A short line.",)),), revealed=1)
    _, _, _, font = editor._layout_columns(slide, 400)
    assert font.size == editor.f_term.size


# -------------------------------------------------------------- determinism

DETERMINISM_SRC = str(Path(__file__).resolve().parents[1] / "src")

DETERMINISM_SCRIPT = f"""
import hashlib, sys
sys.path.insert(0, {DETERMINISM_SRC!r})
from videosynx.render import find_font
from PIL import ImageFont

from videosynx.render.diagram import (
    _fit,
    _node_label_anchor,
    _text_rect,
    place_edge_label,
    render_diagram,
)
from videosynx.render.style import SKETCH
from videosynx.timeline import Diagram, Edge, Node

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


# ---------------------------------------------------------------- collisions
# A label is drawn on the last pass so no shape can cover it. The other half
# of that bargain is that it covers whatever it lands on -- and when what it
# lands on is another line of text, the video shows two sentences printed over
# each other with neither of them readable.

def _text_of(diagram, boxes, font) -> list[tuple]:
    """Where each node's own name lands -- the same rule _draw_node uses."""
    rects = []
    for node in diagram.nodes:
        box = boxes.get(node.id)
        if box is None or not node.label:
            continue
        text = _fit(node.label, font, (box[2] - box[0]) * 0.88)
        rects.append(_text_rect(text, font, *_node_label_anchor(node, box, font),
                                pad=font.size * 0.15))
    return rects


def _place_all(diagram, size, font, *, lift: bool):
    """Every label's rect, in the order the renderer places them."""
    from PIL import ImageFont

    from videosynx.render.diagram import _cells, _edge_ends, SCALE, _shape_box

    width, height = size
    layer_font = ImageFont.truetype(FONT, SKETCH.label_size * SCALE)
    box = (0.0, 0.0, width * SCALE, height * SCALE)
    cells = _cells(diagram, box)
    boxes = {n.id: _shape_box(c, n, layer_font)
             for n, c in zip(diagram.nodes, cells)}

    occupied = _text_of(diagram, boxes, layer_font)
    for edge in diagram.edges:
        if edge.source not in boxes or edge.target not in boxes:
            continue
        ends = _edge_ends(boxes[edge.source], boxes[edge.target])
        text = _fit(edge.label, layer_font, width * SCALE * 0.5)
        if lift:
            _, _, rect = place_edge_label(text, layer_font, ends, boxes,
                                          occupied, height * SCALE)
        else:
            mx = (ends[0][0] + ends[1][0]) / 2
            my = (ends[0][1] + ends[1][1]) / 2 - layer_font.size * 0.85
            rect = _text_rect(text, layer_font, mx, my, layer_font.size * 0.15)
        occupied.append(rect)
    return occupied


def _collisions(rects) -> list[tuple]:
    return [
        (a, b)
        for i, a in enumerate(rects)
        for b in rects[i + 1:]
        if a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
    ]


def fanned() -> Diagram:
    """Three arrows leaving one shape, which the row layout draws straight
    over the shapes between them. Their midpoints -- where the labels go --
    land inside a shape, on the name already written there."""
    return Diagram(
        layout="row",
        nodes=(
            Node(id="list", label="List Container", shape="box", repeat=1),
            Node(id="a", label="First Item", shape="round", repeat=1),
            Node(id="b", label="Second Item", shape="round", repeat=1),
            Node(id="c", label="Third Item", shape="round", repeat=1),
        ),
        edges=(
            Edge(source="list", target="a", label="index 0"),
            Edge(source="list", target="b", label="index 1"),
            Edge(source="list", target="c", label="index 2"),
        ),
    )


# The size a 720p render actually gives a diagram: 88% of the width and 46% of
# the height. The proportions are the point -- a narrower, taller box leaves
# room in the gaps and hides the fault completely.
SLIDE = (int(1280 * 0.88), int(720 * 0.46))


def test_the_old_placement_really_did_print_on_itself():
    """Without this the test below proves nothing: it has to fail on the
    arrangement it was written for."""
    font = ImageFont.truetype(FONT, SKETCH.label_size * 2)
    assert _collisions(_place_all(fanned(), SLIDE, font, lift=False))


def test_labels_are_moved_clear_rather_than_printed_over():
    font = ImageFont.truetype(FONT, SKETCH.label_size * 2)
    assert not _collisions(_place_all(fanned(), SLIDE, font, lift=True))


def test_a_chain_of_two_shapes_is_left_alone():
    """The other half: a diagram that was already readable must not have its
    labels thrown out of the row for no reason."""
    diagram = Diagram(
        layout="row",
        nodes=(
            Node(id="a", label="Variable Name", shape="box", repeat=1),
            Node(id="b", label="Value in Memory", shape="round", repeat=1),
            Node(id="c", label="Data Type", shape="document", repeat=1),
        ),
        edges=(
            Edge(source="a", target="b", label="points to"),
            Edge(source="b", target="c", label="classified as"),
        ),
    )
    font = ImageFont.truetype(FONT, SKETCH.label_size * 2)
    occupied = _place_all(diagram, SLIDE, font, lift=True)
    # Every label still inside the layer, and none on another.
    assert not _collisions(occupied)
    assert all(0 <= r[1] and r[3] <= SLIDE[1] * 2 for r in occupied)


# ------------------------------------------------------------------ animation
# A newly revealed arrow draws itself in over DRAW_STEPS sub-steps. The
# renderer never sees a frame index -- it is handed a state and asked to draw
# it -- so motion has to be spelled as a run of *distinct* states. That is
# what makes the failure mode here specific and silent: a sub-step that
# renders identically to its neighbour costs a repaint and an encoded frame
# while showing the viewer nothing at all. The picture stands still and every
# counter says it is moving.


def test_every_sub_step_looks_different():
    """Eight states, eight pictures. If two coincide, the motion has a stall."""
    frames = {draw(revealed=2, step=s).tobytes() for s in range(DRAW_STEPS)}
    assert len(frames) == DRAW_STEPS


def test_an_arrow_grows_rather_than_shrinking():
    drawn = [count_near(draw(revealed=2, step=s), SKETCH.ink)
             for s in range(DRAW_STEPS)]
    assert drawn == sorted(drawn), "the arrow lost ink as it was drawn"
    assert drawn[0] < drawn[-1], "the first sub-step already drew the whole arrow"


def test_a_settled_step_is_the_default():
    """Every caller that wants a still, and every test above, still gets one."""
    assert draw(revealed=2).tobytes() == draw(revealed=2, step=DRAW_STEPS - 1).tobytes()


def test_a_node_with_nothing_to_point_at_does_not_animate():
    """The first node has no earlier shape to be joined to.

    At revealed=1 the arriving node is "a", whose only edge goes *forward* to
    a node that is not on screen yet. There is nothing to draw, so the steps
    must render identically -- the renderer honouring the same rule the plan
    uses when it decides not to animate.
    """
    assert draw(revealed=1, step=0).tobytes() == draw(revealed=1, step=DRAW_STEPS - 1).tobytes()


def phrase(text: str, frames: int = 45) -> Phrase:
    """A sentence with silent audio of a known length."""
    return Phrase(text, np.zeros(frames * SPF, dtype=np.float32))


def steps_of(diagram: Diagram | None, sentences: tuple[str, ...]) -> list[int]:
    spans, _, _, _ = plan_explanation(Explanation(
        slide=Slide(heading="Flow", diagram=diagram),
        phrases=tuple(phrase(s) for s in sentences),
    ))
    return [s.state.slide.step for s in spans if s.state.slide is not None]


def test_a_new_arrow_walks_the_whole_ramp_once():
    """The plan is what produces the motion; the renderer only follows it.

    Asserted as the full ramp rather than as "more than one value", which a
    two-state pop would also satisfy. Sentence 1 settles (the first node has
    nothing behind it to be joined to), then each of the two sentences that
    reveals a node walks 0..7 in order, and the trailing silence holds.

    This is the assertion that caught a rounding bug: the sub-step boundaries
    were floored while the step itself was floored separately, so both landed
    on step 0 and the ramp read 0, 0, 1, 2... -- a still frame at the start of
    every sentence, which "more than one distinct step" never noticed.
    """
    steps = steps_of(flow(), ("Heading.", "A.", "B.", "C."))
    # Collapse runs: consecutive spans sharing a step are one visible moment.
    ramp = [s for i, s in enumerate(steps) if i == 0 or steps[i - 1] != s]
    assert ramp == [DRAW_STEPS - 1] + list(range(DRAW_STEPS)) * 2


def test_a_diagram_with_no_arrows_asks_for_no_animation():
    """Nothing to draw means nothing to repaint.

    Eight sub-steps here would be eight full repaints and eight encoded
    frames per sentence, all rendering identically.
    """
    lone = Diagram(layout="row", nodes=(Node("a", "A"), Node("b", "B"), Node("c", "C")))
    steps = steps_of(lone, ("Heading.", "A.", "B.", "C."))
    assert set(steps) == {DRAW_STEPS - 1}
