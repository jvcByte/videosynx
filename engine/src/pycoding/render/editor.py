"""The editor canvas.

Cost scales with *characters drawn*, not with frames, so the whole design is:
draw the static chrome once, then repaint only the rows that actually changed.
Everything else is a memcpy.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from ..timeline import VisualState
from . import theme
from .diagram import render_diagram
from .style import DEFAULT_STYLE, STYLES, Style

TITLE_H = 36
TAB_H = 36
CODE_TOP = 96
STATUS_H = 24
PANEL_H = 300
GUTTER_W = 80
LINE_H = 34
CODE_SIZE = 22
TERM_SIZE = 20
UI_SIZE = 16
CAPTION_SIZE = 26
TITLE_SIZE = 62
SUBTITLE_SIZE = 22
BOTTOM_PAD = 24  # blank rows kept below the cursor, like VS Code's cursorSurroundingLines
CAPTION_H = 92  # a dedicated band, so clearing a caption never erases terminal output
CAPTION_MAX_LINES = 2
DIAGRAM_FRACTION = 0.46  # of the canvas height; leaves room for heading and note


class Editor:
    def __init__(self, width: int, height: int, font_path: str,
                 filename: str = "snippet.py", style: Style | None = None,
                 watermark: str | None = None):
        self.width = width
        self.height = height
        self.filename = filename
        self.font_path = font_path
        self.watermark = watermark
        # How a concept slide is drawn. The editor chrome and the caption band
        # keep their own colours -- a code editor is not a sketch, and the
        # caption band sits below both.
        self.style = style or STYLES[DEFAULT_STYLE]
        # Scale the panel with the canvas so 720p (free tier) and 1080p both work.
        panel_h = min(PANEL_H, max(80, height // 3))
        self.caption_top = height - STATUS_H - CAPTION_H
        self.panel_top = self.caption_top - panel_h
        self.rows = max(1, (self.panel_top - CODE_TOP - BOTTOM_PAD) // LINE_H)

        self.f_ui = ImageFont.truetype(font_path, UI_SIZE)
        self.f_code = ImageFont.truetype(font_path, CODE_SIZE, layout_engine=ImageFont.Layout.BASIC)
        self.f_term = ImageFont.truetype(font_path, TERM_SIZE, layout_engine=ImageFont.Layout.BASIC)
        self.f_caption = ImageFont.truetype(font_path, CAPTION_SIZE)
        self.f_title = ImageFont.truetype(font_path, TITLE_SIZE)
        self.f_subtitle = ImageFont.truetype(font_path, SUBTITLE_SIZE)
        self.char_w = self.f_code.getlength("M")

        self._chrome = self._draw_chrome()
        self.canvas = self._chrome.copy()

        self._code = ""
        self._spans: list[list[tuple[str, str]]] = []
        self._drawn: list[list[tuple[str, str]]] = []
        self._drawn_output: tuple[str, ...] = ()
        self._drawn_caption: str | None = None
        self._drawn_title: str | None = None
        self._drawn_slide = None
        self._mode = "editor"
        self._scroll = 0
        self._drawn_scroll = 0
        self._cursor_line = 0

    # ------------------------------------------------------------------ chrome
    def _draw_chrome(self) -> Image.Image:
        img = Image.new("RGB", (self.width, self.height), theme.BG)
        d = ImageDraw.Draw(img)

        d.rectangle([0, 0, self.width, TITLE_H], fill=theme.TITLE_BG)
        for i, colour in enumerate(theme.DOT_COLORS):
            x = 20 + i * 22
            d.ellipse([x, 12, x + 12, 24], fill=colour)
        d.text((self.width // 2, 18), f"{self.filename} — pycoding",
               font=self.f_ui, fill="#cccccc", anchor="mm")

        d.rectangle([0, TITLE_H, self.width, TITLE_H + TAB_H], fill=theme.TAB_BG)
        d.rectangle([0, TITLE_H, 220, TITLE_H + TAB_H], fill=theme.BG)
        d.text((16, TITLE_H + 18), self.filename, font=self.f_ui, fill="#ffffff", anchor="lm")
        d.text((196, TITLE_H + 18), "×", font=self.f_ui, fill="#cccccc", anchor="lm")

        d.rectangle([0, self.panel_top, self.width, self.caption_top], fill=theme.PANEL_BG)
        d.line([0, self.panel_top, self.width, self.panel_top], fill=theme.BORDER)
        d.text((24, self.panel_top + 22), "TERMINAL", font=self.f_ui, fill="#9d9d9d")
        d.rectangle([0, self.caption_top, self.width, self.height - STATUS_H], fill=theme.CAPTION_BG)

        d.rectangle([0, self.height - STATUS_H, self.width, self.height], fill=theme.STATUS_BG)
        d.text((20, self.height - 12), "main", font=self.f_ui, fill="#ffffff", anchor="lm")
        d.text((self.width - 20, self.height - 12), "Python 3", font=self.f_ui,
               fill="#ffffff", anchor="rm")
        return img

    # ------------------------------------------------------------------- rows
    def _row_y(self, row: int) -> int:
        return CODE_TOP + row * LINE_H

    def _paint_row(self, row: int, spans: list[tuple[str, str]], line_no: int | None,
                   cursor: bool) -> None:
        y = self._row_y(row)
        d = ImageDraw.Draw(self.canvas)
        d.rectangle([0, y, self.width, y + LINE_H], fill=theme.BG)
        if line_no is not None:
            d.text((GUTTER_W - 20, y), str(line_no), font=self.f_code,
                   fill=theme.GUTTER_FG, anchor="ra")
        x = GUTTER_W
        for text, colour in spans:
            if text:
                d.text((x, y), text, font=self.f_code, fill=colour, anchor="la")
                x += self.char_w * len(text)
        if cursor:
            d.rectangle([x, y + 2, x + self.char_w, y + LINE_H - 4], fill=theme.CURSOR)

    def _repaint_code_area(self) -> None:
        """Full repaint of the code region — only needed when the view scrolls."""
        d = ImageDraw.Draw(self.canvas)
        d.rectangle([0, CODE_TOP - 8, self.width, self.panel_top], fill=theme.BG)
        for row in range(self.rows):
            idx = self._scroll + row
            if idx < len(self._drawn):
                self._paint_row(row, self._drawn[idx], idx + 1, idx == self._cursor_line)

    # ------------------------------------------------------------------ state
    @staticmethod
    def _mode_for(state: VisualState) -> str:
        if state.title is not None:
            return "card"
        if state.slide is not None:
            return "slide"
        return "editor"

    def apply(self, state: VisualState) -> None:
        """Update the canvas to *state*, repainting only what changed.

        Three modes share one canvas: the opening card, a concept slide, and
        the editor. Changing mode invalidates everything, so the next state
        repaints from scratch rather than being diffed against a canvas that
        was showing something else entirely.
        """
        mode = self._mode_for(state)
        if mode != self._mode:
            self._mode = mode
            self.canvas = self._chrome.copy()
            self._code = None  # type: ignore[assignment]
            self._spans = []
            self._drawn = []
            self._drawn_scroll = -1
            self._cursor_line = 0
            self._drawn_output = ()
            self._drawn_caption = None
            self._drawn_title = None
            self._drawn_slide = None

        if mode == "card":
            if state.title != self._drawn_title:
                self._drawn_title = state.title
                self._paint_title_card(state.title)
        elif mode == "slide":
            if state.slide != self._drawn_slide:
                self._drawn_slide = state.slide
                self._paint_slide(state.slide)
        else:
            if state.code != self._code:
                self._code = state.code
                self._spans = theme.highlight(state.code)
                visible = theme.visible_lines(self._spans, state.code, len(state.code))
                self._update_scroll(len(visible) - 1)
                self._redraw_lines(visible)
            if state.output != self._drawn_output:
                self._redraw_output(state.output)

        if state.caption != self._drawn_caption:
            self._redraw_caption(state.caption)

        if self.watermark:
            self._paint_watermark()

    def _paint_watermark(self) -> None:
        """A corner mark, for tiers that ask for one.

        Painted on every state change rather than once into the chrome: a slide
        or a title card repaints the whole canvas, so a mark drawn only into
        the chrome would survive exactly until the first slide.
        """
        d = ImageDraw.Draw(self.canvas)
        d.text((self.width - 28, self.caption_top - 34), self.watermark,
               font=self.f_ui, fill=self.style.dim, anchor="rm")

    def _paint_slide(self, slide) -> None:
        """Draw a concept illustration.

        Explaining over typing code makes the viewer read code before they
        know what it is for. A slide carries the idea on its own, and the code
        starts once the narration says so.
        """
        d = ImageDraw.Draw(self.canvas)
        d.rectangle([0, 0, self.width, self.caption_top], fill=self.style.paper)
        d.rectangle([0, self.height - STATUS_H, self.width, self.height],
                    fill=self.style.paper)

        heading_lines = _wrap_to_pixels(slide.heading, self.f_title, int(self.width * 0.8))[:2]
        heading_h = len(heading_lines) * int(TITLE_SIZE * 1.2)
        rule_gap = int(TITLE_SIZE * 0.25) + 4 + int(TITLE_SIZE * 0.75)

        # One branch per content type, in the same order as Slide.pieces -- that
        # property is the single source of truth for how much is on screen, so
        # the two must not drift apart.
        col_w = wrapped = box_h = 0
        if slide.columns:
            col_w, _, wrapped, box_h = self._layout_columns(slide)
            content_h = box_h
        elif slide.sequence:
            content_h = self._sequence_height(slide)
        elif slide.diagram:
            content_h = self._diagram_height()
        else:
            content_h = 0

        pieces = slide.pieces
        show_note = bool(slide.note) and pieces and slide.revealed >= pieces
        note_h = int(TITLE_SIZE * 0.45) + CAPTION_SIZE if show_note else 0

        total = heading_h + rule_gap + content_h + note_h
        y = max(int(self.height * 0.07), (self.caption_top - total) // 2)

        for line in heading_lines:
            d.text((self.width // 2, y), line, font=self.f_title,
                   fill=self.style.ink, anchor="ma")
            y += int(TITLE_SIZE * 1.2)

        rule_w = int(self.width * 0.09)
        y += int(TITLE_SIZE * 0.25)
        d.rectangle([(self.width - rule_w) // 2, y, (self.width + rule_w) // 2, y + 4],
                    fill=self.style.accent)
        y += 4 + int(TITLE_SIZE * 0.75)

        if slide.columns:
            self._paint_columns(d, slide, y, col_w, wrapped, box_h)
        elif slide.sequence:
            self._paint_sequence(d, slide, y)
        elif slide.diagram:
            self._paint_diagram(slide, y)
        y += content_h

        # The takeaway lands last, once everything it summarises is on screen.
        if show_note:
            d.text((self.width // 2, y + int(TITLE_SIZE * 0.45)), slide.note,
                   font=self.f_caption, fill=self.style.dim, anchor="ma")

    def _diagram_height(self) -> int:
        return max(120, int(self.height * DIAGRAM_FRACTION))

    def _paint_diagram(self, slide, y: int) -> None:
        """Draw the node-and-edge illustration.

        Drawn into its own layer and pasted, rather than drawn straight onto
        the canvas: the diagram renderer supersamples to antialias its curves,
        and that has to happen before the strokes reach the frame.
        """
        width = int(self.width * 0.88)
        height = self._diagram_height()
        image = render_diagram(
            slide.diagram, (width, height),
            key=slide.heading, style=self.style,
            revealed=slide.revealed, font_path=self.font_path,
        )
        self.canvas.paste(image, ((self.width - width) // 2, y))

    def _layout_columns(self, slide):
        """Work out column geometry and wrapped lines before drawing anything."""
        count = len(slide.columns)
        available = int(self.width * 0.82)
        gap = 44
        col_w = (available - gap * (count - 1)) // count
        wrapped = [
            [w for line in lines
             for w in _wrap_to_pixels(line, self.f_term, col_w - 60)[:2]]
            for _, lines in slide.columns
        ]
        rows = max((len(w) for w in wrapped), default=0)
        box_h = int(SUBTITLE_SIZE * 2.4) + rows * int(TERM_SIZE * 1.6) + 32
        return col_w, (self.width - available) // 2, wrapped, box_h

    def _sequence_height(self, slide) -> int:
        return 96 + (96 if slide.revealed >= 2 else 44)

    def _paint_columns(self, d, slide, y: int, col_w: int, wrapped, box_h: int) -> None:
        """Side-by-side panels, revealed one per sentence."""
        gap = 44
        x0 = (self.width - (col_w * len(slide.columns) + gap * (len(slide.columns) - 1))) // 2
        for i, (title, _) in enumerate(slide.columns):
            x = x0 + i * (col_w + gap)
            if i >= slide.revealed:
                # Not said yet: an empty frame, so the shape of what is coming
                # is visible without giving it away.
                d.rounded_rectangle([x, y, x + col_w, y + box_h], radius=14,
                                    outline=self.style.ghost, width=2)
                continue
            d.rounded_rectangle([x, y, x + col_w, y + box_h], radius=14,
                                fill=self.style.panel, outline=self.style.panel_line,
                                width=2)
            d.text((x + 30, y + 24), title, font=self.f_subtitle,
                   fill=self.style.accent, anchor="la")
            ty = y + 24 + int(SUBTITLE_SIZE * 2.4)
            for line in wrapped[i]:
                d.text((x + 30, ty), line, font=self.f_term,
                       fill=self.style.ink, anchor="la")
                ty += int(TERM_SIZE * 1.6)

    def _paint_sequence(self, d, slide, y: int) -> None:
        """Boxes in a row, with the index numbers revealed beneath them.

        The boxes are always visible -- they are the data. The narration then
        adds the positive indices, then the negative ones, which is the order
        a person learns them in.
        """
        items = slide.sequence
        box_w = min(220, int(self.width * 0.66 / len(items)))
        box_h = 96
        gap = 20
        x0 = (self.width - (len(items) * box_w + (len(items) - 1) * gap)) // 2
        for i, value in enumerate(items):
            x = x0 + i * (box_w + gap)
            d.rounded_rectangle([x, y, x + box_w, y + box_h], radius=12,
                                fill=self.style.panel, outline=self.style.panel_line,
                                width=2)
            d.text((x + box_w // 2, y + box_h // 2), value, font=self.f_code,
                   fill=self.style.ink, anchor="mm")
            # Positive and negative indices are two different readings of the
            # same box, so they have to look different.
            if slide.revealed >= 1:
                d.text((x + box_w // 2, y + box_h + 30), str(i),
                       font=self.f_caption, fill=self.style.accent, anchor="mm")
            if slide.revealed >= 2:
                d.text((x + box_w // 2, y + box_h + 72), str(i - len(items)),
                       font=self.f_caption, fill=self.style.ink, anchor="mm")

    def _paint_title_card(self, title: str) -> None:
        """Draw the opening card: what the video is about, before any code."""
        d = ImageDraw.Draw(self.canvas)
        d.rectangle([0, 0, self.width, self.caption_top], fill=self.style.paper)
        # The editor's status bar would otherwise sit under the card reading
        # "main" and a Python version, which means nothing before any code.
        d.rectangle([0, self.height - STATUS_H, self.width, self.height],
                    fill=self.style.paper)

        lines = _wrap_to_pixels(title, self.f_title, int(self.width * 0.8))[:3]
        block_h = len(lines) * int(TITLE_SIZE * 1.25)
        y = (self.caption_top - block_h) // 2 - int(TITLE_SIZE * 0.4)

        for line in lines:
            d.text((self.width // 2, y), line, font=self.f_title,
                   fill=self.style.ink, anchor="ma")
            y += int(TITLE_SIZE * 1.25)

        rule_w = int(self.width * 0.12)
        y += int(TITLE_SIZE * 0.35)
        d.rectangle([(self.width - rule_w) // 2, y, (self.width + rule_w) // 2, y + 4],
                    fill=self.style.accent)

        d.text((self.width // 2, y + int(TITLE_SIZE * 0.6)), "a pycoding tutorial",
               font=self.f_subtitle, fill=self.style.dim, anchor="ma")

    def _update_scroll(self, cursor_line: int) -> None:
        """Keep the cursor on screen, holding BOTTOM_PAD rows below it."""
        keep_below = max(1, BOTTOM_PAD // LINE_H)
        if cursor_line - self._scroll >= self.rows - keep_below:
            self._scroll = cursor_line - self.rows + keep_below + 1
        elif cursor_line < self._scroll:
            self._scroll = cursor_line

    def _redraw_lines(self, visible: list[list[tuple[str, str]]]) -> None:
        # The viewport moved, so every row shows different content — repaint all.
        if self._scroll != self._drawn_scroll:
            self._drawn = visible
            self._cursor_line = len(visible) - 1
            self._drawn_scroll = self._scroll
            self._repaint_code_area()
            return

        # Repaint only rows whose content changed, plus the old/new cursor rows.
        changed = set()
        for i in range(max(len(visible), len(self._drawn))):
            new = visible[i] if i < len(visible) else []
            old = self._drawn[i] if i < len(self._drawn) else []
            if new != old:
                changed.add(i)
        changed.add(len(visible) - 1)  # new cursor row
        changed.add(self._cursor_line)  # clear the old cursor

        self._drawn = visible
        self._cursor_line = len(visible) - 1
        for idx in sorted(changed):
            row = idx - self._scroll
            if 0 <= row < self.rows:
                spans = visible[idx] if idx < len(visible) else []
                self._paint_row(row, spans, idx + 1, idx == self._cursor_line)

    def _redraw_output(self, output: tuple[str, ...]) -> None:
        d = ImageDraw.Draw(self.canvas)
        prev_n = len(self._drawn_output)
        if len(output) < prev_n:
            d.rectangle([0, self.panel_top + 44, self.width, self.caption_top],
                        fill=theme.PANEL_BG)
            prev_n = 0
        for i in range(prev_n, len(output)):
            text = output[i]
            # Never spill into the caption band.
            if self.panel_top + 56 + i * 30 + 30 > self.caption_top:
                break
            colour = theme.ERROR_FG if _looks_like_error(text) else theme.FG
            y = self.panel_top + 56 + i * 30
            d.text((24, y), text, font=self.f_term, fill=colour)
        self._drawn_output = output

    def _redraw_caption(self, caption: str | None) -> None:
        """Repaint the caption band. Its own region, so this cannot clobber output."""
        d = ImageDraw.Draw(self.canvas)
        d.rectangle([0, self.caption_top, self.width, self.height - STATUS_H],
                    fill=theme.CAPTION_BG)
        if caption:
            lines = _wrap_to_pixels(caption, self.f_caption, int(self.width * 0.82))
            lines = lines[:CAPTION_MAX_LINES]
            total_h = len(lines) * int(CAPTION_SIZE * 1.35)
            y = self.caption_top + (CAPTION_H - total_h) // 2
            for line in lines:
                d.text((self.width // 2, y), line, font=self.f_caption,
                       fill=theme.CAPTION_FG, anchor="ma")
                y += int(CAPTION_SIZE * 1.35)
        self._drawn_caption = caption

    # ------------------------------------------------------------------ output
    def tobytes(self) -> bytes:
        return self.canvas.tobytes()


def _wrap_to_pixels(text: str, font, max_width: int) -> list[str]:
    """Greedy word wrap measured in pixels rather than characters."""
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if not current or font.getlength(trial) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _looks_like_error(line: str) -> bool:
    return line.startswith(("Traceback", "  File ", "SyntaxError", "NameError",
                            "TypeError", "ValueError", "ZeroDivisionError",
                            "IndexError", "KeyError", "AttributeError")) or "Error:" in line
