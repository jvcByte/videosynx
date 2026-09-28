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
import textwrap

from .lesson import ExplainSection, Lesson, Section
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


def _tidy(title: str, reserve: int = 0) -> str:
    """A heading for a slide: no wrapping punctuation, and not a paragraph.

    *reserve* is room to leave for something appended after this, so the total
    stays within the cap rather than the two trimming each other's work.
    """
    cleaned = _plain(title).strip().strip("—–-:. ").strip()
    limit = max(20, MAX_HEADING_CHARS - reserve)
    if len(cleaned) > limit:
        # Cut at a word boundary rather than mid-word, and say so.
        cleaned = f"{cleaned[:limit].rsplit(' ', 1)[0]}…"
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


# A scene marker that is not at the start of a line. Scripts arrive as one
# continuous line about as often as they arrive wrapped, and the anchored
# _MARKER above then matches nothing at all: the whole script becomes a single
# blob chopped every four sentences, under headings that are truncated
# sentences, with "Scene 5 — Summary:" read aloud as narration.
_CANDIDATE = re.compile(
    r"\b(?:scene|step|part|section|chapter|beat)\s+(\d+)\s*[—–\-:]",
    re.IGNORECASE,
)


def _scene_starts(text: str) -> list[re.Match] | None:
    """Where the inline scenes start, or None if this is not that script.

    A candidate counts only when its number is one more than the last one
    accepted. That is what tells a marker from a mention: "Scene 13 — Red
    Flags:" opens a scene, while "the engineer from Scene 1." and "the buttons
    from Scene 3." are looking back at scenes already gone. Without the check
    the second kind are cut out mid-sentence and become scenes of their own,
    which is why a script that works today breaks the moment the anchor is
    dropped.

    None when no run is found, so the wrapped format keeps the behaviour it
    has now and this only engages on a script that really has a numbered run.
    """
    accepted: list[re.Match] = []
    expected: int | None = None
    for found in _CANDIDATE.finditer(text):
        number = int(found.group(1))
        if expected is None:
            expected = number
        if number != expected:
            continue
        accepted.append(found)
        expected += 1
    return accepted if len(accepted) >= 2 else None


def _cut_scenes(text: str, starts: list[re.Match]) -> tuple[str, list[tuple[str, str]]]:
    """(leading text, [(name, body), ...]) split at the marker offsets.

    Cut at the marker rather than searching per line, because a search returns
    only what follows the match and the sentence *before* a mid-line marker
    would simply disappear -- fifteen words gone from a video, silently.
    """
    lead = text[:starts[0].start()].strip()
    scenes: list[tuple[str, str]] = []
    for i, found in enumerate(starts):
        end = starts[i + 1].start() if i + 1 < len(starts) else len(text)
        rest = text[found.end():end].strip()
        name, body = _split_title(rest)
        if not body:
            # No colon, so there is no title and body to tell apart -- the
            # whole run is narration, and the heading comes from its first
            # sentence. Keeping the name here would say it twice.
            name, body = "", rest
        scenes.append((name, body))
    return lead, scenes


# The writer's own key line, marked as such by being quoted. Both scripts
# pasted so far do this, and `Slide.note` is already defined as exactly that:
# the takeaway, held back until everything it summarises is on screen.
_KEY_LINE = re.compile(r'["“”]([^"“”]{15,})["“”]')

# "On the left: … On the right: …" -- two things set against each other. The
# worth of a comparison is that the two sides are visible at once, which is
# what a column pair is for.
_CONTRAST = re.compile(
    r"(on the (?:left|right|other hand))\s*[:\-—]\s*", re.IGNORECASE)

# A fenced code block, with or without a language, and with the closing fence
# optional -- a script truncated in the middle of a block still has code worth
# showing rather than silently becoming prose.
_FENCE = re.compile(r"```[ \t]*[\w+#-]*[ \t]*\n(.*?)(?:```|\Z)", re.DOTALL)


def _key_line(body: str) -> str:
    """The longest quoted run in the scene, or "" if it is not quoting one."""
    quoted = [q.strip() for q in _KEY_LINE.findall(body)]
    quoted = [q for q in quoted if len(q.split()) >= 4]
    return max(quoted, key=len) if quoted else ""


def _sentence_case(text: str) -> str:
    """Standing on its own in a panel, a fragment starts like a sentence.

    "On the left: a market stall where…" loses its cue to become the panel's
    title, and what is left opens in the middle of the writer's sentence.
    """
    return text[:1].upper() + text[1:] if text else text


def _contrast_columns(chunk: list[str]):
    """Two panels when the writer set two things against each other.

    Built from the sentences, not from the raw text: cutting the body at the
    two cues would run the second panel on through everything after it --
    "…the queue drains in a fraction of the time. Same stall. Same customers."
    -- and there is no way to tell from the text where that side really ends.
    A sentence already ends where the writer ended it.

    The cue becomes the panel's title ("On the left"), which is the writer's
    own label rather than one invented here.
    """
    found = []
    for sentence in chunk:
        hit = _CONTRAST.match(sentence.strip())
        if hit:
            found.append((_tidy(hit.group(1)),
                          _sentence_case(sentence.strip()[hit.end():].strip())))
    if len(found) != 2 or not all(text for _, text in found):
        return ()
    return tuple((title, (text,)) for title, text in found)


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

    starts = _scene_starts(text)
    if starts is not None:
        # One numbered run: the script is a single line, or its markers are
        # not at line starts. This is the path the wrapped script never takes.
        lead, scenes = _cut_scenes(text, starts)
        # The lead is a title block, not a title: it runs from the top of the
        # script to the first scene and may hold several lines, including a
        # second copy of the name. _take_title is what knows to stop there.
        title, _ = _take_title(lead.splitlines()) if lead else ("", [])
        # Kept raw: _plain strips backticks, which would take the fences with
        # them, and a fence is the one thing that says a scene is code.
        sections = list(scenes)
    else:
        title, sections = _wrapped_sections(text)

    if not title:
        title = _tidy(sections[0][0]) if sections and sections[0][0] else "A script"

    built: list[Section | ExplainSection] = []
    for name, raw_body in sections:
        fence = _FENCE.search(raw_body)
        body = _plain(raw_body)
        if len(body.split()) < MIN_SECTION_WORDS:
            continue

        if fence:
            # A scene with code in it is a coding scene. "Both" has to mean
            # something in local mode, and the writer putting a block in the
            # script is them saying which half this scene belongs to.
            code = textwrap.dedent(fence.group(1)).strip("\n")
            spoken = sentences(_plain(_FENCE.sub(" ", raw_body)))
            if code.strip() and spoken:
                built.append(Section(
                    name=_tidy(name) if name else _tidy(spoken[0]),
                    code=code,
                    # Empty markers, so resolve_anchors spreads the sentences
                    # across the typing instead of anchoring them all to
                    # character zero. Nobody said which line each one is
                    # about, and inventing that would be worse than not
                    # knowing.
                    narration=[("", sentence) for sentence in spoken],
                ))
                continue

        said = sentences(body)
        chunks = [said[start:start + sentences_per_slide]
                  for start in range(0, len(said), sentences_per_slide)]
        chunks = [chunk for chunk in chunks if chunk]
        if len(chunks) > 1 and len(chunks[-1]) == 1:
            # One sentence on its own would be a slide with a heading and
            # nothing under it: a single sentence has nothing to reveal, so
            # the whole slide would be the title. It rides along instead.
            chunks[-2].extend(chunks.pop())
        for index, chunk in enumerate(chunks):
            # A scene running to several slides otherwise repeats one heading
            # for minutes on end, which reads as the video being stuck. The
            # counter says the same name is still in progress and how much of
            # it is left, without inventing a subtitle it does not have.
            suffix = (f" · {index + 1}/{len(chunks)}"
                      if name and len(chunks) > 1 else "")
            heading = _tidy(name, len(suffix)) if name else _tidy(chunk[0])
            heading += suffix
            built.append(ExplainSection(
                name=heading,
                slide=Slide(heading=heading, columns=_columns(chunk, body)),
                sentences=tuple(chunk),
            ))

    if not built:
        raise ValueError("no narration could be found in the script")
    return Lesson(title=title, sections=built)


def _wrapped_sections(text: str) -> tuple[str, list[tuple[str, str]]]:
    """The path for a script whose markers are at the start of their lines."""
    title, body_lines = _take_title(text.splitlines())
    if not any(line.strip() for line in body_lines):
        # Nothing but a name: the line is the content, not a name for it.
        body_lines, title = text.splitlines(), ""

    # A section is whatever follows a marker, up to the next one. Lines are
    # kept apart here and joined later, because the wrapping is an artefact of
    # how the file was written, not of where the sentences end.
    raw: list[tuple[str, list[str]]] = []
    for line in body_lines:
        found = _marker(line)
        if found:
            name, inline = found
            raw.append((name, [inline] if inline else []))
        elif raw:
            raw[-1][1].append(line.strip())

    if not raw:
        # No markers at all: one body of prose is still a lesson.
        raw = [("", body_lines)]
    return title, [(name, " ".join(l for l in lines if l)) for name, lines in raw]


def _columns(chunk: list[str], body: str):
    """What to put on the slide, revealed one item per sentence.

    The count is not free. `plan_explanation` reveals `min(sentence_index,
    pieces)` items, so the first sentence always plays over the heading alone
    and the last piece can only appear on the last sentence if there are
    `len(chunk) - 1` of them. One more and the final piece is on screen for
    the silent half-second after the voice stops and never during it; one
    fewer and the last sentence has nothing new to show.

    A comparison the writer drew is used when it divides the scene into
    exactly that many parts, and the sentences are revealed one at a time
    when it does not. The arithmetic holds either way, by construction rather
    than by hope.
    """
    want = len(chunk) - 1
    if want < 1:
        # A single sentence has nothing to reveal, so the heading carries it.
        return ()
    contrast = _contrast_columns(chunk)
    if contrast:
        return contrast
    return tuple(("", (sentence,)) for sentence in chunk[1:])
