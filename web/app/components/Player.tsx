"use client";

import { useEffect } from "react";

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
          <button className="ghost" onClick={onClose}>Close</button>
        </div>
        <video key={url} src={url} controls autoPlay data-testid="player" />
      </div>
    </div>
  );
}
