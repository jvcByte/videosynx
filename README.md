# pycoding

Generate narrated coding-tutorial videos from a topic.

Give it a subject and it writes the code, runs it, explains each line while the
code types itself out on screen, narrates it, and produces an `.mp4` with
captions and a matching `.srt`.

---

## Status

The rendering engine, the sandbox and the generation pipeline are built and
tested (70 tests). **The pipeline has not yet been run against the live Gemini
and ElevenLabs APIs** — that needs API keys and spends real quota, so it is
deliberately the last thing to wire up.

| | |
|---|---|
| Render core | done — deterministic, ~4× faster than real time |
| Captions | done — burned in, exactly synced, plus `.srt` |
| Title card | done |
| Sandbox | done — bubblewrap, no network, no secrets |
| Generation pipeline | done, tested offline against stand-ins |
| Live API run | **not yet exercised** |
| Service layer (API, queue, billing, web) | not started |

---

## Try it offline

No keys, no quota, no spend. Uses hardcoded snippets and offline speech:

```bash
cd engine
pip install -e .
python -m pycoding.cli --demo --execute --out demo.mp4
```

`--execute` runs each snippet in the sandbox and renders its real output.
Without it, the output is canned. Install `espeak-ng` for audible narration —
otherwise you get a tone:

```bash
sudo apt install espeak-ng bubblewrap
```

## Run it for real

```bash
export GEMINI_API_KEY=...
export ELEVENLABS_API_KEY=...
export ELEVENLABS_VOICE_ID=...

python -m pycoding.cli --topic "Python decorators explained" \
    --execute --out tutorial.mp4
```

`--no-tts` runs real code generation against offline speech, which is a cheap
way to check the prompts without spending anything.

---

## How it works

```
topic
  └─ Gemini writes the code, in separately runnable snippets
       └─ each snippet runs in a bubblewrap sandbox
            └─ Gemini writes narration, anchored to fragments of the code
                 └─ ElevenLabs speaks it, one sentence at a time
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
offset it describes and is spoken while that part of the code is on screen,
rather than being a paragraph spread evenly across the snippet. Without this
the opening line talks about the topic in general while the first line of code
is being typed, and the viewer has nothing to attach it to.

**Generated code is treated as hostile.** It is model output derived from
user-supplied input, so it runs in bubblewrap with no network, no readable
home directory, and no inherited environment. If isolation cannot be
established the worker refuses to start rather than falling back to running
it unsandboxed.

**Narration is synthesised a sentence at a time**, which is what makes caption
timing exact: each caption spans the frames of the audio it was actually
spoken over. Measured at 0 ms offset across a full render.

---

## Layout

```
engine/                # the package: renderer, timeline, sandbox, pipeline
  src/pycoding/
    timeline.py        # the frame plan
    render/            # editor canvas, ffmpeg sink
    sandbox.py         # bubblewrap isolation
    execute.py         # run a snippet, capture its transcript
    prompts.py         # what the model is asked for
    providers.py       # Gemini and ElevenLabs, behind protocols
    script.py          # topic -> render plan
    cli.py             # entry point
  tests/
pyproject.toml         # workspace: shared tool config only
```

The old screen-capture engine is kept on the `legacy-engine` branch.

## Requirements

- Linux, Python 3.11+
- `ffmpeg` — encoding
- `bubblewrap` — sandboxing generated code
- `espeak-ng` — optional; offline narration for development

## Development

```bash
pip install -e "engine[dev]"
pytest              # from the repository root
```
