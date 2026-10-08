# videosynx

Generate narrated coding-tutorial videos from a topic.

Give it a subject and it writes the code, runs it, explains each line while the
code types itself out on screen, narrates it, and produces an `.mp4` with
captions and a matching `.srt`.

It also runs as a hosted service: submit a topic through the web UI, a worker
renders it, and the result is served from object storage.

---

## Status

All eight phases are built. **359 Python tests and 26 browser tests pass**, and
the full loop has been run for real: submit through the API, a worker claims
and renders, the file lands in storage, the API serves it back.

| | |
|---|---|
| Render core | done — deterministic, ~4× faster than real time |
| Captions | done — burned in, exactly synced, plus `.srt` |
| Sandbox | done — bubblewrap, no network, no secrets |
| Generation pipeline | done — exercised against the live Gemini API |
| Queue and worker | done — Postgres leases, heartbeat, reaper |
| HTTP API | done — quota enforced before any spend |
| Storage | done — R2 or local, signed URLs |
| Billing | done — Stripe webhooks, idempotent |
| Web UI | done — submit a topic or a script, poll, play, settings |
| Illustrations | done — diagrams drawn from a spec, two styles |
| Modes | done — coding-only, illustration-only, or both |

**Not yet exercised against live services:** Cloudflare R2 (no credentials),
Stripe Checkout (needs a live key; everything after checkout is tested), and
ElevenLabs at length (the free allowance is 10,000 characters, under three
tutorials — which is why Kokoro exists).

---

> **Running it:** see [usage.md](usage.md) for copy-pasteable commands, the
> flags that matter, configuration, and troubleshooting. The short version is
> below.

## Try it offline

No keys, no quota, no spend. Hardcoded snippets and local speech:

```bash
pip install -e engine/
cd engine && python -m videosynx.cli --demo --execute --out demo.mp4
```

`--execute` runs each snippet in the sandbox and renders its real output.
Install `espeak-ng` for audible narration — otherwise you get a tone. For good
local narration, install `kokoro-onnx` and download the model:

```bash
pip install kokoro-onnx onnxruntime
mkdir -p ~/.cache/kokoro && cd ~/.cache/kokoro
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.onnx
curl -LO https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
```

## Run the whole service

```bash
createdb videosynx
pip install -e engine/ -e worker/ -e api/ -e agent/

python -m videosynx_api --port 8000          # the API
python -m videosynx_worker --once            # a worker (drop --once to loop)
cd web && npm install && npm run dev        # the UI on :3000
```

`agent/` is only needed to drive the service from an assistant rather than
the UI — see "Driving it from an assistant" in `usage.md`.

With `GEMINI_API_KEY` set, a job with no `lesson` generates its code from the
topic. With `R2_BUCKET` set, artifacts go to object storage instead of disk.
With `STRIPE_SECRET_KEY` set, `POST /billing/checkout` starts a subscription.

### Render a lesson you wrote

`--topic` asks a model to write everything. If the content already exists, a
lesson JSON renders it verbatim, with each narration sentence anchored to the
code fragment it describes:

```bash
python -m videosynx.cli --lesson my-lesson.json --execute --tts kokoro
```

A lesson has `intro`, `sections` and `outro`. A section is either `code` plus
`narration` (`[["fragment", "sentence"], ...]`), or `explain` — a narrated
slide with no code on screen, so concepts are explained before anything is
typed.

There are three ways in: a `--topic`, a `--lesson` file, or a script. Through
the API a script is sent as **text** rather than a path, so the API and the
worker never need to share a disk; that text is rendered verbatim if it is
already lesson JSON, and structured by the model once if it is prose.

### Modes

`--mode` decides which halves of a tutorial are produced:

| mode | what you get |
|---|---|
| `both` (the default) | the concept slides, then the code |
| `coding` | code alone |
| `illustration` | slides alone — no code, no sandbox, no `--execute` needed |

An illustration-only video has no code to carry it, so it asks for more slides.

### Diagrams

An `explain` slide can carry a `diagram` instead of text columns — labelled
shapes joined by arrows:

```json
{
  "layout": "row",
  "nodes": [
    {"id": "req", "label": "Request", "shape": "box"},
    {"id": "q",   "label": "Queue",   "shape": "cylinder"}
  ],
  "edges": [["req", "q", "enqueue"]]
}
```

`layout` is one of `row`, `column`, `grid`, `cycle`, `tree`, `stack`; `shape`
is one of `box`, `round`, `cylinder`, `cloud`, `document`, `person`, `stack`.
Add `"repeat": 3` to a node for "a lot of these". Nodes reveal one per
sentence exactly as columns do, and an edge appears once both of its ends are
on screen.

**Nothing here is an image.** A diagram is drawn from its spec, so the same
lesson produces byte-identical frames on every render — the hand-drawn wobble
is seeded from the diagram's own text, never from Python's `hash()`, which is
salted per process. `sketch` (the default for slides) draws on paper in
pencil; `clean` draws the same shapes in the dark palette.

### The explain slide

An `explain` slide holds a `heading`, `columns` and a `note` (or a `diagram`).
It reveals one column or node per sentence, so **each narration entry must be
exactly one sentence**:
the heading holds for the first, then each sentence lights the next column,
then the note lands once everything is up. Write one entry as two sentences
and it silently becomes two reveal steps, and the columns run a beat ahead of
the voice. `lessons/` holds worked examples, and the test suite checks every
lesson there for this.

---

## How it works

```
topic
  └─ slides explain the concept          (no code on screen yet)
       └─ Gemini writes the code, in separately runnable snippets
            └─ each snippet runs in a bubblewrap sandbox
                 └─ narration is written anchored to fragments of the code
                      └─ a voice speaks it, one sentence at a time
                           └─ frames are rendered and piped into ffmpeg
```

### The parts worth knowing about

**Nothing is screen-captured.** Frames are drawn directly and piped to ffmpeg
as raw RGB, so there is no display, no window manager, no editor and no
keystroke simulation. It runs faster than real time, and many jobs fit on one
box — which the screen-recording approach it replaced could not do.

**The timeline is a plan, not a recording.** `FPS=30` and `SR=48000`, so one
frame is exactly 1600 audio samples. Every span carries an integer frame count
and everything else derives from those integers — there is no wall clock
anywhere in the timing path, so audio and video cannot drift apart.

**Narration is anchored to the code.** Each sentence carries the character
offset it describes and is spoken while that part of the code is on screen.
Without this the opening line talks about the topic in general while the first
line of code is being typed, and the viewer has nothing to attach it to.

**Concepts get slides, not code.** Explaining over typing code makes the viewer
read code before they know what it is for. An explanation section is narrated
over an illustration, and the code starts once the narration hands over to it.

**Generated code is treated as hostile.** It is model output derived from
user-supplied input, so it runs in bubblewrap with no network, no readable home
directory, and no inherited environment. If isolation cannot be established the
worker refuses to start rather than falling back.

**Rendering is free; writing is what costs.** Frames are drawn with Pillow and
piped to ffmpeg, and Kokoro narrates on the same CPU, so a video costs nothing
to make. Only *generating* the content — the code and narration for a topic,
or structuring prose — calls a model. A spent free allowance is the ordinary
way this runs rather than a fault, so the providers stand in for one another:
Gemini first, then Groq, Hugging Face or OpenRouter, whichever have keys, and
only a job where all of them failed is reported as failed. `generation: "local"` refuses to do that
at all: it renders lesson JSON you supply, and structures prose itself — scene
markers become slides, hard-wrapped lines are rejoined, and the writer's own
words go on screen. Only a bare topic is refused, before it is queued rather
than queued and failed.

**The tier is enforced, not just declared.** Which voice engine, what
resolution, whether there is a watermark and how many snippets are all decided
by the tier and merged over anything the client sent, so a free-tier job
cannot ask for the paid voice. The user's own settings — mode, style,
audience, focus, voice, captions, length — are saved server-side and layered
underneath.

**Money is guarded before it is spent.** Submitting a job validates, checks
quota and inserts a queued row — it never constructs a client that costs
anything. Quota is enforced in the same transaction as the insert, behind an
advisory lock, so two concurrent submits cannot both pass a check with room for
one. Stripe webhooks are recorded against their event id, so a retry grants
once.

**A failure is retried, not reported.** A job that fails is queued again after
a growing wait — a minute, then five, then out to a day — and the page says
*Retrying* rather than *Failed* for as long as that is true. The wait is the
point: this requeued immediately, so the second attempt started a second after
the first and found conditions unchanged, which made it worth nothing. Seven
attempts over about 31 hours outlasts a daily quota reset. Only work that was
*declined* rather than attempted is not retried, because the same input is
declined in exactly the same way.

---

## Layout

```
engine/     the renderer, timeline, sandbox and generation pipeline
worker/     the Postgres queue, storage backends, and the worker loop
api/        FastAPI: submit, status, quota, billing, video delivery
agent/      MCP server: an assistant submits and reads back over the API
web/        Next.js frontend
lessons/    hand-written lesson JSON, checked by the test suite
```

Three rules hold the boundaries: the API imports the worker (shared data layer)
and never the reverse, storage lives with the worker so the dependency runs one
way, and `agent/` imports none of them — it is an HTTP client of the API, which
is what stops it from becoming a second place where quota and credits are
decided.

## Requirements

- Linux, Python 3.11+
- `ffmpeg` — encoding
- `bubblewrap` — sandboxing generated code
- PostgreSQL — the job queue
- Node 20+ — the frontend
- `espeak-ng` or Kokoro — local narration

## Development

```bash
pip install -e "engine[dev]" -e worker/ -e api/ -e "agent[dev]"
pytest                      # from the repository root: 459 tests
cd web && npx playwright test   # needs videosynx_e2e
```
