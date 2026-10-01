import type { Metadata } from "next";
import Link from "next/link";

import { ThemeToggle } from "./components/ThemeToggle";
import {
  CreditPack, Plan, Plans, PublishedExample, Stats, absolute, serverGet,
} from "./lib/api";
import { duration, modeLabel, tierLabel } from "./lib/format";

/**
 * The public page, rendered on the server.
 *
 * It was a client component that fetched its numbers, its examples and its
 * prices after loading, which meant the HTML a crawler received had the
 * headings and none of the substance -- and Google renders JavaScript on a
 * second pass while most AI crawlers do not render it at all. The facts
 * worth quoting, including what this costs, are now in the document. The
 * only interactive thing on the page is the theme toggle, and that was
 * already its own client component.
 */
export const metadata: Metadata = {
  title: "videosynx — turn any topic into a video, and publish it",
  description:
    "Give it a topic and it writes the script, narrates it in a natural " +
    "voice, renders a finished video and posts it to your channels. Give it " +
    "plain text instead and it reads that aloud. Free to start.",
  openGraph: {
    title: "videosynx — any topic into a video, published",
    description:
      "A topic becomes a finished, narrated video, published to your " +
      "channels. Plain text becomes a voice-over. Free to start.",
    type: "website",
  },
};

/**
 * The public page.
 *
 * Everything the app does lives at /dashboard; this is what someone sees
 * before they have an account, and its job is to say what the thing is and
 * get out of the way.
 *
 * The numbers in the band are measured. They come from /stats, which counts
 * rows, and a value that has not arrived shows as an em dash rather than a
 * zero -- "0 tutorials" and "we have not asked yet" are different claims and
 * only one of them is true.
 */

/**
 * One thing the service makes: what you get, what you type, and what comes
 * back.
 *
 * The card is the shape of the transaction. The label names the output, the
 * pill is the instruction that produces it, and the preview underneath is the
 * result — so the page says "this in, that out" without a paragraph about
 * either. The previews are drawn rather than screenshotted, in the same
 * renderer chrome the hero uses, because a picture of a video that was made
 * once goes stale and a drawn one cannot.
 *
 * Nothing here names a model or a voice engine. A visitor is buying the video
 * and the narration, not the parts they are assembled from.
 */
const MAKERS = [
  {
    kind: "code",
    shape: "wide",
    label: "Video generation",
    pill: "Explain Python list comprehensions",
    body: "A topic becomes a lesson — written, narrated, and handed back as an MP4.",
  },
  {
    kind: "post",
    shape: "tall",
    label: "Publish to social",
    pill: "Post it when it is done",
    body: "One click when the render finishes, and it is on the channels you post to.",
  },
  {
    kind: "voice",
    shape: "small",
    label: "Text to speech",
    pill: "Read this aloud, calmly",
    body: "Plain text in, a voice-over out, with the pauses in the right places.",
  },
  {
    kind: "script",
    shape: "small",
    label: "Your own script",
    pill: "Render this script I wrote",
    body: "Already written it? Paste it in and it renders what you wrote.",
  },
] as const;

/** The waveform in the speech preview, in pixels. Fixed, not random: this is
 *  rendered on the server, and a random height would differ between the pass
 *  that makes the HTML and the one that hydrates it. */
const WAVE = [
  16, 28, 44, 62, 40, 72, 55, 30, 46, 68, 52, 34,
  58, 22, 40, 54, 30, 48, 64, 26, 42, 36, 52, 18,
];

/** A rendered frame: code, typed out, over the line being read. */
function ShotCode() {
  return (
    <div className="shot shot-code" aria-hidden>
      {/* One source line each: the body renders with `white-space: pre` to
          keep the indent, so a line break here would be one on screen. */}
      <div className="shot-body">
        <span><b className="k">def</b> <b className="f">total</b>(prices):</span>
        <span>{"    "}<b className="k">return</b> <b className="f">sum</b>(prices)</span>
        <span><b className="d">print</b>(<b className="f">total</b>([<b className="n">2</b>, <b className="n">4</b>]))<b className="caret" /></span>
      </div>
      {/* The caption band and the progress line along its foot: the two
          things that say this is a frame of a video rather than a code
          editor with a sentence bolted under it. */}
      <div className="shot-caption">
        A function takes a list. It gives back one number.
        <span className="shot-scrub" />
      </div>
    </div>
  );
}

/** A clip: the words, and the sound of them. */
function ShotVoice() {
  return (
    <div className="shot shot-voice" aria-hidden>
      <div className="shot-body">
        <div className="shot-wave">
          {WAVE.map((height, i) => <i key={i} style={{ height }} />)}
        </div>
      </div>
      <div className="shot-meta">
        <span className="shot-play" />
        <span className="figure">0:14</span>
        <span className="shot-chip">Calm</span>
      </div>
    </div>
  );
}

/**
 * A published post, laid out the way a feed lays one out.
 *
 * The one preview that is not about making the video: it is the same render
 * seen from the other end, on a channel, with a title somebody wrote for it.
 */
function ShotPost() {
  return (
    <div className="shot shot-post" aria-hidden>
      <div className="shot-body">
        <div className="shot-post-head">
          <span className="shot-avatar" />
          <span className="shot-post-who">videosynx</span>
          <span className="shot-post-when">just now</span>
        </div>
        {/* The render itself, shrunk to the shape a feed shows it at, with
            the caption burned in where the renderer puts it. An empty box
            with a play glyph would have been the same picture with the
            content left out. */}
        <div className="shot-post-frame">
          <div className="shot-post-mini">
            <span><b className="k">def</b> <b className="f">total</b>(prices):</span>
            <span>{"    "}<b className="k">return</b> <b className="f">sum</b>(prices)</span>
            <span><b className="d">print</b>(<b className="f">total</b>([<b className="n">2</b>, <b className="n">4</b>]))</span>
          </div>
          <span className="shot-play" />
          <span className="shot-post-cap">
            A function takes a list. It gives back one number.
          </span>
        </div>
        <div className="shot-post-title">List comprehensions, explained</div>
        <div className="shot-channels">
          <span>YouTube</span><span>TikTok</span><span>Instagram</span>
        </div>
      </div>
      <div className="shot-meta">
        <span className="shot-check" />
        <span>Published</span>
      </div>
    </div>
  );
}

/** Prose rather than code, because that is what a script someone wrote is. */
function ShotScript() {
  return (
    <div className="shot shot-script" aria-hidden>
      <div className="shot-body">
        <span>Today we look at list comprehensions.</span>
        <span>They build one list out of another.</span>
        <span><b className="caret" /></span>
      </div>
      <div className="shot-meta">
        <span className="shot-play" />
        <span className="figure">1:12</span>
        <span className="shot-chip">Your script</span>
      </div>
    </div>
  );
}

const SHOTS = {
  code: ShotCode,
  post: ShotPost,
  voice: ShotVoice,
  script: ShotScript,
};

// No tool is named in any of these. The steps are what happens to the video,
// and which encoder drew the frames is not part of that story.
const STEPS = [
  { n: "1", title: "Submit a topic", body: "Or paste your own script, or upload a lesson file. Through the page or the API." },
  { n: "2", title: "The lesson is written", body: "Code, explanation, slides and narration, in that order." },
  { n: "3", title: "The video is rendered", body: "Every frame is drawn and encoded here. Nothing is screen-recorded." },
  { n: "4", title: "You get the file", body: "An MP4 and an SRT, served from storage on a signed link." },
];

/**
 * What the service can make, as opposed to what it has made.
 *
 * Shown only while nothing has been published — these carry no durations,
 * because a running time for a video nobody rendered is the same kind of
 * claim as a user count nobody earned.
 */
const EXAMPLES = [
  { title: "Data types in Python", body: "A billing engine where a value arriving as a string is the bug.", tag: "Python" },
  { title: "Asynchronous ingestion", body: "One market stall against three, and why the queue is what helps.", tag: "Concepts" },
  { title: "When to fine-tune", body: "Fifteen scenes on choosing a strategy instead of reaching for one.", tag: "ML" },
];

/** A card is either a published render or one of the topics above. */
type Example = Partial<PublishedExample> & { title: string; body?: string; tag?: string };

function ExampleCard({ example }: { example: Example }) {
  const art = (
    <>
      {/* Through `absolute`, like every other URL the API hands out. The
          API answers on /artifacts/... but the browser only reaches it
          through the /api rewrite, so an un-prefixed path is a request to
          the Next server, which has never served one. */}
      {example.poster_url
        ? <img className="landing-card-poster" src={absolute(example.poster_url)} alt="" />
        : null}
      <span className="landing-card-play" />
      {example.duration_s != null && (
        <span className="landing-card-time figure">
          {duration(example.duration_s)}
        </span>
      )}
    </>
  );

  return (
    <article className="landing-card">
      {example.video_url ? (
        // Straight to the file: the landing page has no player, and a video
        // that opens in its own tab is one less thing to build here.
        <a className="landing-card-art" href={absolute(example.video_url)}
           target="_blank" rel="noreferrer"
           aria-label={`Play ${example.title}`}>
          {art}
        </a>
      ) : (
        <div className="landing-card-art" aria-hidden>{art}</div>
      )}

      <h3>{example.title}</h3>
      {example.body && <p>{example.body}</p>}
      <div className="landing-card-tags">
        <span className="landing-tag">{example.tag ?? modeLabel(example.mode ?? "both")}</span>
      </div>
    </article>
  );
}

/** One plan. The price and the limits both come from the API's tiers. */
function PlanCard({ plan }: { plan: Plan }) {
  // The API sends the tier's own name, which is a database value. What a plan
  // is called is this page's business.
  const name = tierLabel(plan.id);
  return (
    <li className={`plan${plan.price_usd > 0 ? "" : " plan-free"}`}>
      <h3>{name}</h3>
      <p className="plan-tagline">{plan.tagline}</p>
      <p className="plan-price figure">
        ${plan.price_usd}
        <span>{plan.price_usd === 0 ? "forever" : "a month"}</span>
      </p>
      <ul className="plan-features">
        {plan.features.map((feature) => (
          <li key={feature}>{feature}</li>
        ))}
      </ul>
      <Link
        className={plan.price_usd > 0 ? "landing-ghost" : "landing-cta"}
        href="/dashboard#create"
      >
        {plan.price_usd > 0 ? `Choose ${name}` : "Start free"}
      </Link>
    </li>
  );
}

/** One pack of credits. */
function PackCard({ pack }: { pack: CreditPack }) {
  return (
    <li className="pack">
      <span className="pack-credits figure">{pack.credits}</span>
      <span className="pack-unit">credits</span>
      <span className="pack-price figure">${pack.usd}</span>
      <span className="pack-note">{pack.note}</span>
      {/* To the wallet rather than straight to Stripe: buying is a signed-in
          act, and the price ids live on the server. */}
      <Link className="landing-ghost" href="/dashboard#wallet">Buy credits</Link>
    </li>
  );
}

export default async function Landing() {
  // Fetched on the server, so every one of these is in the HTML. Null on a
  // failure rather than zero: see the note above the figure band.
  const [stats, examples, plans] = await Promise.all([
    serverGet<Stats>("/stats"),
    // A failure leaves the section on the topics it can make, which is the
    // same thing it shows before anything has been published.
    serverGet<PublishedExample[]>("/examples", { cache: "fresh" }),
    serverGet<Plans>("/plans"),
  ]);

  const hasNumbers = stats !== null && stats.tutorials_rendered > 0;

  return (
    <div className="landing">
      <nav className="landing-nav">
        <div className="landing-wrap landing-nav-inner">
          <span className="landing-logo">
            <img src="/logo-mark.webp" alt="" width={26} height={26} />
            videosynx
          </span>
          <div className="landing-links">
            <a href="#features">Features</a>
            <a href="#how">How it works</a>
            <a href="#examples">Examples</a>
            <a href="#pricing">Pricing</a>
          </div>
          <div className="landing-nav-actions">
            {/* The page follows the theme now, so a visitor whose system
                prefers dark arrives on a dark page -- with no way out of it
                but a system setting, unless the choice is offered here. */}
            <ThemeToggle />
            <Link className="landing-ghost landing-signin" href="/dashboard">Sign in</Link>
            <Link className="landing-cta" href="/dashboard#create">Get started</Link>
          </div>
        </div>
      </nav>

      <header className="landing-hero">
        <div className="landing-wrap landing-hero-inner">
          <div>
            {/* The two things this makes, said before anything else. The
                eyebrow used to read "AI-powered coding tutorials", which is
                a sentence about how it is built rather than about what
                arrives at the end of it. */}
            <span className="landing-eyebrow">Video generation · Text to speech</span>
            {/* The accent goes on one phrase, not the whole headline: two
                lines of gold is a highlight that has stopped highlighting
                anything. "Any topic" is what is new here -- the page used to
                sell coding tutorials, and the thing makes videos. */}
            <h1>
              Turn <em>any topic</em> into a video, then publish it to social.
            </h1>
            <p className="landing-lede">
              Give it a subject and it writes the lesson, narrates it in a
              natural voice, renders a finished video, and posts it where you
              publish. Give it your own text instead and it reads that aloud.
            </p>
            <div className="landing-actions">
              <Link className="landing-cta landing-cta-lg" href="/dashboard#create">
                Create your first video
              </Link>
              <a className="landing-ghost landing-cta-lg" href="#how">See how it works</a>
            </div>
            <ul className="landing-checks">
              <li>Any topic</li>
              <li>Natural narration</li>
              <li>Publish to social</li>
              <li>Text to speech</li>
            </ul>
          </div>

          {/* A drawn impression of the output, not a screenshot: it is the
              same chrome the renderer paints, so it cannot go stale. */}
          <div className="landing-mock" aria-hidden>
            <div className="landing-mock-bar">
              <span className="landing-dot" /><span className="landing-dot" /><span className="landing-dot" />
              <span className="landing-mock-title">main.py — videosynx</span>
            </div>
            <pre className="landing-mock-code">
              <span className="k">from</span> fastapi <span className="k">import</span> FastAPI{"\n"}
              {"\n"}
              app = <span className="f">FastAPI</span>(){"\n"}
              {"\n"}
              <span className="d">@app.get(</span><span className="s">&quot;/&quot;</span><span className="d">)</span>{"\n"}
              <span className="k">async def</span> <span className="f">home</span>():{"\n"}
              {"    "}<span className="k">return</span> {"{"}<span className="s">&quot;message&quot;</span>: <span className="s">&quot;Hello, World&quot;</span>{"}"}
            </pre>
            <div className="landing-mock-term">
              <span className="landing-mock-label">TERMINAL</span>
              <span className="landing-mock-out">Uvicorn running on http://127.0.0.1:8000</span>
            </div>
          </div>
        </div>
      </header>

      <section className="landing-wrap landing-features-wrap" id="features">
        <header className="landing-section-head">
          <span className="landing-eyebrow landing-eyebrow-center">What it makes</span>
          <h2 className="landing-h2">Videos, voice-overs, and a place to put them.</h2>
          <p className="landing-sub">
            Give it a topic and it writes the lesson, narrates it and captions
            it. Give it your own text and it reads that aloud instead.
          </p>
        </header>

        <div className="landing-makers">
          {MAKERS.map((maker) => {
            const Shot = SHOTS[maker.kind];
            return (
              <article className={`maker maker-${maker.shape}`} key={maker.label}>
                <h3 className="maker-label">{maker.label}</h3>
                <div className="maker-shot">
                  {/* The instruction, floated over the result: what you type
                      sits on top of what comes back, which is the whole idea
                      of the card. It is the one thing in here a screen reader
                      should read -- the preview under it is a picture. */}
                  <span className="maker-pill">{maker.pill}</span>
                  <Shot />
                </div>
                <p className="maker-body">{maker.body}</p>
              </article>
            );
          })}
        </div>
      </section>

      <section className="landing-band" id="how">
        <div className="landing-wrap">
          <span className="landing-eyebrow landing-eyebrow-center">How it works</span>
          <h2 className="landing-h2">From idea to video in minutes</h2>
          <p className="landing-sub">Four steps, and only the first one is yours.</p>
          <ol className="landing-steps">
            {STEPS.map((step) => (
              <li key={step.n}>
                <span className="landing-step-n">{step.n}</span>
                <h3>{step.title}</h3>
                <p>{step.body}</p>
              </li>
            ))}
          </ol>
        </div>
      </section>

      <section className="landing-examples" id="examples">
        <div className="landing-wrap landing-examples-inner">
          <div className="landing-examples-aside">
            <span className="landing-eyebrow">Example tutorials</span>
            <h2 className="landing-h2">See it in action</h2>
            <p className="landing-sub">
              Beautiful, clear, and engaging coding tutorials — generated
              automatically.
            </p>
            <Link className="landing-cta" href="/dashboard">
              View more examples →
            </Link>
          </div>

          <div className="landing-cards">
            {/* Real renders when the operator has published any, and the
                topics the service can make when they have not. Both are
                true; only one of them has a video behind it. */}
            {(examples && examples.length > 0 ? examples : EXAMPLES).map((example) => (
              <ExampleCard key={example.title} example={example} />
            ))}
          </div>
        </div>
      </section>

      {/* Nothing to report is not the same as reporting nothing. Three zeroes
          is a true measurement and a bad sentence, so the band only appears
          once there is something in it. */}
      {hasNumbers && (
      <section className="landing-stats">
        <div className="landing-wrap">
          <header className="landing-stats-head">
            <span className="landing-stats-eyebrow">Platform activity</span>
            {/* One statement of what these numbers are, not two. It said
                "real usage from completed renders" and then, three lines
                later, "based on completed renders". */}
            <p className="landing-stats-sub">Real usage, counted from completed renders.</p>
          </header>

          <dl>
            <div>
              <dd>{stats ? stats.tutorials_rendered : "—"}</dd>
              <dt>Tutorials created</dt>
            </div>
            <div>
              <dd>{stats ? Math.round(stats.minutes_of_video) : "—"}</dd>
              <dt>Video minutes</dt>
            </div>
            <div>
              <dd>{stats ? stats.topics_covered : "—"}</dd>
              <dt>Topics covered</dt>
            </div>
          </dl>
        </div>
      </section>
      )}

      {plans && plans.plans.length > 0 && (
        <section className="landing-wrap landing-pricing" id="pricing">
          <header className="landing-section-head">
            <span className="landing-eyebrow landing-eyebrow-center">Pricing</span>
            <h2 className="landing-h2">Start free. Pay when it earns it.</h2>
            <p className="landing-sub">
              Every plan makes finished videos with real narration. The paid
              plans make more of them, in higher resolution, and without a
              watermark.
            </p>
          </header>

          <ul className="plans">
            {plans.plans.map((plan) => (
              <PlanCard key={plan.id} plan={plan} />
            ))}
          </ul>

          {/* Credits, under the plans rather than beside them: they are not a
              fourth plan, they are what you buy when a busy week runs past
              the one you are on. */}
          <div className="packs">
            <div className="packs-head">
              <h3>Credits</h3>
              <p>
                {plans.credits_per_job === 1
                  ? "One credit is one tutorial, whenever you have used up the day's."
                  : `${plans.credits_per_job} credits are one tutorial, past the day's.`}
              </p>
            </div>
            <ul className="packs-row">
              {plans.credit_packs.map((pack) => (
                <PackCard key={pack.credits} pack={pack} />
              ))}
            </ul>
          </div>
        </section>
      )}

      <section className="landing-wrap landing-final">
        <h2 className="landing-h2">Ready to create your first tutorial?</h2>
        <p className="landing-sub">
          Give it a topic, or give it a paragraph to read. Either way you get a
          file back — a video, or a voice-over.
        </p>
        <Link className="landing-cta landing-cta-lg" href="/dashboard#create">
          Start for free
        </Link>
      </section>

      <footer className="landing-footer">
        <div className="landing-wrap landing-footer-inner">
          <span className="landing-logo">
            <img src="/logo-mark.webp" alt="" width={26} height={26} />
            videosynx
          </span>
          <div className="landing-links">
            <a href="#features">Features</a>
            <a href="#how">How it works</a>
            <a href="#examples">Examples</a>
            <a href="#pricing">Pricing</a>
            <a href="/dashboard">Dashboard</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
