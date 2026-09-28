"""Turn written prose into a lesson without asking a model.

This is what makes local mode work on pasted text. It is deliberately
mechanical: it finds the section markers, unwraps the hard-wrapped lines,
splits the sentences, and lays them out a slide at a time.

It does not summarise, and it does not invent. What you pasted is what gets
filmed, which is the whole point of a mode that promises to cost nothing -- a
paraphrase you did not write would be worse than the words you did.
"""

from __future__ import annotations

import re

from .lesson import ExplainSection, Lesson
from .prompts import sentences
from .timeline import Slide

# A slide of text stops being readable well before it stops fitting. Four
# sentences is roughly fifteen seconds of narration.
SENTENCES_PER_SLIDE = 4

# A heading is a label, not a sentence. Past this it is prose that happens to
# start with a number or a word like "Step".
MAX_HEADING_CHARS = 70
MAX_TITLE_CHARS = 70

# A marker with almost nothing after it is a label the writer put in the
# header, not a scene. "Lesson 1 — When to Fine-Tune" followed by "Video
# Talking Points" is a title and a subheading, and it does not deserve a slide.
MIN_SECTION_WORDS = 6

# "Scene 1", "Step 2", "Part 3" and friends, with whichever separator the
# writer put after the number. Matched at the start of a *line*: a script is
# usually one run of text with no blank lines between its scenes, so the
# marker itself is the only boundary there is.
#
# The number takes dots as a unit, because "Lesson 15.1" is a lesson number
# and not "Lesson 15" followed by ".1". Treating the dot as a separator ate
# the version and left "1 — When to Fine-Tune" as the title of the video.
_MARKER = re.compile(
    r"^(?:scene|step|part|section|chapter|lesson|beat)\s+[\d.]+\s*"
    r"(?:[—–\-:]\s*|[.)]\s+)?"
    r"(.*)$",
    re.IGNORECASE,
)
_MARKDOWN = re.compile(r"^#{1,6}\s+(.*)$")
_NUMBERED = re.compile(r"^\d+[.)]\s+(.*)$")

# A scene is content, so a script that opens with one has no separate title
# line. Without this the first scene is taken for the video's name and never
# becomes a slide.
_SCENE = re.compile(r"^(?:scene|step|beat)\b", re.IGNORECASE)

# "Video Script:" and the like, in front of the name.
_LABEL = re.compile(r"^(?:video\s+)?(?:script|lesson|title)\s*[:\-—]\s*", re.IGNORECASE)


# Scripts get written with emphasis in them, and the markers are not words.
# Left in, `**` is read aloud by the voice and printed in the caption -- which
# is how one narration came to end with a literal `"**`. Paired markers go
# first, then any that were left unpaired, because a script that opens `**`
# and never closes it is common and the stray is just as unreadable.
_EMPHASIS = re.compile(r"\*+([^*]+)\*+", re.DOTALL)
_INLINE_CODE = re.compile(r"`([^`]*)`")
_LOOSE_MARKER = re.compile(r"[*`]")


def _plain(text: str) -> str:
    """The words, with the writing's markup taken off."""
    text = _INLINE_CODE.sub(r"\1", text)
    text = _EMPHASIS.sub(r"\1", text)
    return _LOOSE_MARKER.sub("", text)


def _tidy(title: str) -> str:
    """A heading for a slide: no wrapping punctuation, and not a paragraph."""
    cleaned = _plain(title).strip().strip("—–-:. ").strip()
    if len(cleaned) > MAX_HEADING_CHARS:
        # Cut at a word boundary rather than mid-word, and say so.
        cleaned = f"{cleaned[:MAX_HEADING_CHARS].rsplit(' ', 1)[0]}…"
    return cleaned or "Untitled"


def _split_title(rest: str) -> tuple[str, str]:
    """A marker line usually carries the first of its body after a colon.

    "Scene 1 — The Two-Week Deadline: Open inside a growing startup" is a
    title and a sentence, not one long title.
    """
    title, colon, body = rest.partition(":")
    if colon and body.strip():
        # Shortened later by _tidy, not here: refusing to split a long title
        # would leave the whole line as a heading and lose the sentence.
        return title.strip(), body.strip()
    return rest.strip(), ""


def _marker(line: str) -> tuple[str, str] | None:
    """The title a line starts a section with, or None if it is prose."""
    stripped = line.strip()
    if not stripped:
        return None
    for pattern in (_MARKDOWN, _MARKER, _NUMBERED):
        found = pattern.match(stripped)
        if found:
            return _split_title(found.group(1))
    return None


def _take_title(lines: list[str]) -> tuple[str, list[str]]:
    """The video's name, and the lines that are content.

    The first line is the title however it is labelled. "Lesson 1 — When to
    Fine-Tune" on line one is the name of the video, not its first scene --
    which matters because it matches the same pattern as every scene marker
    below it. Every marker *after* the first line begins a scene.

    A script is often wrapped, so the name runs over two or three lines; it
    stops at the first marker, or once it is longer than a name should be.
    """
    first = next((line.strip() for line in lines if line.strip()), "")
    if not first or _SCENE.match(first):
        return "", lines

    taken: list[str] = []
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped:
            if taken and _marker(stripped):
                break
            taken.append(stripped)
            if len(" ".join(taken)) > MAX_TITLE_CHARS:
                index += 1
                break
        index += 1
    if not taken:
        return "", lines
    return _tidy(_LABEL.sub("", " ".join(taken))), lines[index:]


def lesson_from_prose(text: str, *,
                      sentences_per_slide: int = SENTENCES_PER_SLIDE) -> Lesson:
    """Structure a script into slides, on this machine, for nothing."""
    if not text.strip():
        raise ValueError("the script is empty")

    title, body_lines = _take_title(text.splitlines())
    if not any(line.strip() for line in body_lines):
        # Nothing but a name: the line is the content, not a name for it.
        body_lines, title = text.splitlines(), ""

    # A section is whatever follows a marker, up to the next one. Lines are
    # kept apart here and joined later, because the wrapping is an artefact of
    # how the file was written, not of where the sentences end.
    sections: list[tuple[str, list[str]]] = []
    for line in body_lines:
        found = _marker(line)
        if found:
            name, inline = found
            sections.append((name, [inline] if inline else []))
        elif sections:
            sections[-1][1].append(line.strip())

    if not sections:
        # No markers at all: one body of prose is still a lesson.
        sections = [("", body_lines)]

    if not title:
        title = _tidy(sections[0][0]) if sections[0][0] else "A script"

    built: list[ExplainSection] = []
    for name, lines in sections:
        body = _plain(" ".join(line for line in lines if line))
        if len(body.split()) < MIN_SECTION_WORDS:
            continue
        said = sentences(body)
        for start in range(0, len(said), sentences_per_slide):
            chunk = said[start:start + sentences_per_slide]
            if not chunk:
                continue
            heading = _tidy(name) if name else _tidy(chunk[0])
            built.append(ExplainSection(
                name=heading,
                # One untitled panel of the section's own sentences. The
                # caption band carries the same words in time with the voice;
                # the panel is what makes the slide more than a bare heading.
                slide=Slide(heading=heading, columns=(("", tuple(chunk)),)),
                sentences=tuple(chunk),
            ))

    if not built:
        raise ValueError("no narration could be found in the script")
    return Lesson(title=title, sections=built)
