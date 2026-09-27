"""The editor canvas.

Cost scales with *characters drawn*, not with frames, so the whole design is:
draw the static chrome once, then repaint only the rows that actually changed.
Everything else is a memcpy.
"""

from __future__ import annotations

from PIL import Image, ImageDraw, ImageFont

from ..timeline import VisualState
from . import theme

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
BOTTOM_PAD = 24  # blank rows kept below the cursor, like VS Code's cursorSurroundingLines
CAPTION_H = 92  # a dedicated band, so clearing a caption never erases terminal output
CAPTION_MAX_LINES = 2


class Editor:
    def __init__(self, width: int, height: int, font_path: str, filename: str = "snippet.py"):
        self.width = width
        self.height = height
        self.filename = filename
        # Scale the panel with the canvas so 720p (free tier) and 1080p both work.
        panel_h = min(PANEL_H, max(80, height // 3))
        self.caption_top = height - STATUS_H - CAPTION_H
        self.panel_top = self.caption_top - panel_h
        self.rows = max(1, (self.panel_top - CODE_TOP - BOTTOM_PAD) // LINE_H)

        self.f_ui = ImageFont.truetype(font_path, UI_SIZE)
        self.f_code = ImageFont.truetype(font_path, CODE_SIZE, layout_engine=ImageFont.Layout.BASIC)
        self.f_term = ImageFont.truetype(font_path, TERM_SIZE, layout_engine=ImageFont.Layout.BASIC)
        self.f_caption = ImageFont.truetype(font_path, CAPTION_SIZE)
        self.char_w = self.f_code.getlength("M")

        self._chrome = self._draw_chrome()
        self.canvas = self._chrome.copy()

        self._code = ""
        self._spans: list[list[tuple[str, str]]] = []
        self._drawn: list[list[tuple[str, str]]] = []
        self._drawn_output: tuple[str, ...] = ()
        self._drawn_caption: str | None = None
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
    def apply(self, state: VisualState) -> None:
        """Update the canvas to *state*, repainting only what changed."""
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
