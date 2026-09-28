"""Draw a node-and-edge diagram.

Shapes are drawn by hand here because Pillow offers only rectangles, ellipses,
polygons and arcs -- and, more importantly, because ImageDraw's geometry is not
antialiased (only text is). Everything is drawn into an oversized layer and
scaled down with LANCZOS, which is what stops a diagonal arrow or a cylinder's
curve from looking stair-stepped.

The wobble that makes a stroke look hand-drawn comes from a seeded RNG keyed on
the node's own id, never from Python's ``hash()``. ``hash()`` is salted per
process, so using it would make two renders of the same video differ in ways
nobody could reproduce or explain.
"""

from __future__ import annotations

import hashlib
import math
from random import Random

from PIL import Image, ImageDraw, ImageFont

from ..timeline import LAYOUTS, SHAPES, Diagram, Edge, Node
from .style import Style

# Drawn at this multiple and scaled back down, to antialias the geometry.
SCALE = 3

# These read as icons: the label goes underneath, so the shape gives up room.
ICON_SHAPES = {"person", "cloud"}

# A node in a wide cell (a column layout, say) must not stretch into a bar.
MAX_ASPECT = 2.6


def seed_for(text: str) -> int:
    """A stable seed for *text*, across processes and machines."""
    return int.from_bytes(hashlib.blake2b(text.encode(), digest_size=8).digest(), "big")


class Pen:
    """Strokes, wobbling if the style asks for it.

    One pen per shape, seeded from that shape's id, so adding a node does not
    restyle the ones already there.
    """

    def __init__(self, draw: ImageDraw.ImageDraw, style: Style, seed: int, scale: int):
        self.draw = draw
        self.style = style
        self.rng = Random(seed)
        self.width = max(1, round(style.stroke * scale))
        self.jitter = style.jitter * scale

    def _wobble(self, point):
        jitter = self.jitter
        if not jitter:
            return point
        return (point[0] + self.rng.uniform(-jitter, jitter),
                point[1] + self.rng.uniform(-jitter, jitter))

    def stroke(self, points, *, ink=None, width=None, close=False, passes=2):
        """Draw a polyline. Twice, when the style is hand-drawn."""
        ink = ink or self.style.ink
        width = width or self.width
        for _ in range(1 if not self.jitter else passes):
            pts = [self._wobble(p) for p in points]
            if len(pts) < 2:
                continue
            if close:
                pts.append(pts[0])
            self.draw.line(pts, fill=ink, width=width, joint="curve")


# --------------------------------------------------------------------- shapes

def _rounded_points(box, frac: float = 0.26, steps: int = 3):
    """A rectangle with genuinely rounded corners, as a polyline.

    Drawn as points rather than Pillow's rounded_rectangle so the hand-drawn
    wobble applies to it like every other stroke.
    """
    x0, y0, x1, y1 = box
    r = max(1.0, min(x1 - x0, y1 - y0) * frac)
    corners = (
        ((x1 - r, y0 + r), -90, 0),    # top right
        ((x1 - r, y1 - r), 0, 90),     # bottom right
        ((x0 + r, y1 - r), 90, 180),   # bottom left
        ((x0 + r, y0 + r), 180, 270),  # top left
    )
    points = []
    for (cx, cy), start, end in corners:
        for i in range(steps + 1):
            a = math.radians(start + (end - start) * i / steps)
            points.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return points


def _shape(pen: Pen, box, shape: str, fill: str, ink: str) -> None:
    """Draw one silhouette, filled then outlined."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    d = pen.draw

    if shape == "round":
        pts = _rounded_points(box)
        d.polygon(pts, fill=fill)
        pen.stroke(pts, ink=ink, close=True)

    elif shape == "cylinder":
        eh = h * 0.26
        d.rectangle([x0, y0 + eh / 2, x1, y1 - eh / 2], fill=fill)
        d.ellipse([x0, y1 - eh, x1, y1], fill=fill)
        d.ellipse([x0, y0, x1, y0 + eh], fill=fill)
        pen.stroke([(x0, y0 + eh / 2), (x0, y1 - eh / 2)], ink=ink)
        pen.stroke([(x1, y0 + eh / 2), (x1, y1 - eh / 2)], ink=ink)
        d.arc([x0, y1 - eh, x1, y1], 0, 180, fill=ink, width=pen.width)
        d.arc([x0, y0, x1, y0 + eh], 0, 360, fill=ink, width=pen.width)

    elif shape == "cloud":
        base = y1 - h * 0.26
        puffs = (
            (x0 + w * 0.00, base - h * 0.44, x0 + w * 0.42, base + h * 0.08),
            (x0 + w * 0.20, base - h * 0.78, x0 + w * 0.74, base - h * 0.02),
            (x0 + w * 0.54, base - h * 0.54, x0 + w * 1.00, base + h * 0.08),
        )
        for puff in puffs:
            d.ellipse(list(puff), fill=fill)
        d.rectangle([x0 + w * 0.18, base - h * 0.16, x1 - w * 0.18, base], fill=fill)
        for puff in puffs:
            d.arc(list(puff), 0, 360, fill=ink, width=pen.width)
        pen.stroke([(x0 + w * 0.20, base), (x1 - w * 0.20, base)], ink=ink)

    elif shape == "document":
        wave = h * 0.16
        d.rectangle([x0, y0, x1, y1 - wave], fill=fill)
        pts = [(x0, y1 - wave)]
        steps = 14
        for i in range(steps + 1):
            t = i / steps
            pts.append((x0 + w * t,
                        y1 - wave * 0.5 + math.sin(t * math.tau) * wave * 0.5))
        pen.stroke(pts, ink=ink)
        pen.stroke([(x0, y0), (x1, y0), (x1, y1 - wave)], ink=ink)
        pen.stroke([(x0, y0), (x0, y1 - wave)], ink=ink)

    elif shape == "person":
        head_r = min(w, h) * 0.30
        cx = (x0 + x1) / 2
        head = [cx - head_r, y0, cx + head_r, y0 + head_r * 2]
        d.ellipse(head, fill=fill)
        d.arc(head, 0, 360, fill=ink, width=pen.width)
        # The shoulders are the upper half of an ellipse whose top half ends
        # exactly at y1, so the figure stays inside the box it was given. Kept
        # as wide as the box, or an arrow meeting this shape stops in mid-air.
        top = y0 + head_r * 2.3
        body = [cx - w * 0.46, top, cx + w * 0.46, 2 * y1 - top]
        d.pieslice(body, 180, 360, fill=fill)
        d.arc(body, 180, 360, fill=ink, width=pen.width)

    elif shape == "stack":
        offset = min(w, h) * 0.18
        for i in (2, 1, 0):
            shift = offset * i
            inner = (x0 + shift, y0 + shift, x1 - shift, y1 - shift)
            pts = _rounded_points(inner, 0.16)
            d.polygon(pts, fill=fill)
            pen.stroke(pts, ink=ink, close=True)

    else:  # "box", and anything unrecognised
        d.rectangle([x0, y0, x1, y1], fill=fill)
        pen.stroke([(x0, y0), (x1, y0), (x1, y1), (x0, y1)], ink=ink, close=True)


# --------------------------------------------------------------------- layout

def _cells(diagram: Diagram, box) -> list[tuple[float, float, float, float]]:
    """One cell per node, in the order the nodes are declared."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    n = len(diagram.nodes)
    layout = diagram.layout if diagram.layout in LAYOUTS else "row"

    if layout == "row":
        return [(x0 + w * i / n, y0, x0 + w * (i + 1) / n, y1) for i in range(n)]

    if layout in ("column", "stack"):
        # "stack" is a column that overlaps, so the layers read as a pile.
        overlap = 0.22 if layout == "stack" else 0.0
        step = h / (n - overlap * (n - 1)) if n > 1 else h
        return [(x0, y0 + step * i * (1 - overlap), x1, y0 + step * i * (1 - overlap) + step)
                for i in range(n)]

    if layout == "cycle":
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        rx, ry = w * 0.32, h * 0.31
        cw, ch = w * 0.30, h * 0.36
        cells = []
        for i in range(n):
            angle = -math.pi / 2 + math.tau * i / n  # start at the top
            px, py = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
            cells.append((px - cw / 2, py - ch / 2, px + cw / 2, py + ch / 2))
        return cells

    if layout == "tree" and n > 1:
        # The root spans the top; everything else sits in one row beneath it.
        leaves = n - 1
        cells = [(x0 + w * 0.25, y0, x1 - w * 0.25, y0 + h / 2)]
        for i in range(leaves):
            cells.append((x0 + w * i / leaves, y0 + h / 2,
                          x0 + w * (i + 1) / leaves, y1))
        return cells

    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return [(x0 + w * (i % cols) / cols, y0 + h * (i // cols) / rows,
             x0 + w * (i % cols + 1) / cols, y0 + h * (i // cols + 1) / rows)
            for i in range(n)]


def _centre(box) -> tuple[float, float]:
    return ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)


def _fit(text: str, font, limit: float) -> str:
    """Shorten *text* until it fits, rather than letting it run out of a shape."""
    if font.getlength(text) <= limit:
        return text
    out = text
    while out and font.getlength(out + "…") > limit:
        out = out[:-1]
    return out + "…" if out else ""


def _shape_box(cell, node: Node, font) -> tuple[float, float, float, float]:
    """Where the silhouette sits inside its cell: centred, never stretched."""
    x0, y0, x1, y1 = cell
    w, h = x1 - x0, y1 - y0
    inner_w = max(font.size * 1.4, w * 0.80)
    inner_h = max(font.size * 1.6, h * 0.72)
    cx = (x0 + x1) / 2

    if node.shape in ICON_SHAPES:
        # The label sits underneath, so the silhouette only takes the top part.
        room = max(font.size * 1.4, h * 0.72 - font.size * 1.8)
        side = max(font.size * 1.2, min(inner_w, room))
        top = y0 + (h - side) / 2 - font.size * 0.45
        return (cx - side / 2, top, cx + side / 2, top + side)

    # Wide enough for the label, but capped so a wide cell cannot stretch it.
    wanted = min(font.getlength(node.label) + font.size * 2.4, inner_w)
    box_w = min(max(wanted, inner_w), inner_h * MAX_ASPECT)
    top = y0 + (h - inner_h) / 2
    return (cx - box_w / 2, top, cx + box_w / 2, top + inner_h)


def _draw_node(pen: Pen, node: Node, box, font, style: Style,
               fill: str, ink: str) -> None:
    copies = max(1, node.repeat)
    front = box

    if copies > 1:
        step = min(box[2] - box[0], box[3] - box[1]) * 0.09
        shift = step * (copies - 1)
        front = (box[0], box[1], box[2] - shift, box[3] - shift)
        # The copies behind are plain cards: repeating a document would smear
        # its wavy edge across the card behind it.
        for i in reversed(range(1, copies)):
            d = step * i
            _shape(pen, (front[0] + d, front[1] + d, front[2] + d, front[3] + d),
                   "round", fill, ink)

    _shape(pen, front, node.shape, fill, ink)

    if not node.label:
        return
    if node.shape in ICON_SHAPES:
        anchor = ((front[0] + front[2]) / 2, front[3] + font.size * 0.8)
    else:
        anchor = _centre(front)
    text = _fit(node.label, font, (front[2] - front[0]) * 0.88)
    pen.draw.text(anchor, text, font=font, fill=ink, anchor="mm")


# ---------------------------------------------------------------------- edges

def _arrowhead(pen: Pen, start, tip, ink: str) -> None:
    angle = math.atan2(tip[1] - start[1], tip[0] - start[0])
    size = pen.width * 5.5
    for spread in (math.radians(150), math.radians(-150)):
        pen.stroke(
            [tip, (tip[0] + size * math.cos(angle + spread),
                   tip[1] + size * math.sin(angle + spread))],
            ink=ink, passes=1,
        )


def _edge_ends(a, b) -> tuple[tuple[float, float], tuple[float, float]]:
    """Where an arrow leaves *a* and lands on *b*.

    It attaches to whichever pair of facing sides the two shapes actually
    share, so the arrow meets an edge rather than a centre.
    """
    ax, ay = _centre(a)
    bx, by = _centre(b)
    dx, dy = bx - ax, by - ay
    if abs(dx) >= abs(dy):
        return (a[2] if dx > 0 else a[0], ay), (b[0] if dx > 0 else b[2], by)
    return (ax, a[3] if dy > 0 else a[1]), (bx, b[1] if dy > 0 else b[3])


def _draw_edge(pen: Pen, ends, ink: str) -> None:
    start, tip = ends
    pen.stroke([start, tip], ink=ink)
    _arrowhead(pen, start, tip, ink)


def _draw_edge_label(pen: Pen, ends, edge: Edge, font, style: Style,
                     limit: float) -> None:
    """The label goes on the last pass, so no shape can be drawn over it.

    It is sized to *limit*, not to the length of its own arrow: an arrow
    between two close shapes is a few dozen pixels long, and fitting the label
    to that turns "enqueue" into "enqu…". Letting it run wider than the gap and
    drawing it last is what keeps the word readable.
    """
    start, tip = ends
    mx, my = (start[0] + tip[0]) / 2, (start[1] + tip[1]) / 2
    text = _fit(edge.label, font, limit)
    # Sit beside the line rather than on it. Breaking the line to make room
    # leaves the arrow looking like it was never drawn.
    if abs(tip[0] - start[0]) >= abs(tip[1] - start[1]):
        my -= font.size * 0.85
    else:
        mx += font.size * 0.85
    pen.draw.text((mx, my), text, font=font, fill=style.dim, anchor="mm")


# -------------------------------------------------------------------- public

def render_diagram(
    diagram: Diagram,
    size: tuple[int, int],
    *,
    key: str,
    style: Style,
    revealed: int,
    font_path: str,
) -> Image.Image:
    """Draw *diagram* at *size*, with the first *revealed* nodes shown.

    Returns an RGB image at exactly *size*, ready to paste onto the canvas.
    Nodes past the reveal are drawn as empty outlines, the same way a column
    that has not been narrated yet shows its shape without its contents.
    """
    width, height = size
    layer = Image.new("RGB", (width * SCALE, height * SCALE), style.paper)
    draw = ImageDraw.Draw(layer)
    font = ImageFont.truetype(font_path, style.label_size * SCALE)

    if not diagram.nodes:
        return layer.resize((width, height), Image.LANCZOS)

    box = (0.0, 0.0, width * SCALE, height * SCALE)
    if diagram.layout == "cycle":
        # A ring drawn in a wide, short box reads as a flat smear. Give it the
        # largest near-square region that fits, and centre it.
        side = min(box[2], box[3] * 1.7)
        inset = (box[2] - side) / 2
        box = (box[0] + inset, box[1], box[2] - inset, box[3])

    cells = _cells(diagram, box)
    boxes = {
        node.id: _shape_box(cell, node, font)
        for node, cell in zip(diagram.nodes, cells)
    }

    shown = {node.id for i, node in enumerate(diagram.nodes) if i < revealed}

    # Three passes, and the order is the whole point: lines first so a shape
    # covers the line that meets it, shapes next, and edge labels last so a
    # shape can never be drawn over the label of the arrow pointing at it.
    labelled = []
    for edge in diagram.edges:
        if edge.source not in shown or edge.target not in shown:
            continue
        if edge.source not in boxes or edge.target not in boxes:
            continue
        pen = Pen(draw, style, seed_for(f"{key}|{edge.source}->{edge.target}"), SCALE)
        ends = _edge_ends(boxes[edge.source], boxes[edge.target])
        _draw_edge(pen, ends, style.ink)
        if edge.label:
            labelled.append((pen, ends, edge))

    for i, node in enumerate(diagram.nodes):
        box = boxes.get(node.id)
        if box is None:
            continue
        pen = Pen(draw, style, seed_for(f"{key}|{node.id}"), SCALE)
        if i < revealed:
            _draw_node(pen, node, box, font, style, style.panel, style.ink)
        else:
            _draw_node(pen, node, box, font, style, style.paper, style.ghost)

    for pen, ends, edge in labelled:
        _draw_edge_label(pen, ends, edge, font, style, width * SCALE * 0.5)

    return layer.resize((width, height), Image.LANCZOS)
