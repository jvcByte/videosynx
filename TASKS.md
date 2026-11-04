# videosynx — the build checklist

Ordered work list for `PLAN.md`. Every line is one unit of work: a file to
create, a symbol to write, a test to add, or a decision to take. Tick as you go.

`PLAN.md` holds the reasoning; this file holds the doing.

```
[ ]  not started      [x]  done or decided
```

The order is deliberate. P0 measures what a render costs before anything changes
it. P1–P9 build the look: brand, characters, props, light, effects, compositor,
the design gate, the prompts, the mouth. P10–P16 are where a topic comes from and
where the finished file goes.

---

## 0. Decisions and preparation

Art direction — **settled** (`PLAN.md` §3)

- [x] Direction: modern 2D/2.5D cinematic educational animation; clean vector,
      geometric characters, cinematic light, controlled palette.
- [x] Characters are **procedural rigs**, not generated sprites — the only way
      episode twenty has the same narrator as episode one.
- [x] Palette: Deep Navy `#101B35` · Cyan `#20D6C2` · Amber `#FFCE57` · Coral
      `#F16B83` · Off-white `#F2F4F8` · Purple `#8176FF` · Green `#69C779` ·
      Slate `#334565`.
- [x] Type: Space Grotesk (display) · Inter (body) · Space Mono (labels, code).
      All OFL, so they can be committed.
- [x] Brand: **Qrius**.
- [x] Pipeline: design → approve → animate, never prompt-to-video.

Repository

- [ ] `git pull` on `main` — the local clone is one commit behind.
- [ ] Branch: `feat/qrius-scenes` off `main`.
- [ ] Render one existing tutorial at 1080p and keep the `.mp4` as the "before"
      reference to eyeball later.
- [ ] Save its lesson JSON as `engine/tests/fixtures/legacy_lesson.json` — the
      byte-identical test in P6 compares against it.

---

## P0 — measure before drawing anything

- [ ] `engine/src/videosynx/render/__init__.py`: count frames, distinct states and
      milliseconds per state in `render_plan`.
- [ ] Log `states`, `frames`, `seconds`, `frames/s` and the slowest state at the
      end of a render.
- [ ] Measure the legacy fixture at 1280×720 and 1920×1080; record both as
      constants in `engine/tests/test_render_budget.py`.
- [ ] Budget helper `ratio(seconds, baseline)` in that test module.
- [ ] `pytest engine` green.

**Done when:** a render prints its own cost and there is a number to compare every
later phase against.

---

## P1 — brand foundation

- [ ] `engine/src/videosynx/render/fonts/` — SpaceGrotesk, Inter, SpaceMono
      (the weights the layouts need) plus `OFL.txt`.
- [ ] `engine/src/videosynx/render/fonts.py` — `ROLES = {"display", "body",
      "mono"}`, `font(role, size)`, cached by `(role, size)`.
- [ ] Each role fails loudly when its face is missing, the way `find_font()` does
      (`render/__init__.py:32`) — never silently substitute a proportional face.
- [ ] `find_font()` resolves to Space Mono first, then the existing candidates, so
      nothing that runs today breaks.
- [ ] `render/brand.py` — `BRAND = Brand(name="Qrius", tagline, watermark,
      card_subtitle)`.
- [ ] The hard-coded "a videosynx tutorial" (`timeline.py:347`) reads from
      `BRAND`.
- [ ] `render/style.py` — `COSMIC` from the palette, plus the two new slots
      `light` and `glow`; `STYLES["cosmic"]`; `sketch` stays the default.
- [ ] Check every heading size against Space Grotesk metrics at 720p and 1080p
      (`TITLE_SIZE`, the wrap widths at `editor.py:284`); adjust constants only,
      no layout rewrites.
- [ ] `engine/tests/test_fonts.py`: every role resolves; a missing face raises;
      `cosmic` has every slot `sketch` has.
- [ ] `engine/tests/test_brand.py`: `BRAND.watermark` reaches a rendered frame;
      the title card carries the brand subtitle.

**Done when:** a video renders in the Qrius palette, in the right type, with the
brand on the card and in the corner — and no existing video changed.

---

## P2 — characters

Implementation — `engine/src/videosynx/render/characters.py`

- [ ] `PROPORTIONS`: head, neck, torso, hips, upper arm ×2, forearm ×2, leg ×2,
      feet — numbers in one table.
- [ ] `POSES`: ten joint-angle tables (`neutral`, `explaining`, `pointing`,
      `arms_crossed`, `shrugging`, `alert`, `defeated`, `celebrating`, `thinking`,
      `presenting`).
- [ ] `EXPRESSIONS`: six (`curious`, `calm`, `confident`, `worried`, `angry`,
      `surprised`) as brow, eye-shape and mouth-corner offsets.
- [ ] `TURNS`: `front`, `three_quarter`, `profile` — shoulder width, ear offset,
      eye spacing, face width.
- [ ] `CHARACTERS`: three presets (`narrator`, `human`, `scientist`), each a
      six-tone palette from the brand palette plus limited accessories.
- [ ] `draw_character(draw, char, *, pose, expression, mouth, tick, turn, scale,
      style)` — all strokes through `Pen` (`render/diagram.py:41`).
- [ ] `POSE_ARC_TICKS = 6` pose interpolation, so a change animates.
- [ ] Idle bob: 2 px on a 16-tick sine, seeded `seed_for(char.id + tick)`.
- [ ] Blink every 3–5 s, same seeding.
- [ ] Four mouth shapes interpolated by `mouth ∈ [0, 1]`.
- [ ] Contact-shadow hook (the ellipse `scene.py` will paint).
- [ ] `sheet(character)` → PNG: three turns across, six expressions down.
- [ ] `assets/characters/{narrator,human,scientist}.json` — the same shape as a
      channel's `cast` entries.
- [ ] `PoseCache`: LRU 96 tiles, key `(pose, tick bucket, mouth bucket,
      expression, turn)`, hit/miss counters exposed for P0's log.
- [ ] A CLI flag on `videosynx` to write a sheet to a path, for reviewing a design.

Tests — `engine/tests/test_characters.py`

- [ ] Every pose, expression, turn and preset renders.
- [ ] Unknown pose, expression or turn renders the default.
- [ ] Identical arguments give identical bytes.
- [ ] Three different ticks give three different tiles.
- [ ] `mouth=0` and `mouth=1` differ.
- [ ] A cache hit returns the same tile and does not mutate it.
- [ ] The cache never exceeds its cap.
- [ ] `sheet()` matches its committed golden image.

**Done when:** any preset can be drawn in any pose, at any turn, with a moving
mouth, identically on every render, and its sheet proves it.

---

## P3 — props, environments and the asset library

- [ ] `engine/src/videosynx/render/props.py` — `PROPS` with sixteen entries:
      `globe`, `map`, `flag`, `tank`, `jet`, `ship`, `missile`, `building`,
      `factory`, `wall`, `city`, `oil_rig`, `money`, `chart`, `arrow`, `table`.
- [ ] Each prop: outline + lit facet + shade facet + extrusion polygon,
      `extrude ∈ [0, 1]`, tones from `Style`.
- [ ] `papercut` treatment: 1 px light edge and a stronger offset shadow.
- [ ] Unknown prop draws a box; `extrude=0` equals the flat outline.
- [ ] Props return their bounds, so `scene.py` can place the contact shadow.
- [ ] `engine/src/videosynx/render/environments.py` — `space`, `laboratory`,
      `city`, `nature`, `map_wall`, `abstract`, each with palette variants.
- [ ] Star and dust fields seeded from the scene id, not from `random`.
- [ ] `engine/src/videosynx/render/graphics.py` — bars, line charts, callouts,
      measurement labels, arrows, set in Space Mono.
- [ ] `assets/README.md` — what the library is, how to add a character or an
      environment, and the licence rule for anything binary.
- [ ] `engine/tests/test_props.py`, `test_environments.py`, `test_graphics.py`:
      every name renders; unknown falls back; `extrude=0` is flat; a backdrop is
      deterministic for a given seed.

**Done when:** a scene can be dressed from the library alone, with no bespoke
drawing per video.

---

## P4 — depth, lighting and atmosphere

- [ ] `engine/src/videosynx/render/depth.py` — `Plane`, `PLANES` (the five rows
      in `PLAN.md` §4), `Camera(pan_x, pan_y, dolly, zoom, shake)`.
- [ ] `parallax_offset(plane, camera)`, `scale_for(plane, camera)`.
- [ ] `atmosphere(image, colour, mix)`.
- [ ] `contact_shadow(width, depth)`.
- [ ] `bake_blur(image, plane)` — once per slide, never per frame.
- [ ] Clamps: `|pan| ≤ 2 % width/s`, `zoom ∈ [0.92, 1.10]`, `shake ≤ 1.5 px`.
- [ ] `engine/src/videosynx/render/light.py` — radial gradient from a source,
      rim stroke on near silhouettes, bloom (blur + `ImageFilter.SCREEN`), one
      noise tile per video, vignette.
- [ ] `engine/tests/test_depth.py`, `test_light.py`: plane order; parallax
      monotonic in `z`; atmosphere in range; an over-fast pan is clamped; the near
      plane is never blurred; bloom brightens only near its source; the noise tile
      is identical across two renders.

**Done when:** the same scene at two camera positions reads as a camera move, and
a dark palette looks like a lit space rather than a blue rectangle.

---

## P5 — effects and transitions

- [ ] `engine/src/videosynx/render/effects.py` — `bloom`, `glow`, `embers`,
      `dust`, `smoke`, `sparks`, `debris`, `grain`, `vignette`, each a pure
      function of `(tick, seed, depth)`.
- [ ] Particle cap 600 per frame at 1080p, enforced inside the effect, not by the
      caller.
- [ ] `engine/src/videosynx/render/transitions.py` — `wipe`, `zoom`,
      `fade_dark`, `metamorphosis` (cross-dissolve between two prop states), each
      a pure function of `progress ∈ [0, 1]`.
- [ ] `engine/tests/test_effects.py`, `test_transitions.py`: particles stay in
      frame; the cap holds; `progress=0` equals the out-frame and `1` the
      in-frame; a transition does not flash mid-point.

**Done when:** the Sun can fade while embers rise and the shots can change without
a cut looking like a mistake.

---

## P6 — the compositor

- [ ] `engine/src/videosynx/render/scene.py` — `ScenePainter(width, height, fonts,
      style, look)`.
- [ ] `bake(slide)` once per slide: backdrop, back props, skewed floor
      (`Image.transform(AFFINE)`), front props.
- [ ] `paint(canvas, slide)`: baked layers, then character tiles at `slide.tick`,
      then particles, light and bloom, grain, vignette, labels, caption band.
- [ ] Paste with alpha onto the **RGB** canvas — no full-frame RGBA pass, no
      per-frame conversion (`editor.py:96`, `:554`).
- [ ] `look ∈ {whiteboard, flat2d, papercut, cosmic}` resolved like `style_for`
      (`style.py:72`); unknown falls back to `cosmic`.
- [ ] `motion ∈ {off, keys, smooth}` → 0 / 4 / 8 ticks a second.
- [ ] `render/editor.py:272`: `_paint_slide` branches to `self._scene.paint(...)`
      **only when the slide has a scene**; the existing body stays as the else.
- [ ] `Editor.__init__` builds the painter lazily; mode change invalidates it.
- [ ] `render_plan(..., look=..., motion=...)` passes both through.
- [ ] `engine/tests/test_scene.py`: layer order puts front props over the cast;
      the caption band is never occluded; a slide with no scene is
      **byte-identical** to `legacy_lesson.json`; unknown look and motion fall
      back.
- [ ] `test_render_budget.py`: `keys ≤ 1.8×`, `smooth ≤ 3.0×`, with lighting on.

**Done when:** the legacy fixture is untouched and a scene renders with a moving
camera, layered props, an animating character and bloom — inside the budget.

---

## P7 — the scene spec and the approval gate

Engine — `engine/src/videosynx/spec.py`

- [ ] `SceneSpec`, `Layer`, `CastMember`, `Light`, `Motion`, `Effect`,
      `Transition` dataclasses — primitives and tuples only.
- [ ] `parse_spec(raw)` beside the other parsers, defensive in the style of
      `parse_diagram` (`timeline.py:183`): unknown environment, shot, pose,
      expression, turn, effect or transition falls back; counts bounded with
      `_bounded_int`; a spec with no subject raises a readable `ValueError`.
- [ ] `spec_to_prompt(spec)` — the spec back to prose, so a hand-edited spec
      renders through the same path a generated one does.
- [ ] `render/scene.py::preview(spec, at)` — one composed frame at 960×540,
      no audio, used for the three stills.

Worker — `worker/src/videosynx_worker/worker.py`

- [ ] `design` kind: `SCENE_PROMPT` → `parse_spec` → three stills
      (first/middle/last) → `Queue.write_design(...)` → beat `done`, no artifact.
- [ ] Stills land under `designs/<design_id>/<slot>.png` via `storage.put`.
- [ ] A spec that fails to parse fails the job with the field named, not a stack
      trace.

Timeline and lesson

- [ ] `Slide` gains `scene: SceneSpec | None`, `tick: int = 0`,
      `mouth: float = 0.0` — none of them arrays.
- [ ] `fold_steps` (`:820`) stamps `tick` and `mouth` onto each folded slide.
- [ ] `lesson.py::_load_explain` passes `scene=parse_scene(spec.get("scene"))`.
- [ ] A section may name a design id instead of an inline spec; the worker
      resolves it from `designs` before parsing.

API — `api/src/videosynx_api/app.py`

- [ ] `POST /designs` → a `design` job, quota-checked.
- [ ] `GET /designs`, `GET /designs/{id}` → spec plus signed still URLs
      (signed the way artifacts already are, `app.py:445`).
- [ ] `PUT /designs/{id}` → replace the spec by hand, validated with
      `parse_spec` so a broken edit is refused at the edge.
- [ ] `POST /designs/{id}/approve` → queues exactly one video job carrying the
      spec; refuses a second approval; refuses an unparsed spec.
- [ ] `quota.py`: `design` counted (it calls a model), approval free.

Tests

- [ ] `engine/tests/test_spec.py`: round-trip; unknown vocabulary falls back; no
      subject raises; bounds hold.
- [ ] `worker/tests/test_design.py`: three stills written; no artifact; a broken
      spec fails with a field name.
- [ ] `api/tests/test_designs.py`: approve creates one job; a second approval is
      409; an edited spec is what the job carries; stills come back signed.

**Done when:** a scene can be designed, looked at as three stills, edited, and
only then rendered — and nothing expensive happens before Approve.

---

## P8 — the content contract

Prompts — `engine/src/videosynx/prompts.py`

- [ ] `SCENE_PROMPT`: the eight-point spec, with the closed vocabulary of
      `PLAN.md` §5.1 written out the way the diagram vocabulary already is
      (`prompts.py:181-220`), plus one worked example.
- [ ] `EXPLAINER_PROMPT`: hook → stakes → what is actually true → the comparison →
      what would decide it → payoff. No code, no backticks, only figures present
      in the brief, contested claims named as contested.
- [ ] `TOPIC_PROMPT` → five ranked `{title, angle, why_now, keywords}`.
- [ ] `explainer_prompt(topic, brief, length, focus)` with `short ≈ 8-12`,
      `medium ≈ 20-30`, `long ≈ 40-60` sentences.
- [ ] `parse_explainer(text)` → `(Lesson, publish_meta)` via `parse_json_object`
      (`prompts.py:328`) then `parse_lesson`.
- [ ] `parse_topics(text)`.

Settings

- [ ] `api/src/videosynx_api/settings.py`: `look` and `motion` join `Settings`
      beside `style` and ride through `as_options()`.
- [ ] `worker.py`: pass `options["look"]` and `options["motion"]` into
      `render_plan`.

Tests

- [ ] `engine/tests/test_prose.py`: `parse_explainer` rejects a malformed section
      readably; `publish` is ignored by `parse_lesson` (the invariant from
      `lesson.py:114-150`).
- [ ] `engine/tests/test_prompts.py`: the scene example in the prompt is
      well-formed and parses back through `parse_spec` — a prompt whose example
      does not parse teaches the model the wrong shape.
- [ ] `api/tests/test_api.py:118` still passes — API and engine agree on modes.
- [ ] `engine/tests/test_pipeline.py`: `mode=coding` with no scene is unchanged.

**Done when:** a hand-written spec and lesson render, and every prompt's own
example parses through the parser it is paired with.

---

## P9 — the mouth moves with the audio

- [ ] `engine/src/videosynx/lipsync.py`: `envelope(samples, fps, ticks_per_s)` →
      `tuple[float, ...]` — RMS per tick, attack and release smoothed, normalised.
- [ ] `timeline.plan_explanation` (`:876`) stamps `mouth` on each sub-span using
      the split that already draws arrows in (`DRAW_STEPS`, `:44-49, 896-916`).
- [ ] The envelope lives on the plan; only floats cross into the renderer.
- [ ] `engine/tests/test_lipsync.py`: length equals ticks; silence → 0; a ramp is
      monotonic; a transient decays before the next tick.
- [ ] `engine/tests/test_timeline.py`: a narrated slide's sub-spans carry a
      `mouth` that changes across the sentence.

**Done when:** a narrated scene has a character whose mouth follows the voice,
with no phoneme aligner and no extra dependency.

> **Milestone: a topic typed by hand now produces a finished Qrius explainer —
> lit, animated, with characters that move and mouths that follow the voice.**

---

## P10 — reading the web

- [ ] `engine/src/videosynx/research.py`: `Source`, `Brief`.
- [ ] `search(query, limit=8)` — DuckDuckGo HTML, no key, behind a `Search`
      protocol so a keyed provider can replace it.
- [ ] `fetch(url, timeout=10)` — `urllib` + `HTMLParser` dropping `script`,
      `style`, `nav`, `footer`, `aside`; whitespace collapsed; 8 000 chars a page.
- [ ] `research(topic, limit=5, budget_s=25)` → `Brief`, ≤ 5 pages, ≤ 120 000
      chars. **Never raises**; a failure returns an empty brief.
- [ ] One function owns the network, stubbable the way
      `providers._post_json` (`providers.py:267`) is.
- [ ] `DISCOVERY_MAX_PAGES` honoured from the environment.
- [ ] `engine/tests/test_research.py`: empty results, network error, stripped
      markup, caps honoured, a timeout returns what was gathered.

**Done when:** a topic becomes a brief with sources, offline-testable, and nothing
downstream has to catch an exception from it.

---

## P11 — three new job kinds

- [ ] `Queue.attach_options(job_id, patch)` — `options = options || %s::jsonb`,
      beside `finish`.
- [ ] `Queue.write_design(...)` — insert a row in `designs`, return its id.
- [ ] `worker.py:515`: dispatch becomes `speech` | `design` | `discovery` |
      `publish` | video.
- [ ] `discovery` branch: research → `candidates_prompt` → `parse_topics` →
      `attach_options`, no artifact.
- [ ] Video branch: `options.design` → render the approved spec;
      `options.brief` → `explainer_prompt`; neither → today's `build_tutorial`.
- [ ] `.srt` written when `options.publish` is set: `srt.to_srt(plan.captions)` →
      `video_key(tier, id, ".srt")`.
- [ ] `publish_meta` stored on the job.
- [ ] `length` and `focus` reach the prompt.
- [ ] `worker/tests/test_worker.py`: discovery writes candidates and no artifact;
      design writes stills and no video; a video with a design writes `.mp4` and
      `.srt`; `length="long"` appears in the prompt; an unknown kind still renders
      a video.

**Done when:** research, design and render all travel the queue, and one job
carries a video with its captions.

---

## P12 — publishing

- [ ] `worker/pyproject.toml`: `google-api-python-client`, `google-auth`,
      `google-auth-oauthlib`, `httpx`, all imported lazily.
- [ ] `publishers.py`: `Publisher` protocol, `PublishResult(remote_id, url)`,
      `LocalPublisher`.
- [ ] `youtube.py`: `TokenStore.seal/open` via `cryptography.fernet.Fernet`,
      keyed by `VIDEOSYNX_TOKEN_KEY`.
- [ ] `YouTubePublisher`: refresh → `videos().insert(part="snippet,status",
      resumable=True)` → `captions().insert` with the `.srt`.
- [ ] Privacy from the channel; synthetic-media disclosure set — **confirm the
      field name against the live API**.
- [ ] Failure classification mirroring `providers.classify_http`
      (`providers.py:275`).
- [ ] `publications` insert keyed by `job_id`.
- [ ] Publish branch in `worker.py`, writing `youtube_url` into options.
- [ ] `worker/tests/test_publish.py`: success records a row; retry uploads once;
      missing SRT still publishes; `busy` retries and `permanent` does not;
      `TokenStore` round-trips without leaking.

**Done when:** a finished video reaches a channel with captions, exactly once,
testable with no network.

---

## P13 — channels and the API surface

Schema — `db.py` `SCHEMA`

- [ ] `channels`, `designs`, `discoveries`, `publications` exactly as `PLAN.md`
      §6 writes them, idempotent like the tables at `db.py:97-140`.
- [ ] `worker/tests/test_queue.py`: `install()` twice does not fail.

Channels — `api/src/videosynx_api/channels.py`

- [ ] `start_url(user_id)` with `state = hmac(secret, user_id + expiry)`.
- [ ] `exchange(code)` → refresh token, channel id, channel title.
- [ ] save/load/list/delete against `channels`, token sealed at rest.
- [ ] No response object ever contains a token, sealed or not.

Auth and quota

- [ ] `auth.py:158`: `X-Service-Token` matching `VIDEOSYNX_SERVICE_TOKEN`
      resolves the user id; inert when unset.
- [ ] `quota.py`: `used_today` and `unsettled` exclude `kind='publish'`;
      `design` and `discovery` stay counted.

Routes — `app.py`

- [ ] `POST /discover`, `GET /discover`, `POST /discover/{id}/make`.
- [ ] `POST /jobs/{id}/publish` (idempotent).
- [ ] `GET /channels`, `POST /channels/youtube/start`,
      `GET /channels/youtube/callback`, `PUT /channels/{id}`,
      `DELETE /channels/{id}`.
- [ ] Every route behind `Depends(current_user)`; every route that creates a job
      behind `submit_within_quota`.

Config and copy

- [ ] `.env.example`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
      `GOOGLE_REDIRECT_URI`, `VIDEOSYNX_TOKEN_KEY`, `VIDEOSYNX_STATE_SECRET`,
      `VIDEOSYNX_SERVICE_TOKEN`, `DISCOVERY_MAX_PAGES`.
- [ ] `features.py`: remove `any-topic` and `publish`; add `auto-pilot`.
- [ ] `api/tests/test_features.py:62-93`: sample id moves from `any-topic` to
      `narration-languages`.

Tests

- [ ] `api/tests/test_channels.py`: tampered `state` → 401; sealing round-trips;
      no token in any response.
- [ ] `api/tests/test_discovery.py`: submit, list, make; each needs auth.
- [ ] `api/tests/test_api.py`: `publish` does not consume the allowance; `design`
      and `discovery` do.

**Done when:** a channel is connected once, and any client holding a service token
can move a video from design to upload.

---

## P14 — the planner

- [ ] `worker/src/videosynx_worker/planner.py`: `Planner(api_url, service_token,
      interval_s)`, looping every `PLANNER_INTERVAL_S` (default 300).
- [ ] Select enabled channels past their `cadence_hours`.
- [ ] `POST /discover` → `POST /discover/{id}/make` (a design).
- [ ] `publish='auto'` → approve and queue publish when the video finishes;
      `review` → stop at the design and wait.
- [ ] Stamp `last_planned_at` whether or not the cycle produced anything.
- [ ] `__main__.py`: `--plan` flag sharing the existing logging and `dotenv` load.
- [ ] It is an HTTP client of the API — **no direct inserts**.
- [ ] `worker/tests/test_planner.py` (fake transport): one discovery per due
      channel; none inside cadence; disabled skipped; `review` never publishes; a
      failed cycle still stamps.

**Done when:** leaving `videosynx-worker --plan` running designs tomorrow's video
without anyone touching the app.

---

## P15 — the UI

- [ ] `web/app/lib/api.ts`: `Channel`, `Design`, `SceneSpec`, `Candidate`,
      `Discovery`; `look`/`motion` on `Settings`; eleven calls.
- [ ] `Sidebar.tsx:34-39`: `{ label: "Discover", view: "discover" }`.
- [ ] `page.tsx:31` label and `page.tsx:386-407` branch.
- [ ] `views.tsx`: `DiscoverView` — candidate cards with title, angle, why-now.
- [ ] `views.tsx`: **`DesignView`** — three stills, the spec as a form and as
      JSON, one Approve button. The screen the art direction exists to justify.
- [ ] `views.tsx`: `youtube_url` shown on a published video.
- [ ] `SettingsPanel.tsx`: channels (connect, niche, cast, cadence, privacy,
      `review`/`auto`, enabled).
- [ ] `SettingsPanel.tsx`: `look` and `motion` selects beside `style`.
- [ ] `ui.spec.ts`: discover → design → approve renders and submits; the channel
      block lists a channel.
- [ ] `pnpm lint`, `pnpm typecheck`, `pnpm build` green.

**Done when:** the whole loop — topic, design, approve, video, publish — is usable
without curl.

---

## P16 — sound *(deferred)*

- [ ] Answer the licence question: which music bed, which SFX, under what terms,
      and may they ship in the repository.
- [ ] `audio/` with licence files, wired into the build only once that is settled.
- [ ] `timeline.build_audio` mixing bed + SFX + narration, ducking under speech.
- [ ] `Settings.music ∈ {off, subtle}`, `off` by default.
- [ ] `engine/tests/test_audio_mix.py`: the mix is the same length as the video;
      `music="off"` is bit-identical to today's audio.

**Done when:** a licensed bed plays under the narration and can be turned off.

---

## Release checks

- [ ] End-to-end with `LocalPublisher`: discover → design → approve → video
      (`.mp4` + `.srt`) → publish → a `publications` row.
- [ ] End-to-end against a real channel: upload as `private`, watch it, delete it.
- [ ] Allowance: three videos, then a publish still allowed, then a design
      refused.
- [ ] Render budget re-measured at 1080p with lighting and particles on; the
      constant updated if it moved.
- [ ] `pytest` green in all four packages; `pnpm` checks green.
- [ ] `PLAN.md` status line moved from *proposed* to *in progress*, and completed
      phases ticked here.
- [ ] `README.md` gains a paragraph and a link to `PLAN.md`; `usage.md` gains
      `--plan`, the design flow and the new endpoints.
- [ ] Branch pushed; PR opened against `main`.

---

## Definition of done, whole feature

The account is connected once. `videosynx-worker --plan` is running. Nobody
touches anything for a day. A video appears on the paired channel: 2D, lit, with
characters whose mouths move, with captions and a design three stills were
approved from — drawn from something that was actually in the news that week —
and the quota, the retries and the idempotency all held while it happened.

---

## Not in this checklist

True 3D, per-frame 3D compositing, viseme lip-sync, photoreal assets, auto
thumbnails, TikTok and Instagram, voice cloning, and any audio that is not
licensed (`PLAN.md` §12).
