# videosynx — the build checklist

Ordered work list for `PLAN.md`. Every line is one unit of work: a file to
create, a symbol to write, a test to add, or a decision to take. Tick as you go.

Read `PLAN.md` for the reasoning; this file is only the doing.

```
[ ]  not started      [x]  done
```

Phase order is the plan's order and it is deliberate. P0 comes first because
everything after it changes how long a render takes, and P1–P6 give a finished
2D explainer before any of the web, quota or YouTube work begins.

---

## 0. Before any code

- [ ] Decide the character art source — procedural rigs **or** licensed sprite
      packs (`PLAN.md` §12). This decides what P1 draws, so it cannot wait.
- [ ] `git pull` on `main`: the local clone is one commit behind.
- [ ] Branch: `feat/scene-renderer` off `main`.
- [ ] Make one existing tutorial at 1080p and keep the `.mp4` as the "before"
      reference for eyeballing later.
- [ ] Save that render's lesson JSON as
      `engine/tests/fixtures/legacy_lesson.json` — the byte-identical test in P4
      compares against it.

---

## P0 — measure before drawing anything

Implementation

- [ ] `engine/src/videosynx/render/__init__.py`: count frames written and
      distinct `VisualState`s in `render_plan`.
- [ ] Log `states`, `frames`, `seconds` and `frames/s` at the end of a render.
- [ ] Render the legacy fixture at 1280×720 and 1920×1080 and write both numbers
      into `engine/tests/test_render_budget.py` as `BASELINE_720` /
      `BASELINE_1080`.

Tests

- [ ] `engine/tests/test_render_budget.py`: helper `ratio(seconds, baseline)`
      plus one test asserting the current fixture renders within 1.0× of itself
      (a smoke test that the instrumentation reports something).
- [ ] `pytest engine` green.

**Done when:** a render prints its own cost and there is a number to compare
every later phase against.

---

## P1 — characters

Implementation — `engine/src/videosynx/render/characters.py`

- [ ] `RIG`: joint table — head, neck, torso, hips, upper arm ×2, forearm ×2,
      leg ×2, feet.
- [ ] `POSES`: ten named joint-angle tables (`neutral`, `explaining`,
      `pointing`, `arms_crossed`, `shrugging`, `alert`, `defeated`,
      `celebrating`, `thinking`, `presenting`).
- [ ] `EXPRESSIONS`: six (`calm`, `confident`, `worried`, `angry`, `surprised`,
      `smug`) as brow and mouth-corner offsets.
- [ ] `TURNS`: `front`, `three_quarter`, `profile` — shoulder width, ear offset,
      eye spacing, face width.
- [ ] `draw_character(draw, char, *, pose, expression, mouth, tick, turn, scale,
      style)` drawn entirely with `Pen` (`render/diagram.py:41`).
- [ ] Pose interpolation: `POSE_ARC_TICKS = 6`, angles interpolated, so a pose
      change animates.
- [ ] Idle bob: 2 px on a 16-tick sine, seeded `seed_for(char.id + tick)`.
- [ ] Blink: every 3–5 s, seeded the same way.
- [ ] Mouth: openness from `mouth ∈ [0, 1]`; closed, half, open, wide.
- [ ] Contact shadow hook (ellipse under the figure; painted in P4).
- [ ] `PoseCache`: key `(pose, tick bucket, mouth bucket, expression, turn)`,
      value a ~300×450 RGBA tile, LRU cap 96, `hit`/`miss` counters for P0's log.
- [ ] Add `circle` and `polygon` helpers beside `_shape`
      (`render/diagram.py:99`) so characters and props share one drawing path.
- [ ] Unknown pose, expression or turn falls back to the default — never raises.

Tests — `engine/tests/test_characters.py`

- [ ] Every name in `POSES`, `EXPRESSIONS`, `TURNS` renders without raising.
- [ ] An unknown pose/expression/turn renders the default.
- [ ] Two renders with identical arguments produce identical bytes.
- [ ] Three different ticks produce three different tiles.
- [ ] `mouth=0` and `mouth=1` differ.
- [ ] A cache hit returns the same tile object and does not mutate it.
- [ ] Cache never exceeds its cap.

**Done when:** a character can be drawn in any pose, at any turn, with a moving
mouth, deterministically, and the tile cache stays inside its budget.

---

## P2 — props with drawn extrusion

Implementation — `engine/src/videosynx/render/props.py`

- [ ] `Prop` dataclass: `name`, `at`, `z`, `extrude`, `state`.
- [ ] Sixteen drawings: `globe`, `map`, `flag`, `tank`, `jet`, `ship`,
      `missile`, `building`, `factory`, `wall`, `city`, `oil_rig`, `money`,
      `chart`, `arrow`, `table`.
- [ ] Each prop = outline + lit facet + shade facet, tones taken from `Style`.
- [ ] `extrude` (0–1): offset silhouette in a darker tone plus a filled side
      polygon joining them.
- [ ] `papercut` style: 1 px light paper edge and a stronger offset shadow.
- [ ] Unknown prop name draws a box; `extrude=0` equals the flat outline.
- [ ] Return the drawing bounds so `scene.py` can place the contact shadow.

Tests — `engine/tests/test_props.py`

- [ ] Every prop name renders.
- [ ] Unknown name renders a box.
- [ ] `extrude=0` output equals the outline-only path.
- [ ] Nothing draws outside the returned bounds.

**Done when:** every prop in the vocabulary can be placed at any depth and reads
as a solid object while being three flat polygons.

---

## P3 — depth and camera

Implementation — `engine/src/videosynx/render/depth.py`

- [ ] `Plane` dataclass: `z`, `scale`, `atmosphere`, `blur`, `parallax`,
      `shadow`.
- [ ] `PLANES`: the five rows from `PLAN.md` §3 as constants.
- [ ] `Camera(pan_x, pan_y, dolly, zoom, shake)` with `at(seconds)`.
- [ ] Clamps: `|pan| ≤ 2 % width/sec`, `zoom ∈ [0.92, 1.10]`, `shake ≤ 1.5 px`.
- [ ] `parallax_offset(plane, camera)`, `scale_for(plane, camera)`.
- [ ] `atmosphere(image, colour, mix)`.
- [ ] `contact_shadow(width, depth)` → blurred ellipse.
- [ ] `bake_blur(image, plane)` — used once per slide, never per frame.

Tests — `engine/tests/test_depth.py`

- [ ] Plane order is ascending in `z`.
- [ ] Parallax is monotonic across planes.
- [ ] Atmosphere mix stays within 0–1 and matches the table.
- [ ] An over-fast pan is clamped, not honoured.
- [ ] The near plane is never blurred.

**Done when:** the same scene painted at two camera positions reads as a camera
move rather than a slide shaking.

---

## P4 — the compositor

Implementation — `engine/src/videosynx/render/scene.py`

- [ ] `ScenePainter(width, height, fonts, style, look)`.
- [ ] `bake(slide)` once per slide: backdrop, back props, skewed floor (a flat
      grid pushed through `Image.transform(AFFINE)`), front props.
- [ ] `paint(canvas, slide)`: paste baked layers, then character tiles for
      `slide.tick`, then labels/pill/note, then the caption band.
- [ ] Paste with alpha: `canvas.paste(tile, (x, y), tile)` onto the **RGB**
      canvas — no full-frame RGBA pass, no per-frame conversion.
- [ ] `look ∈ {whiteboard, flat2d, papercut}` resolved like `style_for`
      (`render/style.py:72`) — unknown falls back to `flat2d`.
- [ ] `motion ∈ {off, keys, smooth}` → 0 / 4 / 8 ticks a second.

Wiring

- [ ] `render/editor.py:272`: `_paint_slide` branches to `self._scene.paint(...)`
      **only when `slide.scene` is set**; the existing body stays as the else.
- [ ] `Editor.__init__` builds a lazy `ScenePainter` (slides may never use it).
- [ ] `Editor` invalidates the painter when the slide changes or on mode change.
- [ ] `render_plan(..., look=..., motion=...)` passes both through.

Tests — `engine/tests/test_scene.py`

- [ ] Front props are pasted after the cast (occlusion order).
- [ ] The caption band is never occluded by any layer.
- [ ] A slide with `scene=None` renders **byte-identical** to
      `engine/tests/fixtures/legacy_lesson.json`.
- [ ] Unknown `look` and `motion` fall back to defaults.
- [ ] `engine/tests/test_render_budget.py`: `keys` ≤ 1.8×, `smooth` ≤ 3.0×.

**Done when:** the legacy fixture is untouched and a new scene slide renders with
a moving camera, layered props and an animating character — inside the budget.

---

## P5 — the content contract

Prompts — `engine/src/videosynx/prompts.py`

- [ ] `EXPLAINER_PROMPT`: hook → stakes → what is actually true → the
      comparison → what would decide it → payoff. No code, no backticks, only
      figures present in the brief, contested claims named as contested.
- [ ] The prompt lists the closed vocabulary (10 poses, 6 expressions, 3 turns,
      5 shots, 16 props) with a worked `scene` example, the way the diagram
      vocabulary is listed at `prompts.py:181-220`.
- [ ] `explainer_prompt(topic, brief, length, focus)` with
      `short ≈ 8-12`, `medium ≈ 20-30`, `long ≈ 40-60` sentences.
- [ ] `parse_explainer(text)` → `(Lesson, publish_meta)` via
      `parse_json_object` (`prompts.py:328`) then `parse_lesson`.
- [ ] `TOPIC_PROMPT` + `candidates_prompt(brief, niche)` + `parse_topics(text)`
      → five ranked `{title, angle, why_now, keywords}` candidates.

Timeline — `engine/src/videosynx/timeline.py`

- [ ] `Scene`, `SceneCast`, `SceneProp` dataclasses.
- [ ] `parse_scene(raw)` beside `parse_diagram` (`:183`): unknown pose or prop →
      default, unknown character id dropped, counts bounded with `_bounded_int`.
- [ ] `Slide` gains `scene: Scene | None = None`, `tick: int = 0`,
      `mouth: float = 0.0` — primitives only, so `Slide.__eq__` stays cheap.
- [ ] `fold_steps` (`:820`) stamps `tick` and `mouth` onto every folded slide.

Lesson and settings

- [ ] `lesson.py::_load_explain` passes `scene=parse_scene(spec.get("scene"))`.
- [ ] `api/src/videosynx_api/settings.py`: `look` and `motion` join `Settings`
      beside `style`, and ride through `as_options()`.
- [ ] `worker.py`: pass `options["look"]` and `options["motion"]` into
      `render_plan`.

Tests

- [ ] `engine/tests/test_scene.py`: `parse_scene` drops unknown cast, bounds
      counts, keeps `steps` in lockstep with narration.
- [ ] `engine/tests/test_prose.py`: `parse_explainer` rejects a malformed
      section with a readable message; the `publish` key is ignored by
      `parse_lesson` (the invariant from `lesson.py:114-150`).
- [ ] `api/tests/test_api.py:118` still passes — API and engine agree on modes.
- [ ] `engine/tests/test_pipeline.py`: `mode=coding` with no scene renders
      unchanged.

**Done when:** a hand-written explainer JSON with a cast and scenes renders, and
nothing about `mode=coding` moved.

---

## P6 — the mouth moves with the audio

- [ ] `engine/src/videosynx/lipsync.py`: `envelope(samples, fps, ticks_per_s)`
      → `tuple[float, ...]`, RMS per tick, attack/release smoothed, normalised.
- [ ] `timeline.plan_explanation` (`:876`) computes the envelope per sentence
      and stamps `mouth` on each sub-span, using the split that already exists
      for arrow draw-in (`DRAW_STEPS`, `:44-49, 896-916`).
- [ ] The envelope is stored on the plan, not inside `VisualState` — floats only
      cross into the renderer.
- [ ] `engine/tests/test_lipsync.py`: length equals ticks; silence → 0; a ramp
      is monotonic; a loud transient decays before the next tick.
- [ ] `engine/tests/test_timeline.py`: a narrated slide's sub-spans carry a
      `mouth` value that changes across the sentence.

**Done when:** a narrated explainer has a character whose mouth follows the
voice, with no phoneme aligner and no extra dependency.

> **Milestone: a topic typed by hand now produces a finished 2D animated
> explainer.** Everything below is sourcing and delivery.

---

## P7 — reading the web

Implementation — `engine/src/videosynx/research.py`

- [ ] `Source(url, title, excerpt)` and `Brief(query, topic, sources, text,
      fetched_at)`.
- [ ] `search(query, limit=8)` against the DuckDuckGo HTML endpoint, behind a
      `Search` protocol so a keyed provider can replace it by configuration.
- [ ] `fetch(url, timeout=10)`: `urllib` + a `HTMLParser` subclass dropping
      `script`, `style`, `nav`, `footer`, `aside`; whitespace collapsed; 8 000
      characters a page.
- [ ] `research(topic, limit=5, budget_s=25)` → `Brief`, ≤ 5 pages, ≤ 120 000
      characters. **Never raises**: any failure returns an empty brief.
- [ ] One function owns the network so tests stub it, as `providers._post_json`
      (`providers.py:267`) does.
- [ ] `DISCOVERY_MAX_PAGES` honoured from the environment.

Tests — `engine/tests/test_research.py`

- [ ] Empty search gives an empty brief, not an exception.
- [ ] A network error gives an empty brief.
- [ ] Markup is stripped: no `<script>` text survives.
- [ ] Page and character caps hold.
- [ ] A URL that times out returns what was already gathered.

**Done when:** a topic becomes a brief with sources, offline-testable, and
nothing downstream has to handle an exception from it.

---

## P8 — two new job kinds

Queue — `worker/src/videosynx_worker/db.py`

- [ ] `Queue.attach_options(job_id, patch)` — `options = options || %s::jsonb`,
      beside `finish`.
- [ ] `Queue.get` already returns options; confirm a `discovery` job's results
      survive a re-read.

Worker — `worker/src/videosynx_worker/worker.py`

- [ ] Dispatch at `:515` becomes `speech` | `discovery` | `publish` | video.
- [ ] `discovery`: `research(topic)` → `candidates_prompt` → `parse_topics` →
      `attach_options({"candidates": [...]})`, no artifact written, beat `done`.
- [ ] Video path: when `options.brief` is present, build the lesson with
      `explainer_prompt` instead of `build_tutorial`; keep the old path when it
      is absent.
- [ ] Write the `.srt` when `options.publish` is set:
      `srt.to_srt(plan.captions)` → `video_key(tier, id, ".srt")` → storage.
- [ ] Store `publish_meta` (title, description, tags, made_for_kids) on the job.
- [ ] Wire `options["length"]` and `options["focus"]` into the prompt — the gap
      `PLAN.md` §2 names.
- [ ] A discovery failure is a normal job failure (retry path unchanged).

Tests — `worker/tests/test_worker.py`

- [ ] A `discovery` job writes candidates to options and produces no artifact.
- [ ] A video job with a brief writes both `.mp4` and `.srt`.
- [ ] A video job without a publish intent writes no `.srt`.
- [ ] `length="long"` reaches the prompt (assert on the prompt text).
- [ ] An unknown `kind` still renders a video (backwards compatible).

**Done when:** a topic can be researched and turned into a narrated explainer
entirely through the queue, and the same job carries the file and its captions.

---

## P9 — publishing

Dependencies

- [ ] `worker/pyproject.toml`: add `google-api-python-client`, `google-auth`,
      `google-auth-oauthlib`, `httpx`. Lazy-import them so the module imports
      without them installed.

Code

- [ ] `worker/src/videosynx_worker/publishers.py`: `Publisher` protocol,
      `PublishResult(remote_id, url)`, and `LocalPublisher` writing a JSON stub
      into storage.
- [ ] `worker/src/videosynx_worker/youtube.py`: `TokenStore.seal/open`
      (`cryptography.fernet.Fernet`, key from `VIDEOSYNX_TOKEN_KEY`).
- [ ] `YouTubePublisher`: refresh → `videos().insert(part="snippet,status",
      resumable=True)` → `captions().insert` with the job's `.srt`.
- [ ] Set privacy from the channel; set synthetic-media disclosure — **confirm
      the exact field name against the live API rather than trusting the plan**.
- [ ] Classify failures like `providers.classify_http` (`providers.py:275`):
      `busy` retries, `quota` waits, `permanent` fails the job with a reason.
- [ ] `publications` insert with `job_id` as the primary key — the guard that
      stops a retry uploading twice.
- [ ] Publish branch in `worker.py`: load artifact and `.srt`, upload, record
      the row, write `youtube_url` into options.

Tests — `worker/tests/test_publish.py`

- [ ] A successful publish records a row with a URL.
- [ ] A retried publish calls the publisher once, not twice.
- [ ] A missing `.srt` still publishes the video.
- [ ] A `busy` error leaves the job retryable; a `permanent` error does not.
- [ ] `TokenStore` round-trips and the sealed value contains no readable token.

**Done when:** a finished video reaches a channel with captions, exactly once,
and the whole path is testable with no network.

---

## P10 — channels and the API surface

Schema — `worker/src/videosynx_worker/db.py` `SCHEMA`

- [ ] `channels`, `discoveries`, `publications` as written in `PLAN.md` §5,
      idempotent like the tables at `db.py:97-140`.
- [ ] `worker/tests/test_queue.py`: `install()` twice does not fail.

Channels — `api/src/videosynx_api/channels.py`

- [ ] `start_url(user_id)` with `state = hmac(secret, user_id + expiry)`.
- [ ] `exchange(code)` → refresh token, channel id, channel title.
- [ ] `save`/`load`/`list`/`delete` against `channels`, token sealed at rest.
- [ ] No response object ever contains a token, sealed or otherwise.

Auth — `api/src/videosynx_api/auth.py`

- [ ] `current_user` (`:158`) gains one branch: `X-Service-Token` matching
      `VIDEOSYNX_SERVICE_TOKEN` resolves to the user id in the request.
- [ ] That branch is inert when the variable is unset.

Quota — `api/src/videosynx_api/quota.py`

- [ ] `used_today` and `unsettled` exclude `options->>'kind' = 'publish'`.
- [ ] `discovery` stays counted (it calls a model).

Routes — `api/src/videosynx_api/app.py`

- [ ] `POST /discover`, `GET /discover`, `POST /discover/{id}/make`
- [ ] `POST /jobs/{id}/publish` (idempotent on the job)
- [ ] `GET /channels`, `POST /channels/youtube/start`,
      `GET /channels/youtube/callback`, `PUT /channels/{id}`,
      `DELETE /channels/{id}`
- [ ] Every one uses `Depends(current_user)` and `submit_within_quota` where it
      creates a job.

Config and copy

- [ ] `.env.example`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
      `GOOGLE_REDIRECT_URI`, `VIDEOSYNX_TOKEN_KEY`, `VIDEOSYNX_STATE_SECRET`,
      `VIDEOSYNX_SERVICE_TOKEN`, `DISCOVERY_MAX_PAGES`.
- [ ] `features.py`: remove `any-topic` and `publish`; add `auto-pilot`.
- [ ] `api/tests/test_features.py:62-93`: move the sample id from `any-topic` to
      `narration-languages`.

Tests

- [ ] `api/tests/test_channels.py`: tampered `state` → 401; sealing round-trips;
      no token in any response.
- [ ] `api/tests/test_discovery.py`: submit, list, make; each needs auth.
- [ ] `api/tests/test_api.py`: a `publish` job does not consume the video
      allowance; a `discovery` job does.

**Done when:** a channel can be connected once, and a video can be queued for
upload by any client holding a service token.

---

## P11 — the planner

Implementation — `worker/src/videosynx_worker/planner.py`

- [ ] `Planner(api_url, service_token, interval_s)` looping every
      `PLANNER_INTERVAL_S` (default 300).
- [ ] Select enabled channels whose `last_planned_at` is older than
      `cadence_hours`.
- [ ] `POST /discover` for the channel's niche; read the candidates back.
- [ ] `POST /discover/{id}/make` for candidate zero.
- [ ] If `publish='auto'`, `POST /jobs/{id}/publish` once the video finishes.
- [ ] Stamp `last_planned_at` whether or not a video was made, so a failing
      cycle cannot spin.
- [ ] `worker/src/videosynx_worker/__main__.py`: `--plan` flag, sharing the
      existing logging and `dotenv` load.
- [ ] It is an HTTP client of the API — **no direct inserts**.

Tests — `worker/tests/test_planner.py` (fake `httpx` transport)

- [ ] A due channel produces exactly one discovery.
- [ ] A channel inside its cadence produces none.
- [ ] A disabled channel is skipped.
- [ ] `publish='review'` never calls publish.
- [ ] A failed discovery still stamps `last_planned_at`.

**Done when:** leaving `videosynx-worker --plan` running makes tomorrow's video
without anyone touching the app.

---

## P12 — the UI

Client — `web/app/lib/api.ts`

- [ ] Types: `Channel`, `Candidate`, `Discovery`, plus `look`/`motion` on
      `Settings`.
- [ ] Calls: `discover`, `discoveries`, `makeFrom`, `publish`, `channels`,
      `connectYouTube`, `updateChannel`, `deleteChannel`, `callback` handling.

Shell

- [ ] `components/Sidebar.tsx:34-39`: `{ label: "Discover", view: "discover" }`.
- [ ] `dashboard/page.tsx:31`: the `discover` label.
- [ ] `dashboard/page.tsx:386-407`: the view branch.

Views

- [ ] `dashboard/views.tsx`: `DiscoverView` — candidate cards with title, angle,
      why-now; "Make this"; channel picker; private/public toggle.
- [ ] `dashboard/views.tsx`: show the job's `youtube_url` on a published video.
- [ ] `components/SettingsPanel.tsx`: Channels block — connect (navigate to the
      returned URL), niche, cadence, privacy, `review`/`auto`, enabled.
- [ ] `components/SettingsPanel.tsx`: `look` and `motion` selects beside `style`.

Tests

- [ ] `web/tests/ui.spec.ts`: the Discover view renders candidates; "Make this"
      submits; the channel block lists a channel.
- [ ] `pnpm run lint`, `pnpm run typecheck`, `pnpm run build` all green.

**Done when:** the whole loop is usable without curl.

---

## Release checks

- [ ] End-to-end with `LocalPublisher`: discovery → video (`.mp4` + `.srt`) →
      publish → `publications` row.
- [ ] End-to-end against a real channel, upload set to `private`, then deleted.
- [ ] Allowance check: three videos, then a fourth `publish` still allowed;
      a fifth `discovery` refused.
- [ ] Render budget re-measured at 1080p and the constant in
      `test_render_budget.py` updated if it moved.
- [ ] `pytest` green in all four packages; `pnpm` checks green.
- [ ] `PLAN.md` status line changed from *proposed* to *in progress*, and the
      completed phases ticked here.
- [ ] `README.md` gains one paragraph and a link to `PLAN.md`; `usage.md` gains
      the `--plan` flag and the new endpoints.
- [ ] Branch `feat/scene-renderer` pushed; PR opened against `main`.

---

## Definition of done, whole feature

The account is connected once. `videosynx-worker --plan` is running. Nobody
touches anything for a day. A video appears on the paired channel, in 2D, with
characters whose mouths move, with captions, drawn from something that was
actually in the news — and the quota, the retries and the idempotency all held
while it happened.

---

## Not in this checklist

Anything in `PLAN.md` §11: true 3D, viseme lip-sync, photoreal assets, auto
thumbnails, TikTok and Instagram, voice cloning. Adding them here would make
this list a wish rather than a plan.
