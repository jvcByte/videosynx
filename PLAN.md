# videosynx — the trend-to-YouTube plan

**Status:** proposed, nothing here is built yet.

The app today makes one kind of video: a narrated coding tutorial, from a topic
someone types. This plan turns it into something that watches the web for what
people are arguing about, picks a video-shaped angle, writes the explainer,
draws it as a 2D animated scene with characters that reads as having depth,
narrates it, and uploads it to a paired YouTube channel — on a schedule, in the
queue and quota system the app already has.

Two of the three pieces are already on the product's own waitlist
(`api/src/videosynx_api/features.py:30-66`): `any-topic` ("make a tutorial about
anything that is not code") and `publish` ("send a finished tutorial straight to
a channel"). This plan closes both.

---

## Contents

1. [Who owns what](#1-who-owns-what)
2. [What exists, what is added](#2-what-exists-what-is-added)
3. [The 2D system, and how depth is faked](#3-the-2d-system-and-how-depth-is-faked)
4. [Content contract](#4-content-contract)
5. [Data model](#5-data-model)
6. [Endpoints and UI](#6-endpoints-and-ui)
7. [Phases](#7-phases)
8. [Budgets and guards](#8-budgets-and-guards)
9. [Closing the waitlist](#9-closing-the-waitlist)
10. [Risks](#10-risks)
11. [Out of scope](#11-out-of-scope)
12. [Open decision](#12-open-decision)

---

## 1. Who owns what

No 3D engine anywhere. One library draws, one tool encodes, and every step below
either already exists in this repository or is a file this plan adds.

| # | Stage | Component | Tool actually used |
|---|---|---|---|
| 1 | Read the web | `engine/src/videosynx/research.py` *(new)* | stdlib `urllib` + `html.parser` → DuckDuckGo HTML |
| 2 | Choose a topic | `prompts.TOPIC_PROMPT` → `providers.default_writer()` | Gemini → Groq → HF → OpenRouter → DeepSeek *(existing chain, `providers.py:410`)* |
| 3 | Write script, cast, scenes, poses | `prompts.EXPLAINER_PROMPT` | the same model chain |
| 4 | Validate it | `lesson.parse_lesson` + `timeline.parse_scene` | our code; bad output degrades, never fails a job |
| 5 | **Draw the characters** | **`engine/src/videosynx/render/characters.py`** *(new)* | **Pillow `ImageDraw` + the existing `Pen` (`diagram.py:41`)** |
| 6 | Fake the depth | `render/depth.py`, `render/props.py` *(new)* | Pillow alpha `paste`, `ImageFilter.GaussianBlur`, `Image.transform(AFFINE)` |
| 7 | Stack the frames | `render/scene.py` *(new)*, called from `editor.py:272` | Pillow, onto the existing **RGB** canvas |
| 8 | Move the mouth | `lipsync.py` *(new)* | `numpy` RMS over the real narration PCM |
| 9 | Speak | `KokoroSpeaker` / `ElevenLabsSpeaker` *(existing)* | Kokoro ONNX local · ElevenLabs on the paid tier |
| 10 | Encode | `render/sink.py` *(existing, untouched)* | `ffmpeg` rawvideo pipe · 30 fps · AAC 48 kHz |
| 11 | Store | `worker/src/videosynx_worker/storage.py` *(existing)* | local disk · Cloudflare R2 |
| 12 | Upload | `worker/src/videosynx_worker/youtube.py` *(new)* | YouTube Data API v3 |
| 13 | Schedule | `worker/src/videosynx_worker/planner.py` *(new)* | `httpx` → our own API |

So the answer to "what draws the characters" is: **a new Python module drawing
flat 2D shapes with Pillow**, one frame at a time, piped to ffmpeg. The
three-dimensionality is an illusion made of seven 2D operations (§3), not
geometry.

**Not used, deliberately:** Blender / Unity / Godot (hundreds of megabytes,
output that is not bit-stable across drivers, minutes per video), After Effects
or Lottie (a second rendering toolchain to install and license), any projection
or lighting maths, and sprite packs to begin with.

**New engine dependencies: none.** `pillow` and `numpy` are already required
(`engine/pyproject.toml`). The only new service dependency is the Google client
in the worker (§7 P9).

---

## 2. What exists, what is added

| | Exists today | This plan adds |
|---|---|---|
| Queue | Postgres, `FOR UPDATE SKIP LOCKED`, leases, retries, reaper (`db.py:35-141`) | kinds `discovery` and `publish` |
| Render | Pillow → ffmpeg, audio and video locked frame-for-frame (`sink.py`) | a scene compositor, characters, fake depth |
| Drawing | code editor, title cards, diagrams whose steps draw themselves in (`render/editor.py`, `render/diagram.py`) | character rigs, 2D props |
| Voice | 54 Kokoro voices, ElevenLabs with a local fallback chain | a mouth driven by the real audio |
| Model | five-provider failover chain | an explainer prompt and a topic prompt |
| Quota / billing | `quota.py`, `billing.py`, credit ledger | `publish` jobs kept off the paid allowance |
| Auth | Supabase JWT plus a dev header (`auth.py`) | a service token, and Google OAuth |
| Storage | R2 or local (`storage.py`), signed URLs | an `.srt` and publish metadata |
| UI | dashboard views, settings, composer | a Discover view, channel connection, `look`/`motion` |

Two gaps in what exists, worth naming because this plan depends on them:

- `Settings.length` is accepted (`settings.py:44`) and **read by nobody** — the
  worker never looks at it, so "short / medium / long" is currently decoration.
  P8 wires it.
- The SRT writer exists (`engine/src/videosynx/srt.py`) but only the CLI writes
  a file; the worker produces an MP4 and nothing else. P8 produces it.

---

## 3. The 2D system, and how depth is faked

Seven 2D operations, each cheap on its own, applied together:

1. **Layer parallax.** Five planes; a camera pan moves each by `pan × factor`.
   Depth reads as *motion* before it reads as geometry.
2. **Scale with depth.** Nearer objects are drawn larger, and a dolly scales the
   planes at different rates.
3. **Drawn extrusion.** A prop is its outline, a second outline offset by
   `(dx, dy)` in a darker tone, and a filled polygon joining them. Three flat
   polygons that read as a solid block.
4. **Two-tone facets.** One light direction per `Style`, two hard tone steps — a
   lit facet and a shaded facet. No light maths.
5. **Contact shadows.** A blurred dark ellipse under every object, its height
   and skew set by depth. The most depth per line of code that exists.
6. **Atmospheric fade.** Far planes are blended toward the paper colour.
7. **Baked depth of field.** Far planes get one or two pixels of blur, computed
   **once per slide**, never per frame.

Characters turn by the 2D convention rather than by rotation: `front`,
`three_quarter` and `profile` are *one rig* with shoulder width, ear offset, eye
spacing and face width as parameters.

Depth is a single number, `z ∈ [0, 1]`, carried by every layer and every object;
draw order is `z` ascending.

| Plane | z | scale | atmosphere | blur (baked) | parallax | shadow |
|---|---|---|---|---|---|---|
| backdrop / sky / map wall | 0.00 | 0.70 | 55 % | 2.0 px | 0.15 | — |
| back props | 0.25 | 0.82 | 35 % | 1.0 px | 0.35 | 20 % |
| cast and ground line | 0.55 | 1.00 | 10 % | 0 | 1.00 | 35 % |
| front props | 0.85 | 1.18 | 0 % | 0 | 1.45 | 45 % |
| caption / pill / watermark | 1.00 | 1.00 | 0 | 0 | 0 | text shadow |

**Clamped in code, not left to taste:** `|pan| ≤ 2 % of width per second`,
`zoom ∈ [0.92, 1.10]`, `shake ≤ 1.5 px`. Past those numbers the illusion stops
being depth and becomes a slide wobbling. The clamps are asserted in tests.

**Honest ceiling:** this looks like flat paper-cut or explainer-video art. It
will not look like a rendered 3D scene, and it is not meant to.

---

## 4. Content contract

The explainer prompt returns a lesson, plus a cast and a scene per slide. The
shape is the existing lesson JSON with two additions, so `parse_lesson`
(`lesson.py:102`) keeps working untouched — it reads only `title`, `intro`,
`sections` and `outro`, and ignores everything else.

```json
{
  "title": "Who would win: USA vs Iran",
  "cast": [
    {"id": "usa",  "name": "United States", "side": "left",  "palette": "steel"},
    {"id": "iran", "name": "Iran",          "side": "right", "palette": "sand"}
  ],
  "sections": [
    {
      "name": "The opener",
      "explain": {
        "heading": "Two militaries, very different shapes",
        "scene": {
          "shot": "two-shot",
          "setting": "map_table",
          "camera": {"pan": "right", "dolly": 1.04},
          "props": [
            {"name": "map",  "at": "centre", "z": 0.25},
            {"name": "flag", "at": "usa",    "z": 0.85}
          ],
          "cast": [
            {"id": "usa",  "pose": "explaining",   "expression": "calm",   "turn": "three_quarter"},
            {"id": "iran", "pose": "arms_crossed", "expression": "worried", "turn": "front"}
          ]
        },
        "steps": [
          {"pose": {"iran": "pointing"}, "pill": "Asia-Pacific", "tone": "busy"}
        ],
        "narration": ["One.", "Two.", "Three.", "Four."]
      },
      "publish": {
        "description": "A short, sourced look at how the two forces compare.",
        "tags": ["geopolitics"],
        "made_for_kids": false
      }
    }
  ]
}
```

The vocabulary is closed and is listed in the prompt the way the diagram
vocabulary already is (`prompts.py:181-220`):

- **poses:** `neutral`, `explaining`, `pointing`, `arms_crossed`, `shrugging`,
  `alert`, `defeated`, `celebrating`, `thinking`, `presenting`
- **expressions:** `calm`, `confident`, `worried`, `angry`, `surprised`, `smug`
- **turns:** `front`, `three_quarter`, `profile`
- **shots:** `two-shot`, `closeup`, `split`, `wide`, `insert`
- **props:** `globe`, `map`, `flag`, `tank`, `jet`, `ship`, `missile`,
  `building`, `factory`, `wall`, `city`, `oil_rig`, `money`, `chart`, `arrow`,
  `table`

Invariants this must not break:

- narration sentences = things revealed + 1 (the rule at `prompts.py:236`);
- `publish` is inert to `parse_lesson`, which is pinned by a test;
- a slide with **no** `scene` takes today's code path byte for byte, so every
  existing tutorial renders exactly as it does now.

---

## 5. Data model

Appended to the `SCHEMA` string in `worker/src/videosynx_worker/db.py`, in the
same idempotent style as the tables already there (`db.py:97-140`).

```sql
create table if not exists channels (
  id              uuid primary key default gen_random_uuid(),
  user_id         text not null,
  platform        text not null default 'youtube',
  channel_id      text,
  title           text,
  -- Sealed with VIDEOSYNX_TOKEN_KEY. A refresh token is a standing key to
  -- someone's channel; it is never stored, logged or returned in the clear.
  refresh_token   text not null,
  niche           text not null default '',
  cadence_hours   integer not null default 24,
  length          text not null default 'medium',
  privacy         text not null default 'private',
  publish         text not null default 'review',   -- 'review' | 'auto'
  enabled         boolean not null default false,
  last_planned_at timestamptz,
  created_at      timestamptz not null default now(),
  unique (user_id, platform, channel_id)
);

-- One research pass: what was searched, what came back, and what the model
-- thought was worth making. Kept so a topic can be explained after the fact.
create table if not exists discoveries (
  id         uuid primary key default gen_random_uuid(),
  user_id    text not null,
  channel_id uuid,
  query      text not null,
  brief      jsonb not null default '{}',
  candidates jsonb not null,
  job_id     uuid,
  created_at timestamptz not null default now()
);

-- The primary key does the work: a publish job that is retried cannot upload
-- the same video twice, for the same reason stripe_event_id makes a webhook
-- delivered twice grant once.
create table if not exists publications (
  job_id     uuid primary key,
  platform   text not null,
  remote_id  text,
  url        text,
  status     text not null,
  error      text,
  created_at timestamptz not null default now()
);
```

---

## 6. Endpoints and UI

All of these follow the existing shape: `Depends(current_user)`,
`submit_within_quota`, `JobResponse` from the queue (`app.py:1073-1139`).

```
POST   /discover                    queue a research pass        -> { id, status }
GET    /discover                    recent passes and candidates
POST   /discover/{id}/make          turn one candidate into a video job
POST   /jobs/{id}/publish           queue an upload (idempotent)
GET    /channels
POST   /channels/youtube/start      -> { url } to send the browser to
GET    /channels/youtube/callback   exchange the code, store a sealed token
PUT    /channels/{id}               niche, cadence, length, privacy, publish, enabled
DELETE /channels/{id}
```

UI additions:

- `Discover` beside the existing entries in `web/app/components/Sidebar.tsx:34-39`,
  a label in `dashboard/page.tsx:31`, a branch in its view switch
  (`page.tsx:386-407`), and a new `DiscoverView` in `dashboard/views.tsx`;
- channel connection plus `look` and `motion` selects in
  `components/SettingsPanel.tsx`;
- nine calls and their types in `web/app/lib/api.ts`.

---

## 7. Phases

Each phase is one commit's worth of work, has its own tests, and leaves the repo
working. P0 through P6 are the video; P7 through P12 are where the topic comes
from and where the finished file goes.

### P0 — measure before drawing anything *(blocking)*

`render_plan` (`engine/src/videosynx/render/__init__.py:42`) gains a counter for
frames and distinct states, logged at the end of a render. The 1080p baseline is
recorded as a constant that `engine/tests/test_render_budget.py` asserts against
from then on.

*Why first:* animation is a per-tick repaint. Without a measured baseline, a
later phase quietly turns a forty-second render into a six-minute one, the queue
fills, and nobody can tell which change did it.

### P1 — characters

New `engine/src/videosynx/render/characters.py`.

- A rig: head, neck, torso, hips, two upper arms, two forearms, two legs, feet.
- Ten poses as **joint angle tables**, interpolated over about six ticks, so a
  pose change animates instead of switching.
- Idle: a two-pixel vertical bob on a sixteen-tick sine, seeded per character id
  with `seed_for` (`render/diagram.py:36`), so two renders are identical.
- Blink every three to five seconds, seeded the same way.
- Six expressions: brows and mouth corners.
- `mouth ∈ [0, 1]`, fed from P6.
- `turn` parameterises shoulder width, ear offset, eye spacing and face width.

Everything is stroked with the existing `Pen` (`render/diagram.py:41`), so
characters inherit both styles for free: wobbly pencil in `sketch`, ruled in
`clean`. No fonts, no asset files.

Pose cache: one ~300×450 RGBA tile per `(pose, tick bucket, mouth bucket,
expression, turn)`, LRU-capped at 96 tiles (~50 MB), because the per-frame cost
is what decides whether motion is affordable.

*Tests:* tables resolve; unknown pose falls back; identical seeds give identical
bytes; three ticks differ; `mouth` 0 and 1 differ; a cache hit does not mutate
the tile.

### P2 — props with drawn extrusion

New `engine/src/videosynx/render/props.py`. Sixteen named props, each an outline
plus a lit facet plus a shaded facet plus an extrusion polygon, with
`extrude ∈ [0, 1]`. `papercut` adds a light paper edge and a stronger offset
shadow.

*Tests:* every name renders; unknown name becomes a box; `extrude=0` equals the
flat outline; nothing draws outside its declared bounds.

### P3 — depth and camera

New `engine/src/videosynx/render/depth.py`: `Plane`, `Camera`, `parallax`,
`atmosphere`, `contact_shadow`, `bake_blur`, and the clamps from §3.

*Tests:* plane order; parallax monotonic in `z`; atmosphere stays in range; an
over-fast pan is clamped; the near plane is never blurred.

### P4 — the compositor

New `engine/src/videosynx/render/scene.py`, entered from
`Editor._paint_slide` (`render/editor.py:272`) **only when `slide.scene` is
set**. Static layers (backdrop, back props, skewed floor) are baked once per
slide; per tick only character tiles are pasted.

Compositing is `canvas.paste(tile, (x, y), tile)` onto the existing RGB canvas
— verified `editor.py:96` and `:554` — so there is no full-frame RGBA pass and
no per-frame conversion.

Order: backdrop → back props → floor → cast → front props → labels and pill →
caption band → watermark.

*Tests:* layer order puts front props after the cast; the caption band is never
occluded; a `scene=None` slide is byte-identical to today's output.

### P5 — the content contract

- `prompts.py`: `EXPLAINER_PROMPT` and `TOPIC_PROMPT`, plus
  `explainer_prompt(topic, brief, length, focus)` and `parse_explainer(text)`,
  which reuses `parse_json_object` (`prompts.py:328`).
- `timeline.py`: `Scene`, `SceneCast`, `SceneProp` and `parse_scene` beside
  `parse_diagram` (`:183`), defensive in the same way — unknown pose or prop
  becomes the default, an unknown character id is dropped, counts bounded with
  `_bounded_int` (`:176`). `Slide` gains `scene`, `tick` and `mouth`;
  `fold_steps` (`:820`) stamps tick and mouth onto each folded slide.
- `lesson.py`: `_load_explain` passes `scene=parse_scene(spec.get("scene"))`.
- `api/settings.py`: `look` and `motion` join the user's own settings, next to
  `style`, because they cost CPU rather than money.
- `render_plan(look=..., motion=...)`, wired from the job's options.

*Tests:* `parse_scene` drops unknown cast and bounds counts; `mode=coding`
renders unchanged; the API and the engine still agree on the mode list
(`api/tests/test_api.py:118`).

### P6 — the mouth moves with the audio

New `engine/src/videosynx/lipsync.py`: an RMS envelope per tick over
`Phrase.samples`, with attack and release smoothing. `plan_explanation`
(`timeline.py:876`) stamps `mouth` per sub-span using the split that already
exists to draw arrows in (`DRAW_STEPS`, `timeline.py:44-49, 896-916`).

No phoneme aligner, and none needed: the envelope is computed from the same
buffer that gets muxed, so it is in sync by construction.

*Tests:* envelope length equals ticks; silence is zero; a ramp is monotonic.

> **End of P6: a topic typed by hand produces a finished 2D animated explainer.**
> Everything after this is about where the topic comes from and where the file
> goes.

### P7 — reading the web

New `engine/src/videosynx/research.py`, stdlib only.

- `search(query, limit=8)` — the DuckDuckGo HTML endpoint, no key. A `Search`
  protocol so a keyed provider can replace it by configuration.
- `fetch(url, timeout=10)` — `urllib` plus an `HTMLParser` that drops
  `script`, `style`, `nav`, `footer` and `aside`, collapses whitespace, caps
  eight thousand characters a page.
- `research(topic, limit=5, budget_s=25)` → `Brief` with sources and text.
  **Never raises**: any failure returns an empty brief, because a video with a
  weaker script beats no video — the same judgement the worker already makes
  about a paid voice (`worker.py:459`).

Network calls sit behind one function so tests stub them, exactly as
`providers._post_json` (`providers.py:267`) does.

*Tests:* empty results give an empty brief; markup is stripped; caps hold; a
timeout returns what it already had.

### P8 — two new job kinds

- `worker.py:515`: dispatch becomes `speech` | `discovery` | `publish` | video.
  `discovery` runs research, then `candidates_prompt`, and writes the candidates
  to the job's options with a new `Queue.attach_options(job_id, patch)`
  (`jsonb || patch`, beside `finish`).
- The video path, when `options.brief` is present, builds the lesson with
  `explainer_prompt` instead of `build_tutorial`, writes an `.srt` beside the
  MP4 when a publish intent exists (`srt.to_srt(plan.captions)`, key
  `video_key(tier, id, ".srt")` — `app.py:412` already maps the type), and
  stores the publish metadata.
- `length` and `focus` finally reach the prompt.

*Tests:* a discovery job writes candidates and no artifact; `.srt` appears only
when a publish intent was set; `length` changes the prompt.

### P9 — publishing

New `worker/src/videosynx_worker/youtube.py` and
`worker/src/videosynx_worker/publishers.py`.

- `TokenStore` seals and opens refresh tokens with `cryptography.fernet.Fernet`
  keyed by `VIDEOSYNX_TOKEN_KEY`. `cryptography` is already installed, as a
  dependency of `pyjwt[crypto]` (`api/pyproject.toml`).
- `YouTubePublisher`: refresh, then `videos().insert(part="snippet,status",
  resumable=True)`, then `captions().insert` with our SRT. Privacy comes from
  the channel; synthetic-media disclosure is set, because the narration is
  synthesised and YouTube's policy asks to be told. The exact field name is to
  be confirmed against the live API rather than assumed.
- Failures are classified like `providers.classify_http`
  (`providers.py:275`) so `busy`, `quota` and `permanent` decide retry or
  give up, reusing the queue's retry semantics instead of inventing new ones.
- `LocalPublisher` writes a JSON stub into storage, so the whole path is
  testable with no network — the same idea as `LocalStorage` for R2.
- `publications.job_id` is the idempotency guard.

*Tests:* success records a row; a retry never re-uploads; a missing SRT still
publishes; a quota-class error is retried and a validation error is not.

### P10 — channels, and the API surface

New `api/src/videosynx_api/channels.py`, plus routes in `app.py`.

- `start_url(user_id)` builds the consent URL with a `state` that is an HMAC of
  the user id and an expiry — no session store, the same trick as
  `LocalStorage.sign` (`storage.py:76`).
- `exchange(code)` swaps the code for a refresh token and the channel identity.
- Tokens are sealed at rest and never returned by any endpoint.
- `current_user` (`auth.py:158`) gains one branch: an `X-Service-Token` header
  matching `VIDEOSYNX_SERVICE_TOKEN` resolves directly to a user id, so the
  planner can act for an account without a browser.
- `quota.py`: `publish` jobs are excluded from `used_today` and `unsettled`
  (`quota.py:126-201`), because an upload spends nothing. A `discovery` calls a
  model and stays counted.

New environment, all documented in `.env.example`: `GOOGLE_CLIENT_ID`,
`GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`, `VIDEOSYNX_TOKEN_KEY`,
`VIDEOSYNX_STATE_SECRET`, `VIDEOSYNX_SERVICE_TOKEN`, `DISCOVERY_MAX_PAGES`.

*Tests:* a tampered `state` is refused; sealing round-trips; no response ever
contains a token; a publish job does not consume the video allowance.

### P11 — the planner

New `worker/src/videosynx_worker/planner.py`, run as `videosynx-worker --plan`.

Every `PLANNER_INTERVAL_S` (default 300), for each enabled channel whose
`last_planned_at` is older than its `cadence_hours`: `POST /discover` →
`POST /discover/{id}/make` with the top candidate → and if the channel says
`publish='auto'`, `POST /jobs/{id}/publish` when the render finishes. With
`publish='review'` it stops there and the video waits in the UI.

It is an **HTTP client of the API, never a second database writer**. That keeps
quota, pricing and billing in the one place they already are, and it means the
planner cannot create a job the API would have refused.

*Tests:* cadence is respected; a disabled channel is skipped; `review` never
publishes; one discovery per cycle.

### P12 — the UI

As §6, with `pnpm typecheck` and `pnpm lint` green — CI gates all three checks
(`.github/workflows/ci.yml`).

---

## 8. Budgets and guards

1. **Render time.** `motion="keys"` (four ticks a second) at most 1.8× the P0
   baseline; `"smooth"` (eight) at most 3.0×. Enforced by test, logged per job.
2. **Memory.** Pose cache ≤ 96 tiles, ~64 MB. At most six baked static layers per
   slide, dropped when the slide changes.
3. **Determinism.** Every random call is seeded from `seed_for(id + tick)`. Two
   renders of one lesson produce identical frames, and a test says so.
4. **Degradation.** Unknown pose, prop, look or motion falls back to a default.
   A slide with no scene takes the old path. No Google credentials means no
   upload, not a failed render.
5. **Untrusted text.** Whatever the web says is prompt input only. It is never
   executed, never echoed back as code, and is capped far under
   `MAX_SCRIPT_CHARS` (60 000, `app.py:83`).

---

## 9. Closing the waitlist

`api/src/videosynx_api/features.py:30-66` already lists this plan's two halves,
as things not built yet:

- **`any-topic`** — "Make a tutorial about how to build a battery, or a rocket,
  or anything else that is not code." Its stated blocker is that the whole
  pipeline writes and runs Python. Closed by P5 and P8.
- **`publish`** — "Send a finished tutorial straight to a channel." Its stated
  blocker is that the MP4 and the SRT already exist, so it is a grant and an
  upload per platform. Closed by P9 and P10.

So both entries come out, and one goes in: **`auto-pilot`** — "It picks
tomorrow's video itself, and puts it where you told it to."

`api/tests/test_features.py:62-93` uses `any-topic` as its sample id and must
move to `narration-languages` in the same commit. The landing page and the
dashboard render whatever the list holds (`page.tsx:403`, `views.tsx:410`) and
need no change.

---

## 10. Risks

1. **The art ceiling.** Procedural rigs read as clean flat cartoons. If the
   target is a richer look, the same architecture takes licensed sprite packs
   behind the same `draw_character` signature — only the art source changes.
2. **Over-animation.** The illusion dies the moment the camera moves fast or the
   parallax is large, which is why the clamps in §3 are code and tests rather
   than intentions.
3. **Prompt complexity.** Cast plus scene plus steps plus narration in one JSON
   is more to ask of a model. The parser is defensive and a missing or broken
   `scene` falls back to today's diagram slide, so the worst case is a plainer
   video rather than a failed job.
4. **`Slide` equality** is compared once per span (`editor.py:241`). `Scene` must
   stay immutable tuples of primitives — no numpy inside — or the render loop
   gets slower for free.
5. **Render time** is the failure nobody notices until the queue is behind, which
   is why P0 comes first and the budget is a test.
6. **YouTube's own limits.** An upload costs 1 600 units of a 10 000 a day quota,
   so roughly six a day per channel. `cadence_hours` should be validated at four
   or more.

---

## 11. Out of scope

Any true 3D, per-frame compositing of 3D assets, viseme-accurate lip-sync (it
needs forced alignment), photoreal assets, auto-generated thumbnails, TikTok and
Instagram posting, and voice cloning.

---

## 12. Open decision

**Character art.** Procedural 2D rigs, as planned — no assets, no licensing —
or licensed 2D sprite packs dropped into the same `draw_character` signature.
Everything else in this document is the same either way, but the choice has to
be made before P1 lands, because it decides what P1 draws.

---

*Written as a plan, not a promise. Every file path and line number above was
checked against the repository as it stands.*
