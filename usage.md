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

`X-Dev-User` is a development bypass. It is honoured **only** while nothing is
configured to check a token with, so it cannot survive into a deployment.

### Signing in

The API is user-scoped throughout — jobs, settings, allowance, retries, video
URLs — and the browser identifies itself with a Supabase session token once a
project is configured.

**Both halves have to be configured, or neither.** A server with a credential
set ignores `X-Dev-User`, so a client still sending one is simply
unauthenticated and every request comes back 401.

| on the API | on the web app |
|---|---|
| `SUPABASE_URL` (the key set is found under it), or `SUPABASE_JWKS_URL` for a self-hosted one | `NEXT_PUBLIC_SUPABASE_URL` |
| `SUPABASE_JWT_SECRET` — only for a project created before Supabase moved to asymmetric keys | `NEXT_PUBLIC_SUPABASE_ANON_KEY` |
| `SUPABASE_JWT_AUDIENCE`, default `authenticated` | |

With **neither** set on both sides, the app behaves exactly as it always has:
no sign-in screen, the development header, everything runnable with no Supabase
account. That is what local development and the browser tests use.

Supabase now issues **ES256** tokens verified against a published key set, and
the API verifies those by default. The shared secret is the older scheme and
still works, but a project created today will not use it. A key set that cannot
be reached answers **503, not 401**, deliberately: the token may be perfectly
good and a 401 would sign someone out of a working session over a blip.

**Every account is on the free tier.** Nothing sets a `tier` claim yet, so
billing and sign-in are not connected — upgrading is a separate piece of work,
not something a token currently carries.

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

**A script replaces the topic and the mode.** It carries its own content, so
neither `mode` nor `topic` is applied. The page dims both controls once a script
is present and says which of the two the script will produce, because "the mode
is not applied" reads as a limitation rather than as an answer:

- **has a ```` ``` ```` block** — its code scenes render as code, the rest as
  illustrated slides. This is how "Both" works in local mode.
- **no code in it** — illustrated slides throughout, and the page says there is
  no code in it to show, and how to add some.

Neither of the two scripts pasted so far has a fence, so both are in the second
case.

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

### Working with no allowance left

There are **two** allowances and they are counted apart, which is easy to
confuse because both are called quota:

| | what it limits | free tier |
|---|---|---|
| **jobs** | submissions that call a model, and so cost money | 3 a day |
| **local renders** | submissions that call no model at all | 10 a day |

The one you hit on the page is the **jobs** allowance. `quota_exceeded` with a
429 is the API refusing to queue, and it has nothing to do with the model keys
in `.env` — those are the worker's, and the API never uses one.

**A local render is not counted against the jobs allowance**, because the
moment that allowance is gone is exactly when someone reaches for the mode that
spends nothing. Counting it would lock the door from the inside. It is not free
of *server* cost though — it still renders on this machine — so it has a limit
of its own rather than none.

So with the jobs allowance spent, the page says so and points at local mode
rather than letting the submission come back as a 429. Set it under
**Settings → Generation → Local only**, or `"generation": "local"` on a
submission.

Separately, Gemini's own free tier is **20 requests per day, per model**, and
its fallback chain has five models — so it is a few hundred requests, not a
few.

A spent allowance is the ordinary way this runs, so it is not treated as a
fault. When Gemini is done for the day the job carries on with the next
provider that has a key, and only a job where *every* one of them failed is
reported as failed. Set any of these and it is picked up with no other change:

| variable | free tier | what to expect |
|---|---|---|
| `GEMINI_API_KEY` | [AI Studio](https://aistudio.google.com/apikey) | tried first — best at this prompt. 20/day per model |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com/keys) | fast, generous free limits |
| `HF_TOKEN` | [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens) | the router; `HUGGINGFACE_API_KEY` also works |
| `OPENROUTER_API_KEY` | [openrouter.ai/keys](https://openrouter.ai/keys) | `:free` models cost nothing |

The order is fixed — Gemini, Groq, Hugging Face, OpenRouter — and a provider
with no key is not offered at all rather than offered and failed into. With one
key there is no chain; with none, the error names the variables above.

Free tiers rename their models fairly often, so each list is overridable rather
than hardcoded to a name that may not exist by the time you read this:

```bash
GROQ_MODELS="openai/gpt-oss-120b,openai/gpt-oss-20b"
HF_MODELS="meta-llama/Llama-3.3-70B-Instruct"
OPENROUTER_MODELS="nvidia/nemotron-3.5-lightning:free,qwen/qwen3.8-27b:free"
```

A comma-separated list, tried in order; the first is the default and the rest
are that provider's own fallbacks. A stale name is then an environment variable
away from fixed rather than a release.

Nothing about *rendering* costs anything. Frames are drawn with Pillow, piped
to ffmpeg as raw RGB, and Kokoro narrates on the same CPU. The only thing that
costs a call is **writing** the content: the code and narration for a topic,
or the structuring of prose.

So there is a mode that never calls a model at all:

**Settings → Generation → Local only**, or `"generation": "local"` on a
submission.

It takes **prose as well as lesson JSON**, and it structures prose itself. It
does not summarise and it does not invent, so what you pasted is what gets
filmed.

**Scenes are found wherever the markers are.** A script pasted as one
continuous line — which is how they usually arrive — is split at its `Scene N —`
markers just as one with a marker per line is. The marker itself is never read
aloud. A scene number that does not go up is a *mention*, not a scene: "bring
back the engineer from Scene 1" is a sentence, and stays one.

**Each sentence gets its own thing on screen.** A slide shows one item per
narrated sentence rather than the whole scene at once, so the words arrive with
the voice instead of being dumped and then read out. A scene the writer built as
a comparison — *"On the left: … On the right: …"* — becomes two labelled panels,
using their labels and their words. Anything with a ```` ``` ```` block in it
becomes a real code section instead, which is what "Both" means in local mode.

Because there is no model, nothing is drawn that the text does not already say.
A scene with no structure in it is set as sentences revealed one at a time, and
that is the floor rather than a fallback that quietly loses content.

What it cannot do is invent a tutorial from a bare topic, and it does not
pretend to — that is refused *before it is queued*, so nothing is spent and
nothing fails later:

```bash
curl -s -X POST localhost:8000/jobs \
     -H 'X-Dev-User: me:free' -H 'Content-Type: application/json' \
     -d '{"topic":"decorators","generation":"local"}'
# 422 {"detail":{"error":"local_mode_needs_content", ...}}
```

The composer says which of the two a submission is before you press Generate:
*"Counts against your daily allowance"*, or *"No model call, no cost"*.

### When a render fails

Failing does not mean finished. A job that fails is tried again, and the page
says **Retrying** — never *Failed* — for as long as that is true. The card
carries what went wrong and when the next attempt is due, so a script that can
never be parsed is legible straight away rather than being hidden behind a day
of waiting.

The gap grows with each attempt, because the two things that most often go
wrong need very different amounts of time to clear: an overloaded model is
worth retrying in a minute, and a spent daily allowance is not worth retrying
for hours.

| attempt | wait after it fails |
|---|---|
| 1 | 1 minute |
| 2 | 5 minutes |
| 3 | 15 minutes |
| 4 | 1 hour |
| 5 | 6 hours |
| 6 | 24 hours |

Seven attempts over about 31 hours — long enough to outlast a daily quota
reset, which is the commonest cause by a wide margin. Only then is the job
marked failed, with the reason and how many times it was tried.

The one thing not retried is a job that was **refused** rather than attempted
— local mode handed a bare topic, say. Nothing went wrong and nothing was
spent, and the same input is refused in exactly the same way, so repeating it
would only delay the answer.

Once a job *has* run out of attempts it says **Failed**, and the card offers
**Try again** — the one thing a failure left you able to do:

```bash
curl -s -X POST localhost:8000/jobs/<id>/retry -H 'X-Dev-User: me:free'
```

That puts it back in the queue with a full attempt budget. **It costs no
allowance**: the row already exists and the work is the same work, and charging
again would mean the way to recover from a failure is to spend another of three
daily jobs on the same video. Only a failed job can be retried — a job that is
still running, or one that has already produced a video, is refused with a 409
saying which state it is actually in.

Deleting the wait is not an option worth wanting: a retry that starts a second
after the failure finds conditions exactly as they were, which is what the old
two-attempts-one-second-apart behaviour amounted to.

### Inspecting what was generated

The code, the narration and every diagram are generated afresh each run and
exist nowhere else, so a video that came out wrong cannot be diagnosed without
a record of it. The worker keeps one beside the video:

```bash
curl -s localhost:8000/jobs/<id>/script -H 'X-Dev-User: me:free' | jq '.sections[0]'
```

It is the same JSON `--save-script` writes from the CLI: the diagram specs,
the slide columns, and every sentence with the frame count it was spoken over.
This is what to reach for when a video is not what you expected — you can see
exactly which slide carried the wrong content, and edit it into a lesson.

Jobs submitted before this existed return a short summary of what was *asked
for* instead, marked `"no plan was saved for this job"`.

### The voices

The picker is filled from a catalogue, so it offers only names that exist:

```bash
curl -s localhost:8000/voices -H 'X-Dev-User: me:free'
```

Kokoro ships 54. A name is `<language><gender>_<name>` — `af_heart` is
American English, female; `bm_george` is British English, male. The list comes
back with English first because that is what almost every caller wants, and
fifty-four entries in alphabetical order bury it.

The catalogue is a copy of what `voices-v1.0.bin` holds, so a test loads the
real model and fails if the two ever disagree — otherwise the dropdown would
offer a voice that dies at synthesis.

---

## 3. Tests

```bash
$PYCODING/.venv/bin/pytest                  # 352 tests
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

**The file is found from wherever you start the command**, searching upward to
the repository root, so a worker started from `web/` gets the same credentials
as one started from the root. It did not always: the path was resolved against
the working directory, so a process started one level down found no `.env` at
all, loaded no key, and failed much later with "no model key is configured"
while the key sat in a file directly above it.

`GEMINI_API_KEY` is tried first, then the free fallbacks above. Every model
name shipped here has been checked against the live service — the first
versions of all three were wrong, because free tiers retire and rename models
constantly. If one stops working, check that provider's own model list and
override it rather than waiting for a release.

| variable | needed for |
|---|---|
| `GEMINI_API_KEY` | `--topic` runs (not `--demo` or `--lesson`). Tried first |
| `GROQ_API_KEY`, `HF_TOKEN`, `OPENROUTER_API_KEY` | free fallbacks for when Gemini's daily allowance is spent |
| `GROQ_MODELS`, `HF_MODELS`, `OPENROUTER_MODELS` | override that provider's model list, comma-separated |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID` | `--tts elevenlabs` |
| `PYCODING_DSN` | Postgres, default `postgresql:///pycoding` |
| `R2_BUCKET`, `R2_ENDPOINT`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | object storage instead of local disk |
| `STRIPE_SECRET_KEY`, `STRIPE_PRICE_ID`, `STRIPE_WEBHOOK_SECRET` | billing |
| `SUPABASE_URL` or `SUPABASE_JWKS_URL` | real auth; either disables the `X-Dev-User` bypass |
| `SUPABASE_JWT_SECRET` | the same, for a project older than asymmetric keys |
| `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` | sign-in in the web app |
| `CORS_ORIGINS` | allowed browser origins, default `localhost:3000` |

---

## Troubleshooting

**`bash: .venv/bin/python: No such file or directory`**
You are in a subdirectory. The venv is at `$PYCODING/.venv`. Use the absolute
path, or activate it.

**`createdb: database "pycoding" already exists`**
Not an error. Continue.

**`GET /healthz` returns 404**
It should not — the API answers to both `/health` and `/healthz`, because the
second is what a load balancer or orchestrator probes by default and a 404
there reads as a broken service rather than a wrong path. If you still see
404s, something is probing a port this API is not on: check what else on the
machine serves that path, since two projects both defaulting to port 8000 will
answer for each other.

**A job sits at `queued` forever**
No worker is running. Start one — the API only enqueues.

**A job says "Retrying"**
It failed and is coming back, which is not the same as failing. The card shows
what went wrong and when the next attempt is due. See
[When a render fails](#when-a-render-fails).

**A job says "Failed"**
It has run out of attempts. The reason is on the card, and the note says how
many times it was tried.

**`quota_exceeded` on the page, with a 429**
That is the **API's** daily job allowance, not the model's — 3 a day on the
free tier, and a `local` submission does not count against it. See
[Working with no allowance left](#working-with-no-allowance-left). It is a
different thing from a provider running out, which appears on the job itself
as `provider_quota` after it has been queued.

**`quota_exceeded` from Gemini**
Free-tier quota is **per model** and resets daily. Try
`--model gemini-3.6-flash`, or a fallback chain is built in for 503s
(which are demand spikes, not quota). Set `GROQ_API_KEY` and the job carries on
elsewhere instead of stopping — see
[Working with no allowance left](#working-with-no-allowance-left).

**`ElevenLabs quota exhausted`**
The account is out of characters. Use `--tts kokoro` — the videos are otherwise
identical.

**`no model key is configured`**
The keys are in `.env` and the process did not read it. Start the worker again
from anywhere in the repository — it searches upward for the file now, so this
should not happen, but a worker started before that change and left running
will still be carrying the empty environment it started with. A long-running
worker reads `.env` once, at startup, so a newly added key needs a restart.

**The UI says "Could not reach the API"**
With the browser involved this is usually CORS, not the server being down.
The browser calls `/api` on its own origin and Next forwards it to whatever
`PYCODING_API_URL` points at (default `http://localhost:8000`). So check the
API is running on that port — a browser-side CORS problem is no longer
possible, because every request is same-origin.

`PYCODING_API_URL` is read by the Next server when it starts, so changing it
needs a restart of `npm run dev`, not a rebuild. That is deliberate: it used
to be a `NEXT_PUBLIC_*` value, which is inlined into a client chunk that a
browser will happily cache and go stale on.

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

- **Supabase sign-in** — no project. The token path is tested against real
  ES256 tokens signed by a key generated in the test and published at a real
  JWKS endpoint, so signature, expiry and audience are all exercised; what is
  unproven is the shape of a token *Supabase* issues, and where it puts the
  claims this reads. A project that names them differently would need
  `auth.py` adjusted.
- **Cloudflare R2** — no credentials. The S3 path is unit-tested against a
  stubbed client for key layout and presign shape; the real store is unproven.
- **Stripe Checkout** — needs a live key. Everything *after* checkout is tested
  with locally-signed webhook payloads.
- **ElevenLabs at length** — see the quota note above.
