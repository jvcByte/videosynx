"""Prompts for the two things the model is asked to write.

The narration prompt is the unusual one. The timeline needs to know what each
sentence is *about*, so it can speak it while that part of the code is on
screen -- a paragraph describing the snippet as a whole cannot be placed, and
spreading it evenly is what produced narration that talked about list
comprehensions while `numbers = range(15)` was being typed.
"""

from __future__ import annotations

import json
import re

from .timeline import parse_diagram

MARKER = "@@"

CODE_PROMPT = """Write Python code for a short tutorial video on the topic below.

**Topic:** {topic}
{context}
**Instructions:**
1. **The topic is the subject. The scenario is only the setting.** Everything
   here must teach the topic named above, and the scenario must be chosen
   because the topic is *what turns on it* -- the place where getting the topic
   wrong is the thing that breaks. A video about data types belongs in code
   where a value arriving as the wrong type is the bug; putting it in a webhook
   dispatcher teaches the viewer about webhooks and nothing about data types.
   If a line of the code is not there because of the topic, it does not belong
   in the video.
2. Ground that scenario in ONE real system -- the kind of code that runs in
   production, not a textbook illustration -- name it in the comments, and keep
   it the same across every block.
3. Use realistic names and data: `pending_invoices`, `retry_backoff`,
   `failed_uploads`, real-looking records. Never `foo`, `bar`, `x`, `[1, 2, 3]`
   or any placeholder that says nothing about what the value is.
4. **Introduce nothing the narration will not explain.** Every setting-specific
   word the code brings with it -- a webhook, a retry budget, a pool -- has to
   be paid for with a sentence saying what it is before it is used. Keep those
   words to a minimum, and prefer the topic's own vocabulary.
5. Split the code into several separate ```python blocks, one per step.
6. Each block must be small enough to explain in a few sentences.
7. Each block must be runnable on its own: import anything it needs, and do
   not rely on variables defined in an earlier block.
8. Keep each block well under five minutes to run, and never wait for input.
9. Use only the standard library, plus numpy, pandas or matplotlib.
   Do not install anything.
10. Comment sparingly -- only where the code is not self-explanatory.
11. Include a print statement so each block produces visible output.
"""

NARRATION_PROMPT = """Below is one Python snippet from a tutorial video. It will be
typed out on screen, character by character, while a narrator explains it.

```python
{code}
```

Write what the narrator says. Return a series of blocks, each in exactly this
form:

{MARKER} a fragment copied exactly from the code {MARKER}
A sentence to say while that fragment is being typed.

**Rules:**
1. The fragment must be copied character for character from the code above and
   must appear exactly once in it. It is used to line the sentence up with the
   right part of the screen.
2. Write one block per idea, in the order the lines appear.
3. Each sentence must say what the code *at that fragment* does. Never begin
   with a general statement about the topic.
4. Speak in the scenario's terms -- but never assume one. The first time a
   setting-specific word appears, say what it is in that same sentence. A
   viewer who has to guess what "backoff" means has stopped following the topic.
5. Name the system this is part of, and what would go wrong without it -- a
   viewer should finish knowing what this is *for*, not only what it does.
6. Keep every sentence under twenty words, so it fits while that part is typed.
7. Speak plainly. No markdown, no quotation marks, no code in the sentences.
8. Say a variable name as the words it is made of: `max_retry_attempts` is
   "max retry attempts". **Never say the word "underscore"** -- spelling the
   name out character by character is not speech. If a name cannot be said
   naturally, say what the value is instead.
9. Cover the whole snippet -- do not leave a long stretch of code unexplained.
"""

INTRO_PROMPT = """Write the opening narration for a short coding tutorial video.

**Topic:** {topic}

The video begins with a title card showing the topic. Over that card, the
narrator says what the video is about and what the viewer will do.

**Instructions:**
1. Two or three sentences, under twenty words each.
2. **The first sentence says what the topic is.** The viewer chose this
   subject by name and has been told nothing else yet, so name it in plain
   language before anything else happens: what it is, or what it is for.
   Asked for variables, open on variables -- not on the store, the system or
   the scenario the later examples happen to live in.
3. Then say where it applies, if there is somewhere worth naming. The
   scenario comes after the subject, never instead of it, and a topic that is
   its own setting does not need one invented for it.
4. Say what the viewer will build or learn, and the order they will do it in.
5. Do not use markdown, quotation marks, or any greeting like "welcome".
6. Do not read the title back as a bare fragment. Say what the thing is, in a
   sentence someone could follow having never seen the card.
"""

OUTRO_PROMPT = """Write the closing narration for a short coding tutorial video.

**Topic:** {topic}

The video is over. It ends on a card showing the topic again, and the narrator
says what the viewer has just been through.

What the video covered:
{covered}

**Instructions:**
1. Two or three sentences, under twenty words each.
2. Say what the viewer can now do, or what the pieces add up to. It is the
   answer to "what did I just watch", and it is the last thing they will hear.
3. Name the thing in the topic's own words. A closing that reaches for a
   different vocabulary ends the video in a language the video never taught.
4. Raise nothing new. No new terms, no "next time", no "in the next video".
5. Do not thank the viewer and do not say goodbye. It is a summary, not a
   sign-off.
6. Do not use markdown or quotation marks.
"""


CONCEPT_PROMPT = """Write the concept slides that open a tutorial video on the topic below.

**Topic:** {topic}
{context}
This video teaches the idea before it shows any code. Over these slides the
narrator says what the thing *is* and gives the viewer the words for it. Code
comes afterwards, so these slides must not describe code.

**Draw the idea; do not describe it in text.** A slide carries either a
`diagram` of labelled shapes joined by arrows, or `columns` of text. Prefer the
diagram whenever the idea is positional -- what connects to what, what sits on
top of what, what happens in what order. Reach for text columns only when there
is genuinely nothing to draw.

Return JSON: {slide_range} slides, as a list of objects in exactly this shape:

[
  {{
    "heading": "How a request reaches a worker",
    "diagram": {{
      "layout": "row",
      "nodes": [
        {{"id": "req", "label": "Request", "shape": "box"}},
        {{"id": "q", "label": "Queue", "shape": "cylinder"}},
        {{"id": "w", "label": "Worker", "shape": "person"}}
      ],
      "edges": [["req", "q", "enqueue"], ["q", "w", "claim"]]
    }},
    "note": "The queue is what lets the web tier stay fast.",
    "narration": [
      "A sentence spoken over the heading, before any shape is on screen.",
      "A sentence spoken as the first shape appears.",
      "A sentence spoken as the second shape appears.",
      "A sentence spoken as the third shape appears.",
      "A closing sentence that lands the note."
    ]
  }},
  {{
    "heading": "Feature extraction or fine-tuning",
    "columns": [
      ["Feature extraction", ["the model stays frozen", "a small model learns"]],
      ["Fine-tuning", ["the model itself changes", "more data needed"]]
    ],
    "note": "Two strategies with different trade-offs.",
    "narration": ["One.", "Two.", "Three.", "Four."]
  }}
]

**Diagram vocabulary:**
- `layout`: row, column, grid, cycle, tree, stack
- `shape`: box, round, cylinder, cloud, document, person, stack
- an edge is `[source_id, target_id]`, or `[source_id, target_id, "label"]`
- node `id`s must be unique, and every edge must name an id that exists
- add `"repeat": 3` to a node for "a lot of these" (2 to 5 stacked copies)
- use `stack` for layers, `cycle` for a loop, `tree` for one-to-many

**Instructions:**
1. The first slide answers "what is this?" in plain language, for someone who
   has never met the idea. Do not mention the scenario or any real system yet.
2. **Name the actual things.** `int`, `str`, `list`, `dict` -- whatever the
   topic is really about -- not "values", "information" or "data". A slide of
   abstractions is one the viewer cannot use afterwards; it is the specific
   words they take away.
3. **Cover the vocabulary, and never use a word before it is defined.** By the
   last slide the viewer must have met every term the rest of the video leans
   on, each explained where it first appears. A viewer who has to guess what a
   word means has already stopped following the topic.
4. Later slides may narrow toward the problem the video goes on to solve.
5. Three to five shapes per diagram, or two to three columns of text. Keep
   every label and every line under forty characters.
6. `narration` must hold exactly one sentence more than there are things to
   reveal -- nodes for a diagram, columns for a text slide. The first is spoken
   over the heading alone, then one per shape as it appears, then a closing
   sentence. Three nodes need five sentences; the diagram example above has
   exactly that.
7. Each sentence is under twenty words, and must not read the labels back word
   for word -- say it in speech, not as a caption.
8. The note is one sentence: what to remember from that slide.
9. No code, no backticks, no function calls, no markdown.
10. Never write placeholder text: "heading", "body", "text here", "line one"
   or an ellipsis name a slot instead of filling it, and a slide made of them
   is worse than no slide. Write the real content or drop the slide.
"""

SCRIPT_PROMPT = """Turn the video script below into the lesson JSON a renderer consumes.

Return only the JSON, with no commentary and no markdown fences.

{
  "title": "the video's title",
  "intro": "one or two sentences spoken over the title card",
  "sections": [
    {
      "name": "a short label for this section",
      "explain": {
        "heading": "what this slide says, as a heading",
        "diagram": {
          "layout": "row",
          "nodes": [{"id": "a", "label": "Request", "shape": "box"}],
          "edges": []
        },
        "note": "the one sentence to remember"
      },
      "narration": ["one sentence", "one sentence", "one sentence"]
    },
    {
      "name": "a section that shows code",
      "code": "print('the actual code, runnable on its own')",
      "narration": [["print(", "one sentence about that fragment"]]
    }
  ],
  "outro": ""
}

**Rules:**
1. Every narration entry is exactly ONE sentence. This is not a style
   preference: each sentence reveals one thing on screen, so two sentences
   packed into one entry puts the slide ahead of the voice.
2. An `explain` section's narration needs exactly two more sentences than
   there are things to reveal -- one over the heading, one per node or column,
   one closing. Three nodes need five sentences.
3. Prefer a `diagram` over `columns` whenever the script describes something
   positional: what connects to what, what sits on top of what, what happens
   in what order. Use `columns` only for a genuine contrast of text.
4. Diagram vocabulary: `layout` is row, column, grid, cycle, tree or stack;
   `shape` is box, round, cylinder, cloud, document, person or stack; an edge
   is [source, target] or [source, target, "label"]. Node ids must be unique
   and every edge must name an id that exists.
5. Use a `code` section only where the script actually contains code. Each
   narration marker must be copied character for character from that code and
   appear in it exactly once. Omit `code` sections entirely if the script has
   no code in it.
6. Keep the script's own words wherever you can. Do not invent claims,
   examples or numbers that are not in it.
7. Keep every heading, label and line under forty characters.
8. Never write placeholder text. "heading", "body", "line one", "text here",
   "..." name a slot rather than filling it, and a slide of them is worse than
   no slide at all. If a scene does not yield real content, give it fewer
   slides or fold it into the next one -- do not emit a template.

**The script:**

{script}
"""


def _first_json(text: str, pattern: str):
    """The first JSON value matching *pattern* in *text*, or None.

    Models wrap JSON in prose and fences. Digging it out in one place stops
    every caller doing the same thing with a slightly different regex.
    """
    match = re.search(pattern, text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def parse_json_object(text: str):
    return _first_json(text, r"\{.*\}")


def parse_json_array(text: str):
    return _first_json(text, r"\[.*\]")


def script_prompt(text: str) -> str:
    # str.replace, not str.format: the prompt is mostly JSON, and doubling
    # every brace to survive .format() makes it unreadable.
    return SCRIPT_PROMPT.replace("{script}", text)


_BLOCK = re.compile(rf"^\s*{re.escape(MARKER)}\s*(.*?)\s*{re.escape(MARKER)}\s*$")


def code_prompt(topic: str, context: str = "") -> str:
    block = f"\n**Context:** {context}\n" if context else "\n"
    return CODE_PROMPT.format(topic=topic, context=block)


def narration_prompt(code: str) -> str:
    return NARRATION_PROMPT.format(code=code, MARKER=MARKER)


def intro_prompt(topic: str) -> str:
    return INTRO_PROMPT.format(topic=topic)


def outro_prompt(topic: str, covered: list[str] | None = None) -> str:
    """The closing card. *covered* is what the slides actually said."""
    listed = "\n".join(f"- {line}" for line in (covered or []))
    return OUTRO_PROMPT.format(
        topic=topic,
        covered=listed or "- (no slides: this one was code throughout)",
    )


def concept_prompt(topic: str, context: str = "",
                   slide_range: str = "one to three") -> str:
    block = f"\n**Context:** {context}\n" if context else "\n"
    return CONCEPT_PROMPT.format(topic=topic, context=block, slide_range=slide_range)


MAX_SLIDES = 3


def parse_slides(text: str, limit: int = MAX_SLIDES) -> list[dict]:
    """Pull the concept slides out of a model response.

    Returns [] rather than raising. The concept phase improves the opening; a
    model that answers in prose instead should cost the viewer an introduction,
    not the whole job.
    """
    raw = parse_json_array(text)
    if not isinstance(raw, list):
        return []

    slides: list[dict] = []
    for item in raw[:limit]:
        if not isinstance(item, dict) or not str(item.get("heading", "")).strip():
            continue
        columns: list[tuple[str, tuple[str, ...]]] = []
        for entry in item.get("columns") or []:
            if not isinstance(entry, (list, tuple)) or len(entry) != 2:
                continue
            title, lines = entry
            if not isinstance(lines, (list, tuple)) or not lines:
                continue
            columns.append((str(title), tuple(str(line) for line in lines)))
        diagram = parse_diagram(item.get("diagram"))
        narration = [str(s) for s in item.get("narration") or [] if str(s).strip()]
        # A slide with nothing to reveal never reveals anything; one with no
        # narration is a silent still. Either is worse than no slide at all.
        if not (columns or diagram) or not narration:
            continue
        slides.append({
            "heading": str(item["heading"]),
            "columns": tuple(columns),
            "diagram": diagram,
            "note": str(item.get("note", "")),
            "narration": narration,
        })
    return slides


def parse_code(text: str) -> list[str]:
    """Pull the fenced code blocks out of a model response.

    Raises rather than returning an empty list: a response with no code used
    to sail through and produce a job with nothing in it.
    """
    blocks = re.findall(r"```(?:python)?\s*\n(.*?)```", text, re.DOTALL)
    snippets = [b.strip() for b in blocks if b.strip()]
    if not snippets:
        raise ValueError("the model returned no code blocks")
    return snippets


def parse_anchored(text: str) -> list[tuple[str, str]]:
    """Parse ``@@ fragment @@`` blocks into (marker, sentence) pairs."""
    pairs: list[tuple[str, str]] = []
    marker: str | None = None
    words: list[str] = []

    def flush() -> None:
        if marker is not None and words:
            pairs.append((marker, " ".join(words).strip()))

    for line in text.splitlines():
        found = _BLOCK.match(line)
        if found:
            flush()
            marker = found.group(1).strip()
            words = []
        elif marker is not None and line.strip():
            words.append(line.strip())
    flush()
    return [(m, s) for m, s in pairs if m and s]


def sentences(text: str) -> list[str]:
    """Split prose into sentences, for narration that arrives unanchored."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    return [p.strip() for p in parts if p.strip()]
