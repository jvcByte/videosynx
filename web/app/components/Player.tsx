"use client";

import { useEffect } from "react";
import { videoFilename } from "../lib/format";

/**
 * The video, over the page rather than in it.
 *
 * This is one of the two places depth is spent, and it earns it: the player
 * genuinely sits above everything and takes the whole focus while it is open.
 * Escape closes it, because a full-bleed overlay with only a small button to
 * leave is a trap.
 */
export function Player({
  url,
  title,
  onClose,
}: {
  url: string;
  title: string;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="overlay" role="dialog" aria-modal="true" aria-label={title}>
      <button className="overlay-backdrop" aria-label="Dismiss" onClick={onClose} />
      <div className="overlay-body">
        <div className="overlay-head">
          <h2>{title}</h2>
          <div className="overlay-actions">
            {/* A plain link with `download`, not a button that fetches and
                clicks something: the browser already knows how to save a
                same-origin URL, and this way it can stream rather than buffer
                the whole file into memory first. */}
            <a className="ghost" href={url} download={videoFilename(title)}>
              Download
            </a>
            <button className="ghost" onClick={onClose}>Close</button>
          </div>
        </div>
        <video key={url} src={url} controls autoPlay data-testid="player" />
      </div>
    </div>
  );
}
