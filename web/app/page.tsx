import type { Metadata } from "next";
import Link from "next/link";

import { ThemeToggle } from "./components/ThemeToggle";
import {
  CreditPack, Plan, Plans, PublishedExample, Stats, absolute, serverGet,
} from "./lib/api";
import { duration, modeLabel } from "./lib/format";

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
  title: "videosynx — turn any topic into a coding video tutorial",
  description:
    "Give it a subject and it writes the code, runs it, explains each line " +
    "while the code types itself out, narrates it, and produces a finished " +
    "MP4 with captions. Free to start.",
  openGraph: {
    title: "videosynx — coding video tutorials from a topic",
    description:
      "Code, execution, narration, captions and a finished MP4, from one " +
      "sentence of subject.",
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

// One mark each, and they are punctuation rather than signal: six small
// glyphs in the same muted colour, because six coloured icons for six
// equally-weighted claims is the decoration this page is meant to avoid.
const FEATURES = [
  {
    mark: "✦",
    title: "AI-powered generation",
    body: "Give it a topic and it writes the code, the narration and the slides — a complete tutorial, not a snippet.",
  },
  {
    mark: "⌘",
    title: "Real code execution",
    body: "Every snippet runs in a sandbox with no network, and its actual output appears in the video.",
  },
  {
    mark: "CC",
    title: "Captions and subtitles",
    body: "Burned in and exactly timed to the voice, with a matching .srt written alongside.",
  },
  {
    mark: "◇",
    title: "Illustrated explanations",
    body: "Concepts get drawn slides before any code — shapes, arrows and the words for the idea.",
  },
  {
    mark: "◈",
    title: "Sandboxed by default",
    body: "Generated code is treated as hostile: no network, no home directory, no inherited environment.",
  },
  {
    mark: "→",
    title: "Bring your own script",
    body: "Paste a script instead of a topic and it renders what you wrote, on this machine, with no model call at all.",
  },
];

const STEPS = [
  { n: "1", title: "Submit a topic", body: "Or paste a script, or upload lesson JSON. Through the page or the API." },
  { n: "2", title: "The lesson is written", body: "Code, explanation, slides and narration, in that order." },
  { n: "3", title: "The video is rendered", body: "Frames are drawn and piped to ffmpeg. Nothing is screen-recorded." },
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
  return (
    <li className={`plan${plan.price_usd > 0 ? "" : " plan-free"}`}>
      <h3>{plan.name}</h3>
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
        {plan.price_usd > 0 ? `Choose ${plan.name}` : "Start free"}
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
            <span className="landing-eyebrow">AI-powered coding tutorials</span>
            <h1>
              Turn any topic into a <em>professional coding</em> video tutorial.
            </h1>
            <p className="landing-lede">
              Give it a subject and it writes the code, runs it, explains each
              line while the code types itself out on screen, narrates it, and
              produces a finished MP4 with captions.
            </p>
            <div className="landing-actions">
              <Link className="landing-cta landing-cta-lg" href="/dashboard#create">
                Create your first tutorial
              </Link>
              <a className="landing-ghost landing-cta-lg" href="#how">See how it works</a>
            </div>
            <ul className="landing-checks">
              <li>Generated code</li>
              <li>Real execution</li>
              <li>Captions + SRT</li>
              <li>MP4 output</li>
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
          <span className="landing-eyebrow landing-eyebrow-center">What it does</span>
          <h2 className="landing-h2">Everything you need to create technical tutorials</h2>
          <p className="landing-sub">From prompt to polished video.</p>
        </header>

        <div className="landing-features">
          {FEATURES.map((feature) => (
            <article className="landing-feature" key={feature.title}>
              <span className="landing-feature-mark" aria-hidden>{feature.mark}</span>
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
            </article>
          ))}
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
              Every plan makes real videos. The paid ones make more of them,
              bigger, and without a watermark.
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
          Give it a topic. It does the rest — code, execution, narration,
          captions, and a video you can download.
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
            <a href="/dashboard">Dashboard</a>
          </div>
        </div>
      </footer>
    </div>
  );
}
