"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { Stats, api } from "./lib/api";

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

const FEATURES = [
  {
    title: "AI-powered generation",
    body: "Give it a topic and it writes the code, the narration and the slides — a complete tutorial, not a snippet.",
  },
  {
    title: "Real code execution",
    body: "Every snippet runs in a sandbox with no network, and its actual output appears in the video.",
  },
  {
    title: "Captions and subtitles",
    body: "Burned in and exactly timed to the voice, with a matching .srt written alongside.",
  },
  {
    title: "Illustrated explanations",
    body: "Concepts get drawn slides before any code — shapes, arrows and the words for the idea.",
  },
  {
    title: "Sandboxed by default",
    body: "Generated code is treated as hostile: no network, no home directory, no inherited environment.",
  },
  {
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

/** What the service can make, as opposed to what it has made. */
const EXAMPLES = [
  { title: "Data types in Python", body: "A billing engine where a value arriving as a string is the bug.", tag: "Python" },
  { title: "Asynchronous ingestion", body: "One market stall against three, and why the queue is what helps.", tag: "Concepts" },
  { title: "When to fine-tune", body: "Fifteen scenes on choosing a strategy instead of reaching for one.", tag: "ML" },
];

export default function Landing() {
  // Null until it arrives. Not zero: see the note above.
  const [stats, setStats] = useState<Stats | null>(null);

  useEffect(() => {
    api.stats().then(setStats).catch(() => setStats(null));
  }, []);

  const hasNumbers = stats !== null && stats.tutorials_rendered > 0;

  return (
    <div className="landing">
      <nav className="landing-nav">
        <div className="landing-wrap landing-nav-inner">
          <span className="landing-logo">pycoding</span>
          <div className="landing-links">
            <a href="#features">Features</a>
            <a href="#how">How it works</a>
            <a href="#examples">Examples</a>
          </div>
          <div className="landing-nav-actions">
            <Link className="landing-ghost" href="/dashboard">Sign in</Link>
            <Link className="landing-cta" href="/dashboard">Get started</Link>
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
              <Link className="landing-cta landing-cta-lg" href="/dashboard">
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
              <span className="landing-mock-title">main.py — pycoding</span>
            </div>
            <pre className="landing-mock-code">
              <span className="k">from</span> flask <span className="k">import</span> Flask{"\n"}
              {"\n"}
              app = <span className="f">Flask</span>(__name__){"\n"}
              {"\n"}
              <span className="d">@app.route(</span><span className="s">&quot;/&quot;</span><span className="d">)</span>{"\n"}
              <span className="k">def</span> <span className="f">home</span>():{"\n"}
              {"    "}<span className="k">return</span> <span className="f">render_template</span>(<span className="s">&quot;index.html&quot;</span>)
            </pre>
            <div className="landing-mock-term">
              <span className="landing-mock-label">TERMINAL</span>
              <span className="landing-mock-out">Serving on http://127.0.0.1:5000</span>
            </div>
          </div>
        </div>
      </header>

      <section className="landing-wrap landing-features" id="features">
        {FEATURES.map((feature) => (
          <article className="landing-feature" key={feature.title}>
            <h3>{feature.title}</h3>
            <p>{feature.body}</p>
          </article>
        ))}
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

      <section className="landing-wrap landing-examples" id="examples">
        <span className="landing-eyebrow">Example tutorials</span>
        <h2 className="landing-h2">See it in action</h2>
        <p className="landing-sub">
          Some of what the service produces. Every one of these is generated
          from a topic or a script — no footage, no screen recording.
        </p>
        <div className="landing-cards">
          {EXAMPLES.map((example) => (
            <article className="landing-card" key={example.title}>
              <div className="landing-card-art" aria-hidden>
                <span className="landing-card-play" />
              </div>
              <h3>{example.title}</h3>
              <p>{example.body}</p>
              <span className="landing-tag">{example.tag}</span>
            </article>
          ))}
        </div>
      </section>

      {/* Nothing to report is not the same as reporting nothing. Three zeroes
          is a true measurement and a bad sentence, so the band only appears
          once there is something in it. */}
      {hasNumbers && (
      <section className="landing-stats">
        <div className="landing-wrap">
          <dl>
            <div>
              <dt>Tutorials generated</dt>
              <dd>{stats ? stats.tutorials_rendered : "—"}</dd>
            </div>
            <div>
              <dt>Minutes of video</dt>
              <dd>{stats ? Math.round(stats.minutes_of_video) : "—"}</dd>
            </div>
            <div>
              <dt>Topics covered</dt>
              <dd>{stats ? stats.topics_covered : "—"}</dd>
            </div>
          </dl>
          <p className="landing-stats-note">
            Counted from the renders this service has actually completed.
          </p>
        </div>
      </section>
      )}

      <section className="landing-wrap landing-final">
        <h2 className="landing-h2">Ready to create your first tutorial?</h2>
        <p className="landing-sub">
          Give it a topic. It does the rest — code, execution, narration,
          captions, and a video you can download.
        </p>
        <Link className="landing-cta landing-cta-lg" href="/dashboard">
          Start for free
        </Link>
      </section>

      <footer className="landing-footer">
        <div className="landing-wrap landing-footer-inner">
          <span className="landing-logo">pycoding</span>
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
