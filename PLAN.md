# videosynx — the trend-to-YouTube plan

**Status:** proposed, nothing here is built yet. Art direction settled (§3).

The app today makes one kind of video: a narrated coding tutorial, from a topic
someone types. This plan turns it into **Qrius** — a channel that watches the
web for what people are arguing about, picks a video-shaped angle, designs the
scene, gets it approved, animates it as modern 2D/2.5D cinematic explainer art
with characters, narrates it, and uploads it to a paired YouTube channel on a
schedule, inside the queue and quota system the app already has.

Two of the three pieces are already on the product's own waitlist
(`api/src/videosynx_api/features.py:30-66`): `any-topic` ("make a tutorial about
anything that is not code") and `publish` ("send a finished tutorial straight to
a channel"). This plan closes both.

---

## Contents

1. [Who owns what](#1-who-owns-what)
2. [What exists, what is added](#2-what-exists-what-is-added)
3. [Art direction](#3-art-direction)
4. [The 2D system, and how depth is faked](#4-the-2d-system-and-how-depth-is-faked)
5. [Content contract](#5-content-contract)
6. [Data model](#6-data-model)
7. [Endpoints and UI](#7-endpoints-and-ui)
8. [Phases](#8-phases)
9. [Budgets and guards](#9-budgets-and-guards)
10. [Closing the waitlist](#10-closing-the-waitlist)
11. [Risks](#11-risks)
12. [Out of scope](#12-out-of-scope)

---

## 1. Who owns what

No 3D engine anywhere. One library draws, one tool encodes, and every step below
either already exists in this repository or is a file this plan adds.

| # | Stage | Component | Tool actually used |
|---|---|---|---|
| 1 | Read the web | `engine/src/videosynx/research.py` *(new)* | stdlib `urllib` + `html.parser` → DuckDuckGo HTML |
| 2 | Choose a topic | `prompts.TOPIC_PROMPT` → `providers.default_writer()` | Gemini → Groq → HF → OpenRouter → DeepSeek *(existing chain, `providers.py:410`)* |
| 3 | Design the scene | `prompts.SCENE_PROMPT` → a **design job** | the same model chain |
| 4 | Approve it | `POST /designs/{id}/approve` + the UI | our code — nothing renders until this happens |
| 5 | Write the narration | `prompts.EXPLAINER_PROMPT` | the same model chain |
| 6 | Validate | `lesson.parse_lesson` + `timeline.parse_scene` | our code; bad output degrades, never fails a job |
| 7 | **Draw the characters** | **`engine/src/videosynx/render/characters.py`** *(new)* | **Pillow `ImageDraw` + the existing `Pen` (`diagram.py:41`)** |
| 8 | Light and fake depth | `render/light.py`, `render/depth.py`, `render/props.py` *(new)* | Pillow alpha `paste`, `ImageFilter.GaussianBlur`, `Image.transform(AFFINE)` |
| 9 | Stack the frames | `render/scene.py` *(new)* ← `editor.py:272` | Pillow, onto the existing **RGB** canvas |
| 10 | Move the mouth | `lipsync.py` *(new)* | `numpy` RMS over the real narration PCM |
| 11 | Set the type | `render/fonts.py` *(new)* | Space Grotesk · Inter · Space Mono, bundled OFL files |
| 12 | Speak | `KokoroSpeaker` / `ElevenLabsSpeaker` *(existing)* | Kokoro ONNX local · ElevenLabs on the paid tier |
| 13 | Encode | `render/sink.py` *(existing, untouched)* | `ffmpeg` rawvideo pipe · 30 fps · AAC 48 kHz |
| 14 | Store | `worker/src/videosynx_worker/storage.py` *(existing)* | local disk · Cloudflare R2 |
| 15 | Upload | `worker/src/videosynx_worker/youtube.py` *(new)* | YouTube Data API v3 |
| 16 | Schedule | `worker/src/videosynx_worker/planner.py` *(new)* | `httpx` → our own API |

So the answer to "what draws the characters" is: **a new Python module drawing
flat 2D shapes with Pillow**, one frame at a time, piped to ffmpeg. The
dimensionality is an illusion made of parallax, drawn extrusion, lighting,
shadows, atmosphere and one baked blur per slide (§4) — not geometry.

**Not used, deliberately:** Blender / Unity / Godot (hundreds of megabytes,
output that is not bit-stable across drivers, minutes per video), After Effects
or Lottie (a second rendering toolchain to install and license), any projection
or lighting maths, and sprite packs to begin with (§3 explains why not).

**New engine dependencies: none.** `pillow` and `numpy` are already required
(`engine/pyproject.toml`). The three fonts are files, not packages. The only new
service dependency is the Google client in the worker (§8 P12).

---

## 2. What exists, what is added

| | Exists today | This plan adds |
|---|---|---|
| Queue | Postgres, `FOR UPDATE SKIP LOCKED`, leases, retries, reaper (`db.py:35-141`) | kinds `design`, `discovery` and `publish` |
| Render | Pillow → ffmpeg, audio and video locked frame-for-frame (`sink.py`) | a scene compositor, characters, lighting, fake depth, effects |
| Drawing | code editor, title cards, diagrams whose steps draw themselves in (`render/editor.py`, `render/diagram.py`) | character rigs, 2D props, environments, particles |
| Type | one monospace face, pinned, with a hard failure if missing (`render/__init__.py:22-26`) | a font registry: display, body and mono |
| Look | two styles, `sketch` and `clean` (`render/style.py`) | a third, `cosmic`, built on the Qrius palette |
| Voice | 54 Kokoro voices, ElevenLabs with a local fallback chain | a mouth driven by the real audio |
| Model | five-provider failover chain | a scene-design prompt, an explainer prompt, a topic prompt |
| Quota / billing | `quota.py`, `billing.py`, credit ledger | `publish` and `design` kept off or on the allowance deliberately |
| Auth | Supabase JWT plus a dev header (`auth.py`) | a service token, and Google OAuth |
| Storage | R2 or local (`storage.py`), signed URLs | an `.srt`, preview stills, publish metadata |
| UI | dashboard views, settings, composer | a Discover view, a **design review screen**, channel connection |

Three gaps in what exists, worth naming because this plan depends on them:

- `Settings.length` is accepted (`settings.py:44`) and **read by nobody** — the
  worker never looks at it, so "short / medium / long" is currently decoration.
  P11 wires it.
- The SRT writer exists (`engine/src/videosynx/srt.py`) but only the CLI writes
  a file; the worker produces an MP4 and nothing else. P11 produces it.
- `Card.subtitle` defaults to "a videosynx tutorial" (`timeline.py:347`) — the
  only brand string that exists anywhere in the renderer. P1 makes that a
  setting.

---

## 3. Art direction

Settled. This section is the brief the renderer is built to.

### 3.1 Direction

Modern 2D / 2.5D cinematic educational animation: clean vector illustration,
geometric characters and objects, cinematic lighting over atmospheric
backgrounds, smooth camera movement, detailed scientific and technological
visualisation, and a tightly controlled palette. The feeling to aim at is
intelligent, futuristic, playful and accessible — and deliberately **not** the
look of a generic model-generated cartoon channel.

That last point decides the architecture question this plan had left open.
Characters cannot be redrawn by a model each episode and remain the same
character; they have to be **constructed** from fixed proportions and a fixed
palette, so episode twenty has the same narrator as episode one. The rig in P2
is that construction, and it is why the answer is procedural drawing rather than
generated sprites.

### 3.2 Palette

Dark cosmic background with vibrant accents. These are proposed brand colours,
not an existing identity.

| Role | Name | Hex | Where it is used |
|---|---|---|---|
| Primary | Deep Navy | `#101B35` | `Style.paper` — the backdrop of every scene |
| Primary | Cyan | `#20D6C2` | `Style.accent` — highlights, data lines, active state |
| Primary | Amber | `#FFCE57` | light sources, warmth, the sun, `Style.warn` |
| Primary | Coral | `#F16B83` | danger, conflict, `Style.bad` |
| Support | Off-white | `#F2F4F8` | `Style.ink` — headings and body |
| Support | Purple | `#8176FF` | secondary accent — technology, the abstract |
| Support | Green | `#69C779` | success, growth |
| Support | Slate | `#334565` | `Style.panel`, `Style.panel_line`, `Style.dim` |

The style slots that already exist keep their meaning (`render/style.py`:
`paper`, `ink`, `dim`, `accent`, `panel`, `panel_line`, `ghost`, `warn`, `bad`);
`cosmic` is a third entry in `STYLES` beside `sketch` and `clean`, plus the two
slots lighting needs: `light` (ambient direction) and `glow` (bloom tint).

### 3.3 Typography

All three are SIL Open Font Licence, so they can be committed with the code.

| Face | Role | In the renderer |
|---|---|---|
| **Space Grotesk** | headings, titles, the title card | `Editor.f_title`, `f_ui` |
| **Inter** | subtitles, captions, note text | `Editor.f_caption`, `f_subtitle` |
| **Space Mono** | measurements, labels, technical annotation, code | `Editor.f_code`, `f_term`, diagram labels |

Three consequences, stated plainly:

1. `find_font()` (`render/__init__.py:32`) currently raises when its monospace
   face is missing, deliberately, because a silent proportional fallback
   collapses the code layout. That stays true for the code panel; the new
   registry resolves each role separately and fails the same way per role.
2. The bundled files add about 700 KB to the engine, with `OFL.txt` beside them,
   which the licence requires.
3. The metrics change: Space Grotesk is narrower than the monospace face the
   heading layout was tuned against. `TITLE_SIZE` and the wrap widths
   (`editor.py:284`) need a look, not a rewrite — the code wraps by pixel width,
   so it degrades rather than breaks.

### 3.4 Characters

Rounded geometric bodies, distinctive eyes, minimal facial detail, consistent
proportions, limited accessories, reusable poses and expressions.

The rig delivers that by construction: proportions are numbers in one table, the
palette is six named tones, and poses are shared joint-angle tables. Two episodes
cannot drift apart, because there is nothing to drift.

Every recurring character also gets a **sheet** — front, three-quarter and
profile, plus all six expressions — rendered by `characters.sheet(character)`.
The sheet is the reference an artist would use, and it is also the regression
test: a rig change that moves a shoulder changes the sheet, and the test notices.

### 3.5 The production pipeline: design, approve, then animate

The one thing this plan changes about how a video is made. Nothing is generated
from a single wall-of-text prompt, because that is what produces characters that
change between shots. Instead:

**A. Design.** A `design` job produces a **scene spec** — the eight things the
brief asks for, as data:

| # | The spec has | Field |
|---|---|---|
| 1 | a defined subject | `subject` |
| 2 | a clear background | `background` (environment name + palette variant) |
| 3 | consistent character designs | `cast[]` (id, pose, expression, turn, placement) |
| 4 | a defined colour palette | `palette` (named tones, resolved from `Style`) |
| 5 | foreground / middle / background | `layers[]` with a `z` each |
| 6 | camera framing | `shot`, `framing` |
| 7 | planned movement | `motion[]` (pan, dolly, object moves, reveal order) |
| 8 | lighting and effects | `light` (source, direction, intensity), `effects[]` |

The same job renders **three preview stills** — the first, middle and last
composed frame at 960×540 — into storage. Cheap: about a second each, no audio,
no encode.

**B. Approve.** `POST /designs/{id}/approve` is what turns a design into a video
job. Nothing expensive happens before it. A design can also be edited by hand in
the UI first, and the edited spec is what renders — the JSON is the contract, not
a prompt.

**C. Animate.** The video job renders from the approved spec: movement, parallax,
lighting, glow, particles, camera moves, captions, narration. The model is not
asked again, so the scene it approved is the scene it gets.

This is the same shape as `discovery → make` and `video → publish`, so it needs
no new machinery: one more job kind, one more endpoint, one more screen.

Worked example, the one from the brief — *"What would happen if the Sun
disappeared?"*

- **Design:** Deep Navy field; a large Amber sun at centre; Earth front-left at
  `z 0.85`; stars at `z 0.05`; the narrator at `z 0.55` on a slate ground line;
  light source from the sun, direction centre-to-left.
- **Motion:** camera dollies toward Earth; sun fades over six seconds; background
  darkens in three steps; Earth holds, then goes dark; narration covers the
  eight-minute light-travel delay.
- **Effects:** bloom on the sun, ember particles as it fades, a slow `wipe` to
  black.

### 3.6 The reusable asset library

Consistency across episodes is a library problem, not a prompt problem.

| Category | Lives in | Contents |
|---|---|---|
| Characters | `assets/characters/*.json` + `render/characters.py` | narrator, generic human, scientist — proportions, palette, accessories |
| Environments | `render/environments.py` | space, laboratory, city, nature, map wall |
| Objects | `render/props.py` | planets, vehicles, machines, molecules, flags, charts |
| Effects | `render/effects.py` | glow, smoke, embers, dust, sparks, debris |
| Transitions | `render/transitions.py` | zoom, wipe, fade-to-dark, object metamorphosis |
| Graphics | `render/diagram.py` *(exists)* + `render/graphics.py` | charts, labels, arrows, callouts |

A character defined in `assets/characters/` is referred to by id in a scene spec,
so a channel's narrator is the same narrator in every video. Per-channel cast
overrides live in `channels.cast` (§6), so a channel can have its own recurring
faces without forking the repo.

### 3.7 What this costs

- **Fonts:** three OFL files, ~700 KB, committed with `OFL.txt`.
- **Effects:** particles are per-tick work. A 600-particle field at 1080p is
  affordable; a 20 000-particle one is not. §9 sets the budget and the count.
- **Sound:** the brief lists sound effects and transitions under "after
  rendering". That is the one part this plan defers (P16) and it needs
  **licensed** audio — a music bed and a small SFX set — before it can ship.
  Narration alone is what v1 does.
- **Honesty about the ceiling:** "cinematic lighting" here means gradients, rim
  light, bloom and shadow — drawn, not rendered. "Detailed scientific
  visualisation" means the diagram engine, charts and props. It will look like a
  well-directed motion-graphics explainer, not a 3D render, and that is the
  intended look rather than a limitation of the plan.

---

## 4. The 2D system, and how depth is faked

Nine 2D operations, each cheap on its own, applied together:

1. **Layer parallax.** Five planes; a camera pan moves each by `pan × factor`.
   Depth reads as *motion* before it reads as geometry.
2. **Scale with depth.** Nearer objects are drawn larger, and a dolly scales the
   planes at different rates.
3. **Drawn extrusion.** A prop is its outline, a second outline offset by
   `(dx, dy)` in a darker tone, and a filled polygon joining them. Three flat
   polygons that read as a solid block.
4. **Two-tone facets.** One light direction per style, two hard tone steps — a
   lit facet and a shaded facet. No light maths.
5. **Contact shadows.** A blurred dark ellipse under every object, its height and
   skew set by depth.
6. **Atmospheric fade.** Far planes are blended toward the backdrop colour, more
   of it the further away they are.
7. **Baked depth of field.** Far planes get one or two pixels of blur, computed
   **once per slide**, never per frame.
8. **Light and glow.** A light source in the spec becomes a radial gradient, a
   rim stroke on nearby silhouettes, and a bloom pass: a copy of the frame's
   bright pixels, blurred, composited back with `ImageFilter.SCREEN`. Two passes,
   both cheap, and the single biggest difference between "flat" and "cinematic".
9. **Grain and vignette.** One pre-rendered noise tile per video, screened at low
   opacity, plus a softened edge. This is what stops a dark palette looking like
   a blank rectangle.

Characters turn by the 2D convention rather than by rotation: `front`,
`three_quarter` and `profile` are *one rig* with shoulder width, ear offset, eye
spacing and face width as parameters.

Depth is a single number, `z ∈ [0, 1]`, carried by every layer and every object;
draw order is `z` ascending.

| Plane | z | scale | atmosphere | blur (baked) | parallax | shadow |
|---|---|---|---|---|---|---|
| backdrop / sky / starfield | 0.00 | 0.70 | 55 % | 2.0 px | 0.15 | — |
| back props and environment | 0.25 | 0.82 | 35 % | 1.0 px | 0.35 | 20 % |
| cast and ground line | 0.55 | 1.00 | 10 % | 0 | 1.00 | 35 % |
| front props and particles | 0.85 | 1.18 | 0 % | 0 | 1.45 | 45 % |
| captions / pill / watermark | 1.00 | 1.00 | 0 | 0 | 0 | text shadow |

**Clamped in code, not left to taste:** `|pan| ≤ 2 % of width per second`,
`zoom ∈ [0.92, 1.10]`, `shake ≤ 1.5 px`. Past those numbers the illusion stops
being depth and becomes a slide wobbling. The clamps are asserted in tests.

**Particle budget:** 600 particles per frame at 1080p, hard cap, degraded
automatically when `motion="off"`. Particles are pre-seeded per scene from
`seed_for(scene.id)`, so the same video renders the same sparks.

**Honest ceiling:** flat paper-cut with light. It will not look like a rendered
3D scene, and it is not meant to (§3.7).

---

## 5. Content contract

Two JSON documents, one per stage. Both are data, both are hand-editable, and
both are validated before anything expensive happens.

### 5.1 The scene spec — what a `design` job produces

```json
{
  "subject": "What would happen if the Sun disappeared?",
  "background": {"environment": "space", "variant": "deep", "stars": 240},
  "palette": "cosmic",
  "shot": "wide",
  "framing": {"subject_at": "centre", "headroom": 0.18},
  "layers": [
    {"name": "starfield", "z": 0.05},
    {"name": "sun",       "z": 0.25, "keys": {"size": 520, "tone": "amber"}},
    {"name": "earth",     "z": 0.85, "keys": {"size": 340, "tone": "cyan"}},
    {"name": "narrator",  "z": 0.55, "ground": "slate"}
  ],
  "cast": [
    {"id": "narrator", "pose": "explaining", "expression": "curious", "turn": "three_quarter",
     "at": {"x": 0.18, "y": 0.78}}
  ],
  "light": {"source": "sun", "direction": "centre-to-left", "intensity": 0.8,
            "glow": "amber"},
  "motion": [
    {"camera": "dolly", "toward": "earth", "amount": 1.08, "over": "the scene"},
    {"object": "sun", "fade": 0.0, "over": "6s"},
    {"camera": "pan", "dx": -0.04, "over": "the scene"}
  ],
  "effects": [
    {"name": "bloom", "on": "sun", "strength": 0.7},
    {"name": "embers", "from": "sun", "count": 180},
    {"name": "grain", "opacity": 0.06},
    {"name": "vignette", "opacity": 0.25}
  ],
  "transition_out": {"kind": "wipe", "to": "black", "over": "1.5s"}
}
```

Vocabulary, all closed and all listed in the prompt:

- **environments:** `space`, `laboratory`, `city`, `nature`, `map_wall`,
  `abstract`
- **shots:** `wide`, `two-shot`, `closeup`, `split`, `insert`
- **poses:** `neutral`, `explaining`, `pointing`, `arms_crossed`, `shrugging`,
  `alert`, `defeated`, `celebrating`, `thinking`, `presenting`
- **expressions:** `curious`, `calm`, `confident`, `worried`, `angry`,
  `surprised`
- **turns:** `front`, `three_quarter`, `profile`
- **effects:** `bloom`, `glow`, `embers`, `dust`, `smoke`, `sparks`, `debris`,
  `grain`, `vignette`
- **transitions:** `wipe`, `zoom`, `fade_dark`, `metamorphosis`

### 5.2 The lesson — what a video job renders

The existing lesson JSON, unchanged in shape, with one addition: a section may
carry `scene` (a spec, or a design id), and the top level may carry `publish`.

```json
{
  "title": "What would happen if the Sun disappeared?",
  "cast": [{"id": "narrator", "name": "the Narrator", "palette": "cyan"}],
  "sections": [
    {
      "name": "The eight minutes",
      "design": "0b7f…",
      "explain": {
        "heading": "Eight minutes of light, already in flight",
        "narration": ["One.", "Two.", "Three.", "Four."],
        "steps": [{"pose": {"narrator": "pointing"}, "pill": "8 min 20 s"}]
      },
      "publish": {"description": "…", "tags": ["astronomy"], "made_for_kids": false}
    }
  ]
}
```

Invariants this must not break:

- narration sentences = things revealed + 1 (the rule at `prompts.py:236`);
- `publish` is inert to `parse_lesson`, which reads only title, intro, sections
  and outro (`lesson.py:114-150`) — pinned by a test;
- a slide with no `scene` and no `design` takes today's code path **byte for
  byte**, so every existing tutorial renders exactly as it does now.

---

## 6. Data model

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
  brand           text not null default 'qrius',
  niche           text not null default '',
  -- The recurring faces for this channel: [{id, name, palette, proportions,
  -- accessories}]. Same shape as assets/characters/*.json, so a channel can
  -- have its own narrator without forking the repository.
  cast            jsonb not null default '[]',
  cadence_hours   integer not null default 24,
  length          text not null default 'medium',
  privacy         text not null default 'private',
  publish         text not null default 'review',   -- 'review' | 'auto'
  enabled         boolean not null default false,
  last_planned_at timestamptz,
  created_at      timestamptz not null default now(),
  unique (user_id, platform, channel_id)
);

-- The design stage. A spec plus three preview stills, approved by a person
-- before any narration is paid for and any video is encoded.
create table if not exists designs (
  id          uuid primary key default gen_random_uuid(),
  user_id     text not null,
  channel_id  uuid,
  subject     text not null,
  spec        jsonb not null,
  stills      jsonb not null default '{}',   -- {"first": key, "middle": key, "last": key}
  job_id      uuid,                          -- the design job that produced it
  approved_at timestamptz,
  created_at  timestamptz not null default now()
);

-- One research pass: what was searched, what came back, and what the model
-- thought was worth making.
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

No new job status is needed. A design is a job whose product is a row in
`designs`, exactly as a discovery is a job whose product is a row in
`discoveries` — so `job_status` (`db.py:37-40`) is untouched.

---

## 7. Endpoints and UI

All of these follow the existing shape: `Depends(current_user)`,
`submit_within_quota`, `JobResponse` from the queue (`app.py:1073-1139`).

```
POST   /designs                    queue a design pass           -> { id, status }
GET    /designs                    recent designs and their stills
GET    /designs/{id}               the spec, editable
PUT    /designs/{id}               replace the spec by hand
POST   /designs/{id}/approve       -> { job_id } : render this spec
POST   /discover                   queue a research pass         -> { id, status }
GET    /discover                   recent passes and candidates
POST   /discover/{id}/make         candidate -> design job
POST   /jobs/{id}/publish          queue an upload (idempotent)
GET    /channels
POST   /channels/youtube/start     -> { url } to send the browser to
GET    /channels/youtube/callback  exchange the code, store a sealed token
PUT    /channels/{id}              niche, cast, cadence, privacy, publish, enabled
DELETE /channels/{id}
```

UI additions:

- `Discover` beside the existing entries in `web/app/components/Sidebar.tsx:34-39`,
  a label in `dashboard/page.tsx:31`, a branch in its view switch
  (`page.tsx:386-407`);
- `DiscoverView` in `dashboard/views.tsx` — candidates, then a design;
- **`DesignView`** — the three stills side by side, the spec as editable JSON and
  as a form, and one Approve button. This is the screen the whole art direction
  exists to justify;
- channel connection, `look`/`motion` selects and the cast list in
  `components/SettingsPanel.tsx`;
- eleven calls and their types in `web/app/lib/api.ts`.

---

## 8. Phases

Each phase is one commit's worth of work, has its own tests, and leaves the repo
working. P1–P9 are the look and the video; P10–P16 are sourcing, delivery and the
one thing deliberately deferred.

### P0 — measure before drawing anything *(blocking)*

`render_plan` (`engine/src/videosynx/render/__init__.py:42`) gains counters for
frames, distinct states and per-state milliseconds, logged at the end of a
render. The 1080p baseline is recorded as a constant that
`engine/tests/test_render_budget.py` asserts against from then on.

*Why first:* everything after this changes that number. Without it, the lights
and the particles quietly turn a forty-second render into six minutes, the queue
falls behind, and nobody can say which change did it.

### P1 — brand foundation

- `engine/src/videosynx/render/fonts/` — Space Grotesk, Inter, Space Mono (OFL)
  plus `OFL.txt`.
- `engine/src/videosynx/render/fonts.py` — `FONTS = {"display", "body", "mono"}`,
  `font(role, size)`, and a fallback per role that raises the way `find_font()`
  does today (`render/__init__.py:32`) rather than silently substituting a
  proportional face.
- `find_font()` keeps working: it resolves to Space Mono, then the existing
  candidates, so nothing that already runs breaks.
- `render/brand.py` — `BRAND = Brand(name="Qrius", …)` and the title-card
  subtitle, replacing the hard-coded "a videosynx tutorial" (`timeline.py:347`).
- `render/style.py` — a third `Style`, `cosmic`, from the palette in §3.2, plus
  the two new slots `light` and `glow`. `STYLES` gains it; `sketch` stays the
  default so no existing video changes.

*Tests:* every role resolves to a real face; a missing face raises rather than
falling back to Pillow's default; `cosmic` has every slot `sketch` has;
`BRAND.watermark` reaches a rendered frame.

### P2 — characters

New `engine/src/videosynx/render/characters.py`.

- Rig: head, neck, torso, hips, upper arm ×2, forearm ×2, leg ×2, feet — as
  **numbers in one table**, which is what keeps proportions consistent.
- Ten poses as joint-angle tables, interpolated over `POSE_ARC_TICKS = 6`.
- Idle bob (2 px, 16-tick sine) and blink (3–5 s), seeded from
  `seed_for(char.id + tick)` so two renders are identical.
- Six expressions: brows, eye shape, mouth corner.
- `mouth ∈ [0, 1]`; four mouth shapes interpolated.
- `turn` parameterises shoulder width, ear offset, eye spacing and face width.
- Six-tone palette per character, drawn from the Qrius palette.
- Everything stroked with the existing `Pen` (`render/diagram.py:41`), so
  characters inherit both styles for free.
- `sheet(character)` → PNG: front, three-quarter and profile across the top, six
  expressions below. Committed as a golden image the tests compare against.
- `assets/characters/*.json` — narrator, generic human, scientist, defined in
  data so the library and a channel's `cast` share one shape.
- Pose cache: key `(pose, tick bucket, mouth bucket, expression, turn)`, a
  ~300×450 RGBA tile, LRU 96, hit/miss counters for P0's log.

*Tests:* every pose, expression, turn and character renders; unknown values fall
back; identical seeds give identical bytes; three ticks differ; `mouth` 0 and 1
differ; the cache never exceeds its cap; the sheet matches its golden.

### P3 — props, environments and the asset library

- `engine/src/videosynx/render/props.py` — sixteen objects (`globe`, `map`,
  `flag`, `tank`, `jet`, `ship`, `missile`, `building`, `factory`, `wall`, `city`,
  `oil_rig`, `money`, `chart`, `arrow`, `table`), each outline + lit facet + shade
  facet + extrusion polygon, `extrude ∈ [0, 1]`.
- `engine/src/videosynx/render/environments.py` — `space`, `laboratory`, `city`,
  `nature`, `map_wall`, `abstract`; each a backdrop builder with a palette variant
  and an optional star field seeded by the scene id.
- `engine/src/videosynx/render/graphics.py` — bars, line charts, callouts,
  measurement labels and arrows, set in Space Mono.
- `assets/README.md` — what the library is, how a character or environment is
  added, and the licence rule for anything binary that is ever added.

*Tests:* every prop, environment and graphic renders; unknown names fall back;
`extrude=0` equals the flat outline; a backdrop is deterministic for a given seed.

### P4 — depth, lighting and atmosphere

New `engine/src/videosynx/render/depth.py` and `render/light.py`.

- `Plane`, `PLANES` (the five rows in §4), `Camera(pan, dolly, zoom, shake)`.
- `parallax_offset`, `scale_for`, `atmosphere(image, colour, mix)`,
  `contact_shadow`, `bake_blur`.
- Clamps: `|pan| ≤ 2 %/s`, `zoom ∈ [0.92, 1.10]`, `shake ≤ 1.5 px`.
- `light.py`: radial gradient from a source, rim stroke on near silhouettes,
  bloom via blur + `ImageFilter.SCREEN`, one noise tile per video, vignette.

*Tests:* plane order; parallax monotonic in `z`; atmosphere in range; an
over-fast pan is clamped; the near plane is never blurred; bloom brightens only
near the source; the noise tile is identical across two renders.

### P5 — effects and transitions

- `engine/src/videosynx/render/effects.py` — `bloom`, `glow`, `embers`, `dust`,
  `smoke`, `sparks`, `debris`, `grain`, `vignette`, each a function of
  `(tick, seed, depth)`, bounded by the particle cap in §4.
- `engine/src/videosynx/render/transitions.py` — `wipe`, `zoom`, `fade_dark`,
  `metamorphosis` (cross-dissolve between two prop states), each a function of
  `progress ∈ [0, 1]` returning a composited frame.
- Both are pure functions of time, so they are testable with no video.

*Tests:* a particle never leaves the frame; the count cap is enforced; a
transition at `progress=0` equals the out-frame and at `1` the in-frame.

### P6 — the compositor

New `engine/src/videosynx/render/scene.py`, entered from
`Editor._paint_slide` (`render/editor.py:272`) **only when the slide has a
scene**. Static layers (backdrop, back props, floor) are baked once per slide;
per tick only characters, particles and the camera move.

Compositing is `canvas.paste(tile, (x, y), tile)` onto the existing RGB canvas —
verified `editor.py:96` and `:554` — so there is no full-frame RGBA pass and no
per-frame conversion.

Order: backdrop → back props → floor → cast → front props → particles → light and
bloom → grain → vignette → labels → caption band → watermark.

*Tests:* layer order; the caption band is never occluded; a slide with no scene
is **byte-identical** to `engine/tests/fixtures/legacy_lesson.json`; the budget
holds `keys ≤ 1.8×` and `smooth ≤ 3.0×` with lighting on.

### P7 — the scene spec and the approval gate

This is the phase that makes the pipeline *design, approve, animate* rather than
"prompt and hope".

- `engine/src/videosynx/spec.py` — `SceneSpec`, `Layer`, `Motion`, `Light`,
  `Effect`, and `parse_spec(raw)`, defensive like `parse_diagram`
  (`timeline.py:183`): unknown names fall back, counts are bounded, a spec with
  no subject is refused with a readable message.
- Three **preview stills**: the first, middle and last composed frame of a spec,
  at 960×540, no audio — `render/scene.preview(spec, at)`.
- `worker.py` gains the `design` kind: `SCENE_PROMPT` → `parse_spec` → three
  stills into storage → a row in `designs`.
- `timeline.py`: `Slide` gains `scene: Scene | None`, `tick: int`, `mouth: float`
  — primitives only, so `Slide.__eq__` stays cheap.
- `lesson.py::_load_explain` passes `scene=parse_scene(spec.get("scene"))`.
- `api` — `POST /designs`, `GET /designs`, `GET /designs/{id}`, `PUT /designs/{id}`,
  `POST /designs/{id}/approve`. Approve is what queues the video job, and an
  already-approved design cannot be approved twice.
- `quota.py`: a design job calls a model and is therefore counted; approving is
  free.

*Tests:* a spec round-trips through JSON; unknown vocabulary falls back; three
stills are written per design; approval creates exactly one video job; a second
approval is refused; an edited spec is what renders (assert on the prompt).

### P8 — the content contract

- `prompts.py` — `SCENE_PROMPT` (the eight-point spec, with the closed
  vocabulary of §5.1 listed the way the diagram vocabulary already is at
  `prompts.py:181-220`), `EXPLAINER_PROMPT`, `TOPIC_PROMPT`, and their parsers.
- `explainer_prompt(topic, brief, length, focus)` with `short ≈ 8-12`,
  `medium ≈ 20-30`, `long ≈ 40-60` sentences.
- `api/settings.py`: `look` and `motion` join `Settings` beside `style`, because
  they cost CPU rather than money.
- `render_plan(look=..., motion=...)`, wired from the job's options.

*Tests:* `parse_explainer` rejects a malformed section readably; `parse_spec`
drops unknown layers; the API and the engine still agree on the mode list
(`api/tests/test_api.py:118`); `mode=coding` renders unchanged.

### P9 — the mouth moves with the audio

- `engine/src/videosynx/lipsync.py` — `envelope(samples, fps, ticks_per_s)` →
  `tuple[float, ...]`: RMS per tick, attack and release smoothed, normalised.
- `timeline.plan_explanation` (`:876`) stamps `mouth` per sub-span using the
  split that already draws arrows in (`DRAW_STEPS`, `:44-49, 896-916`).
- The envelope lives on the plan, not inside `VisualState` — floats only cross
  into the renderer.

*Tests:* envelope length equals ticks; silence is zero; a ramp is monotonic; a
loud transient decays before the next tick; a narrated slide's sub-spans carry a
`mouth` that changes across the sentence.

> **Milestone: a topic typed by hand now produces a finished, lit, 2D animated
> explainer with characters that move and mouths that follow the voice.**
> Everything below is sourcing and delivery.

### P10 — reading the web

New `engine/src/videosynx/research.py`, stdlib only.

- `search(query, limit=8)` — the DuckDuckGo HTML endpoint, no key, behind a
  `Search` protocol so a keyed provider can replace it by configuration.
- `fetch(url, timeout=10)` — `urllib` plus an `HTMLParser` that drops `script`,
  `style`, `nav`, `footer` and `aside`, collapses whitespace, caps eight thousand
  characters a page.
- `research(topic, limit=5, budget_s=25)` → `Brief`. **Never raises**: any failure
  returns an empty brief, because a video with a weaker script beats no video —
  the same judgement the worker already makes about a paid voice
  (`worker.py:459`).
- One function owns the network so tests stub it, as `providers._post_json`
  (`providers.py:267`) does.

*Tests:* empty results give an empty brief; a network error gives an empty brief;
markup is stripped; caps hold; a URL that times out returns what was gathered.

### P11 — three new job kinds

- `worker.py:515`: dispatch becomes `speech` | `design` | `discovery` | `publish`
  | video.
- `discovery`: research, then `candidates_prompt`, then
  `Queue.attach_options({"candidates": [...]})` — a new method,
  `options = options || %s::jsonb`, beside `finish`.
- Video path: when `options.design` is present, build the lesson from the
  approved spec; when `options.brief` is present, use `explainer_prompt` instead
  of `build_tutorial`; with neither, today's behaviour exactly.
- Write the `.srt` when `options.publish` is set: `srt.to_srt(plan.captions)` →
  `video_key(tier, id, ".srt")` → storage (`app.py:412` already maps the type).
- Store `publish_meta` on the job.
- `length` and `focus` finally reach the prompt.

*Tests:* a discovery job writes candidates and no artifact; a design job writes a
spec, three stills and no video; a video with a design writes `.mp4` and `.srt`;
`length="long"` reaches the prompt; an unknown kind still renders a video.

### P12 — publishing

- `worker/pyproject.toml`: `google-api-python-client`, `google-auth`,
  `google-auth-oauthlib`, `httpx`, all imported lazily.
- `worker/src/videosynx_worker/publishers.py` — `Publisher` protocol,
  `PublishResult(remote_id, url)`, `LocalPublisher` (a JSON stub in storage, so
  the path is testable with no network).
- `worker/src/videosynx_worker/youtube.py` — `TokenStore` sealing with
  `cryptography.fernet.Fernet` keyed by `VIDEOSYNX_TOKEN_KEY` (`cryptography` is
  already installed, via `pyjwt[crypto]`), then refresh →
  `videos().insert(part="snippet,status", resumable=True)` →
  `captions().insert` with our SRT.
- Privacy from the channel; synthetic-media disclosure set, because the
  narration is synthesised and the platform's policy asks to be told. **Confirm
  the exact field name against the live API rather than trusting this plan.**
- Failures classified like `providers.classify_http` (`providers.py:275`), so
  `busy` retries, `quota` waits, `permanent` fails with a reason.
- `publications.job_id` is the guard that stops a retry uploading twice.

*Tests:* success records a row with a URL; a retry calls the publisher once; a
missing `.srt` still publishes; `busy` stays retryable and `permanent` does not;
`TokenStore` round-trips and leaks nothing readable.

### P13 — channels and the API surface

- `db.py` `SCHEMA`: `channels`, `designs`, `discoveries`, `publications` (§6),
  idempotent like the tables at `db.py:97-140`.
- `api/src/videosynx_api/channels.py` — `start_url(user_id)` with
  `state = hmac(secret, user_id + expiry)` (the same trick as
  `LocalStorage.sign`, `storage.py:76`); `exchange(code)`; save/load/list/delete
  with the token sealed at rest and never returned.
- `auth.py:158` — one branch: `X-Service-Token` matching
  `VIDEOSYNX_SERVICE_TOKEN` resolves to the user id, and is inert when unset.
- `quota.py` — `used_today` and `unsettled` exclude `publish` jobs; `design` and
  `discovery` stay counted because they call a model.
- Routes from §7, each behind `Depends(current_user)`.
- `.env.example`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
  `GOOGLE_REDIRECT_URI`, `VIDEOSYNX_TOKEN_KEY`, `VIDEOSYNX_STATE_SECRET`,
  `VIDEOSYNX_SERVICE_TOKEN`, `DISCOVERY_MAX_PAGES`.

*Tests:* a tampered `state` is refused; sealing round-trips; no response contains
a token; a publish job does not consume the allowance while a design job does;
`install()` twice does not fail.

### P14 — the planner

New `worker/src/videosynx_worker/planner.py`, run as `videosynx-worker --plan`.

Every `PLANNER_INTERVAL_S` (default 300), for each enabled channel past its
`cadence_hours`: `POST /discover` → `POST /discover/{id}/make` (a design) → and
when the channel says `publish='auto'`, approve the design and queue the publish
once the video finishes. With `publish='review'` it stops at the design and waits
for a person, which is the honest default: an auto-pilot nobody has ever looked at
is how a channel gets a bad video.

It is an **HTTP client of the API, never a second database writer**. Quota,
pricing and billing stay in the one place they already are, and the planner
cannot create a job the API would have refused.

*Tests:* a due channel produces one discovery; a channel inside its cadence
produces none; a disabled channel is skipped; `review` never publishes; a failed
cycle still stamps `last_planned_at`.

### P15 — the UI

- `web/app/lib/api.ts` — `Channel`, `Design`, `SceneSpec`, `Candidate`,
  `Discovery` types; `look`/`motion` on `Settings`; eleven calls.
- `Sidebar.tsx:34-39`, `page.tsx:31`, `page.tsx:386-407` — the `Discover` entry.
- `views.tsx` — `DiscoverView` (candidates → make a design) and **`DesignView`**
  (three stills, editable spec, one Approve button).
- `SettingsPanel.tsx` — channels (connect, niche, cast, cadence, privacy,
  `review`/`auto`, enabled), plus `look` and `motion`.
- `ui.spec.ts` — the discover → design → approve path; `pnpm lint`, `typecheck`
  and `build` green (CI gates all three, `.github/workflows/ci.yml`).

### P16 — sound *(deferred)*

The brief lists sound effects and transitions. Voice is done; music and SFX are
not, and cannot be until there is **licensed** audio to ship.

- `audio/` — a music bed and a small SFX set with licence files, kept out of the
  repository until the licence question is settled.
- `timeline.build_audio` mixes bed + SFX + narration with numpy, ducking the bed
  under speech.
- `Settings.music ∈ {off, subtle}`, `off` by default, so nobody ships a channel
  with an unlicensed track by accident.

*Left out of v1 on purpose.* Narration-only is a finished video; a video with a
borrowed track is a strike.

---

## 9. Budgets and guards

1. **Render time.** `motion="keys"` (four ticks a second) at most 1.8× the P0
   baseline; `"smooth"` (eight) at most 3.0×. Enforced by test, logged per job.
   Lighting and bloom are inside that number, not on top of it.
2. **Particles.** 600 per frame at 1080p, hard cap, seeded per scene, dropped to
   zero when `motion="off"`.
3. **Memory.** Pose cache ≤ 96 tiles, ~64 MB. At most six baked static layers per
   slide, dropped when the slide changes. One noise tile per video.
4. **Determinism.** Every random call is seeded from `seed_for(id + tick)`,
   including stars, embers, grain and jitter. Two renders of one lesson produce
   identical frames, and a test says so.
5. **Degradation.** Unknown pose, prop, shot, effect, look or motion falls back to
   a default. A slide with no scene takes the old path. No Google credentials
   means no upload, not a failed render.
6. **Untrusted text.** Whatever the web says is prompt input only. It is never
   executed, never echoed back as code, and is capped far under
   `MAX_SCRIPT_CHARS` (60 000, `app.py:83`).
7. **Nothing expensive before approval.** A design costs a model call and three
   stills. Narration and encode happen only after a person (or an explicit
   `publish='auto'`) approves the spec.

---

## 10. Closing the waitlist

`api/src/videosynx_api/features.py:30-66` already lists this plan's two halves,
as things not built yet:

- **`any-topic`** — "Make a tutorial about how to build a battery, or a rocket,
  or anything else that is not code." Its stated blocker is that the whole
  pipeline writes and runs Python. Closed by P7 and P11.
- **`publish`** — "Send a finished tutorial straight to a channel." Its stated
  blocker is that the MP4 and the SRT already exist, so it is a grant and an
  upload per platform. Closed by P12 and P13.

So both entries come out, and one goes in: **`auto-pilot`** — "It picks
tomorrow's video itself, designs it, and puts it where you told it to."

`api/tests/test_features.py:62-93` uses `any-topic` as its sample id and must
move to `narration-languages` in the same commit. The landing page and the
dashboard render whatever the list holds (`page.tsx:403`, `views.tsx:410`) and
need no change.

---

## 11. Risks

1. **The art ceiling is where you draw it.** The rig and the palette give a clean,
   consistent, lit 2D look. It will not reach a hand-animated studio. If the
   target moves beyond that, the same `draw_character` signature accepts licensed
   sprite sheets — the architecture does not change, only the art source does.
2. **Over-animation** kills the illusion. The clamps in §4 are code and tests, not
   intentions, and `motion="keys"` is the default.
3. **Type metrics.** Swapping to Space Grotesk changes every heading's width. The
   layout wraps by pixels so it degrades, but every slide needs a look at 720p and
   1080p before P1 is called done.
4. **Approval friction.** Design-first is what makes the output trustworthy, and
   it is also one more click. `publish='auto'` exists for channels that have
   earned it; the default stays `review`.
5. **Prompt complexity.** A spec plus a lesson plus a cast is a lot to ask. The
   parsers are defensive in the style of `parse_diagram`, so the worst case is a
   plainer scene rather than a failed job.
6. **`Slide` equality** is compared once per span (`editor.py:241`). `Scene` must
   stay immutable tuples of primitives — no numpy inside — or the render loop gets
   slower for free.
7. **Render time** is the failure nobody notices until the queue is behind. P0
   first, budget as a test, ratio logged per job.
8. **Licensing.** Fonts are OFL and safe. Music and SFX are not, which is why P16
   ships nothing until the licence question is answered. Anything else binary that
   enters `assets/` needs the same check, and `assets/README.md` says so.
9. **YouTube's own limits.** An upload costs 1 600 units of a 10 000 a day quota —
   roughly six a day per channel. `cadence_hours` should be validated at four or
   more.

---

## 12. Out of scope

True 3D. Per-frame compositing of 3D assets. Viseme-accurate lip-sync (it needs
forced alignment). Photoreal assets. Auto-generated thumbnails. TikTok and
Instagram posting. Voice cloning. Music and SFX until licensed (P16).

---

*Written as a plan, not a promise. Every file path and line number above was
checked against the repository as it stands; the art direction is the brief this
plan was revised to.*
