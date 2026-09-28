# Usage

Everything below is copy-pasteable **from any directory**. Set this once per
shell and the rest works anywhere:

```bash
export PYCODING=~/Software/pycoding
```

### Where things live

```
$PYCODING/.venv/     the virtualenv — NOT inside engine/
$PYCODING/engine/    the renderer and pipeline
$PYCODING/worker/    the queue and worker
$PYCODING/api/       the HTTP API
$PYCODING/web/       the Next.js UI
```

> **The most common mistake:** the virtualenv is at the repository root, not in
> `engine/`. Running `.venv/bin/python` from inside `engine/` gives
> `No such file or directory`. Either use the absolute path as shown below, or
> activate it first (see [Activating the venv](#activating-the-venv)).

---

## 1. Make a video — one command

```bash
$PYCODING/.venv/bin/pycoding \
    --topic "Python decorators explained" \
    --execute \
    --out /tmp/decorators.mp4
```

Writes `decorators.mp4` and `decorators.srt`. Uses Gemini for the code and
narration and Kokoro for the voice, so **this run costs nothing**.

`--execute` matters. Without it nothing actually runs, and the terminal panel
in the video stays empty. With it, every snippet executes inside the sandbox
and its real output appears on screen.

### From a lesson you wrote

```bash
$PYCODING/.venv/bin/pycoding \
    --lesson /tmp/pycoding_spike/sequences_types.json \
    --execute \
    --out /tmp/lesson.mp4
```

### Illustration only

```bash
$PYCODING/.venv/bin/pycoding \
    --topic "when to fine-tune a model" \
    --mode illustration \
    --out /tmp/slides.mp4
```

No code is written, so there is no sandbox to enter and no `--execute` to pass.
The explanation slides carry the whole video, and they are drawn rather than
described: boxes, arrows, cylinders and people, laid out and revealed one per
sentence. `--mode coding` is the other end — code alone, no slides.

### No keys, no quota, nothing spent

```bash
$PYCODING/.venv/bin/pycoding --demo --execute --out /tmp/demo.mp4
```

---

## 2. The full service

Three terminals, all using the absolute path:

```bash
# terminal 1 — the API
$PYCODING/.venv/bin/pycoding-api --port 8000

# terminal 2 — a worker (drop --once to keep looping)
$PYCODING/.venv/bin/pycoding-worker

# terminal 3 — the UI
cd $PYCODING/web && npm run dev
```

Open **http://localhost:3000**, type a topic, and watch it go
`queued → writing code → rendering → ready`. Click **Watch** to play it.

The API creates its own schema on startup and serves the UI on a different
origin, so CORS is configured for `localhost:3000` by default.

### Verifying it works without the UI

```bash
curl -s localhost:8000/health
curl -s localhost:8000/me/quota -H 'X-Dev-User: me:free'
curl -s -X POST localhost:8000/jobs \
     -H 'X-Dev-User: me:free' -H 'Content-Type: application/json' \
     -d '{"topic":"Python decorators"}'
```

`X-Dev-User` is a development bypass. It is honoured **only** while
`SUPABASE_JWT_SECRET` is unset, so it cannot survive into a deployment.

### Sending a script instead of a topic

A script travels with the job as **text**, so the API and the worker never need
to share a disk. If it is already lesson JSON it renders verbatim; anything
else is sent to the model once to be structured.

```bash
curl -s -X POST localhost:8000/jobs \
     -H 'X-Dev-User: me:free' -H 'Content-Type: application/json' \
     -d '{"script":"# When to fine-tune\n\nScene 1. A manager sets a deadline.","mode":"illustration"}'
```

`topic` is optional when a script is given — the job is named from the
script's first line instead, so it does not appear as a blank row.

### Settings

Saved per user on the server, so they follow the account rather than the
browser. The panel sits behind **Settings** on the page, and the same thing is
reachable directly:

```bash
curl -s localhost:8000/me/settings -H 'X-Dev-User: me:free'

curl -s -X PUT localhost:8000/me/settings \
     -H 'X-Dev-User: me:free' -H 'Content-Type: application/json' \
     -d '{"mode":"illustration","style":"sketch","audience":"expert",
          "focus":"keep it practical","voice":"af_heart","captions":true,
          "length":"medium"}'
```

A `PUT` replaces the whole object, so a field left out returns to its default
rather than keeping a value nobody can see. An unknown field is refused.

What is **not** settable is the tier's business: voice engine, resolution,
watermark and snippet cap. Those are decided by the plan and merged over
anything a client sends, so a free-tier job cannot ask for the paid voice.

---

## 3. Tests

```bash
$PYCODING/.venv/bin/pytest                  # 242 tests
cd $PYCODING/web && npx playwright test     # 3 browser tests
```

The queue tests need Postgres:

```bash
createdb pycoding_test    # once
```

They skip with instructions if it is missing rather than failing.

---

## Activating the venv

If you would rather not type the path every time:

```bash
source $PYCODING/.venv/bin/activate
```

Then `pycoding`, `pycoding-api` and `pycoding-worker` work as plain commands.
`deactivate` undoes it. Note this changes your `PATH` for that shell only.

---

## Flags worth knowing

| flag | what it does |
|---|---|
| `--mode both\|coding\|illustration` | `coding` is code alone; `illustration` is drawn explanation alone, no code on screen |
| `--execute` | run snippets for real; without it, no terminal output |
| `--tts kokoro\|elevenlabs\|espeak` | voice. `auto` prefers Kokoro (free, local), then ElevenLabs, then espeak |
| `--voice af_heart` | pick among Kokoro's 54 voices |
| `--lesson FILE` | render authored content instead of generating it |
| `--save-script out.json` | dump the generated code and narration for inspection |
| `--model gemini-3.6-flash` | quota is **per model**, so this is how to get past a daily limit |
| `--no-intro` | skip the opening title card |
| `--width/--height` | output resolution, default 1920×1080 |
| `--no-tts` | offline speech even when ElevenLabs keys are present |

---

## What costs money

**Nothing, by default.** Kokoro runs locally and Gemini's free tier covers the
code generation.

The only thing that bills is `--tts elevenlabs`, at roughly **$0.65–1.30 per
lesson** (4,000 characters). Your ElevenLabs allowance is 10,000 characters per
period — under three lessons — so `auto` uses Kokoro instead. Ask for
`--tts elevenlabs` when the allowance covers it and you want the better voice.

Cloudflare R2 has no egress fees but bills for storage; the local backend
(`PYCODING_ARTIFACTS`, default `/tmp/pycoding/artifacts`) is used when
`R2_BUCKET` is unset.

---

## Configuration

Credentials load from `$PYCODING/.env` automatically at startup. A shell
`export` still wins over the file, so you can override one value for a single
run without editing anything.

| variable | needed for |
|---|---|
| `GEMINI_API_KEY` | `--topic` runs (not `--demo` or `--lesson`) |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | `--tts elevenlabs` |
| `PYCODING_DSN` | Postgres, default `postgresql:///pycoding` |
| `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | object storage instead of local disk |
| `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET` | billing |
| `SUPABASE_JWT_SECRET` | real auth; setting it disables the `X-Dev-User` bypass |
| `CORS_ORIGINS` | allowed browser origins, default `localhost:3000` |

---

## Troubleshooting

**`bash: .venv/bin/python: No such file or directory`**
You are in a subdirectory. The venv is at `$PYCODING/.venv`. Use the absolute
path, or activate it.

**`createdb: database "pycoding" already exists`**
Not an error. Continue.

**A job sits at `queued` forever**
No worker is running. Start one — the API only enqueues.

**`quota_exceeded` from Gemini**
Free-tier quota is **per model** and resets daily. Try
`--model gemini-3.6-flash`, or a fallback chain is built in for 503s
(which are demand spikes, not quota).

**`ElevenLabs quota exhausted`**
The account is out of characters. Use `--tts kokoro` — the videos are otherwise
identical.

**The UI says "Could not reach the API"**
With the browser involved this is usually CORS, not the server being down.
Check the API is on the port `NEXT_PUBLIC_API_URL` points at, and that the
origin is in `CORS_ORIGINS`.

**Video plays with no sound**
Check `--tts`. The offline fallback is `espeak-ng`; if that is missing the
narrator is a tone, which is easy to mistake for silence.

**The voice sounds robotic**
That is espeak-ng, the last resort. Every run prints the voice it chose —
look for the `voice:` line. `--tts kokoro` gives a neural voice locally at
no cost; `--tts elevenlabs` is the best and the only one that bills.

---

## Not yet exercised against live services

Honest list, so a failure here is expected rather than a surprise:

- **Cloudflare R2** — no credentials. The S3 path is unit-tested against a
  stubbed client for key layout and presign shape; the real store is unproven.
- **Stripe Checkout** — needs a live key. Everything *after* checkout is tested
  with locally-signed webhook payloads.
- **ElevenLabs at length** — see the quota note above.
