"""Prompts for the two things the model is asked to write.

The narration prompt is the unusual one. The timeline needs to know what each
sentence is *about*, so it can speak it while that part of the code is on
screen -- a paragraph describing the snippet as a whole cannot be placed, and
spreading it evenly is what produced narration that talked about list
comprehensions while `numbers = range(15)` was being typed.
"""

from __future__ import annotations

import re

MARKER = "@@"

CODE_PROMPT = """Write Python code for a short tutorial video on the topic below.

**Topic:** {topic}
{context}
**Instructions:**
1. Split the code into several separate ```python blocks, one per step.
2. Each block must be small enough to explain in a few sentences.
3. Each block must be runnable on its own: import anything it needs, and do
   not rely on variables defined in an earlier block.
4. Keep each block well under five minutes to run, and never wait for input.
5. Use only the standard library, plus numpy, pandas or matplotlib.
   Do not install anything.
6. Comment sparingly -- only where the code is not self-explanatory.
7. Include a print statement so each block produces visible output.
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
4. Keep every sentence under twenty words, so it fits while that part is typed.
5. Speak plainly. No markdown, no quotation marks, no code in the sentences.
6. Name variables the way a person would say them, spelling out underscores.
7. Cover the whole snippet -- do not leave a long stretch of code unexplained.
"""

INTRO_PROMPT = """Write the opening narration for a short coding tutorial video.

**Topic:** {topic}

The video begins with a title card showing the topic. Over that card, the
narrator says what the video is about and what the viewer will do.

**Instructions:**
1. Two or three sentences, under twenty words each.
2. Say what the viewer will build or learn, and the order they will do it in.
3. Do not use markdown, quotation marks, or any greeting like "welcome".
4. Do not repeat the topic as a bare title -- make it a spoken sentence.
"""

_BLOCK = re.compile(rf"^\s*{re.escape(MARKER)}\s*(.*?)\s*{re.escape(MARKER)}\s*$")


def code_prompt(topic: str, context: str = "") -> str:
    block = f"\n**Context:** {context}\n" if context else "\n"
    return CODE_PROMPT.format(topic=topic, context=block)


def narration_prompt(code: str) -> str:
    return NARRATION_PROMPT.format(code=code, MARKER=MARKER)


def intro_prompt(topic: str) -> str:
    return INTRO_PROMPT.format(topic=topic)


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
