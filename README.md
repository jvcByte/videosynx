# pycoding

Generate narrated coding-tutorial videos from a topic.

Give it a subject and it writes the code, runs it, explains each line while the
code types itself out on screen, narrates it, and produces an `.mp4` with
captions and a matching `.srt`.

It also runs as a hosted service: submit a topic through the web UI, a worker
renders it, and the result is served from object storage.

---

## Status

All eight phases are built. **147 Python tests and 3 browser tests pass**, and
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
| Web UI | done — submit, poll, play |

**Not yet exercised against live services:** Cloudflare R2 (no credentials),
Stripe Checkout (needs a live key; everything after checkout is tested), and
ElevenLabs at length (the free allowance is 10,000 characters, under three
tutorials — which is why Kokoro exists).

---

## Try it offline

No keys, no quota, no spend. Hardcoded snippets and local speech:

```bash
pip install -e engine/
cd engine && python -m pycoding.cli --demo --execute --out demo.mp4
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
createdb pycoding
pip install -e engine/ -e worker/ -e api/

python -m pycoding_api --port 8000          # the API
python -m pycoding_worker --once            # a worker (drop --once to loop)
cd web && npm install && npm run dev        # the UI on :3000
```

With `GEMINI_API_KEY` set, a job with no `lesson` generates its code from the
topic. With `R2_BUCKET` set, artifacts go to object storage instead of disk.
With `STRIPE_SECRET_KEY` set, `POST /billing/checkout` starts a subscription.

### Render a lesson you wrote

`--topic` asks a model to write everything. If the content already exists, a
lesson JSON renders it verbatim, with each narration sentence anchored to the
code fragment it describes:

```bash
python -m pycoding.cli --lesson my-lesson.json --execute --tts kokoro
```

A lesson has `intro`, `sections` and `outro`. A section is either `code` plus
`narration` (`[["fragment", "sentence"], ...]`), or `explain` — a narrated
slide with no code on screen, so concepts are explained before anything is
typed.

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

**Money is guarded before it is spent.** Submitting a job validates, checks
quota and inserts a queued row — it never constructs a client that costs
anything. Quota is enforced in the same transaction as the insert, behind an
advisory lock, so two concurrent submits cannot both pass a check with room for
one. Stripe webhooks are recorded against their event id, so a retry grants
once.

---

## Layout

```
engine/     the renderer, timeline, sandbox and generation pipeline
worker/     the Postgres queue, storage backends, and the worker loop
api/        FastAPI: submit, status, quota, billing, video delivery
web/        Next.js frontend
```

Two rules hold the boundaries: the API imports the worker (shared data layer)
and never the reverse, and storage lives with the worker so the dependency runs
one way.

## Requirements

- Linux, Python 3.11+
- `ffmpeg` — encoding
- `bubblewrap` — sandboxing generated code
- PostgreSQL — the job queue
- Node 20+ — the frontend
- `espeak-ng` or Kokoro — local narration

## Development

```bash
pip install -e "engine[dev]" -e worker/ -e api/
pytest                      # from the repository root: 147 tests
cd web && npx playwright test   # 3 browser tests, needs pycoding_e2e
```
