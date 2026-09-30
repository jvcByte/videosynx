"use client";

import { useEffect, useState } from "react";
import { Job, RenderPlan, api } from "../lib/api";

/**
 * What the renderer actually produced.
 *
 * The code, the narration and every diagram are made afresh each run and
 * exist nowhere else, so a video that came out wrong cannot be diagnosed
 * without this -- and until now the endpoint that serves it had no way in
 * from the page. Every defect worth finding in the last few rounds was found
 * by pulling this out of the database by hand.
 *
 * Read-only, and deliberately plain: it is a record to read, not a control
 * panel. The one thing it has to get right is the frame count beside each
 * sentence, because that is what says which slide was on screen while the
 * wrong thing was being said.
 */
export function PlanViewer({ job, onClose }: { job: Job; onClose: () => void }) {
  const [plan, setPlan] = useState<RenderPlan | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .plan(job.id)
      .then((loaded) => { if (!cancelled) setPlan(loaded); })
      .catch(() => { if (!cancelled) setFailed(true); });
    return () => { cancelled = true; };
  }, [job.id]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const sections = plan?.sections ?? [];

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label="Render plan">
      <div className="overlay-backdrop" onClick={onClose} />
      <div className="overlay-body plan">
        <header className="overlay-head">
          <div>
            <h2>What was rendered</h2>
            <p className="sheet-note">{job.topic}</p>
          </div>
          <button className="ghost" onClick={onClose}>Close</button>
        </header>

        <div className="plan-body">
          {failed && (
            <p className="notice notice-bad" role="alert">
              <span>Could not load the plan.</span>
            </p>
          )}

          {!plan && !failed && <span className="skeleton skeleton-line" />}

          {plan?.note && (
            <p className="field-note">{plan.note}</p>
          )}

          {plan?.sections && (
            <p className="plan-summary figure">
              {sections.length} sections · {plan.duration_s?.toFixed(0)}s ·{" "}
              {plan.total_frames} frames
            </p>
          )}

          {sections.map((section, index) => (
            <article className="plan-section" key={index}>
              <h3>
                <span className="plan-kind">{section.kind}</span>
                {section.heading ?? ""}
              </h3>

              {section.columns?.map(([title, lines], i) => (
                <div className="plan-column" key={i}>
                  {title && <span className="plan-column-title">{title}</span>}
                  {lines.map((line, j) => <p key={j}>{line}</p>)}
                </div>
              ))}

              {section.diagram && (
                <p className="field-note">
                  diagram: {section.diagram.layout} ·{" "}
                  {section.diagram.nodes.map((node) => node.label).join(" → ")}
                </p>
              )}

              {section.note && <p className="plan-note">{section.note}</p>}

              {section.code && (
                <pre className="plan-code">{section.code}</pre>
              )}
              {section.output && section.output.length > 0 && (
                <pre className="plan-output">{section.output.join("\n")}</pre>
              )}

              <ol className="plan-narration">
                {section.narration.map((phrase, i) => (
                  <li key={i}>
                    <span className="plan-frames figure">{phrase.frames}f</span>
                    {phrase.text}
                  </li>
                ))}
              </ol>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
