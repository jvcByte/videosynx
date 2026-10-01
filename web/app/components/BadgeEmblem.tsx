import type { ReactNode } from "react";

/**
 * The emblem struck into each badge, one per badge.
 *
 * They were all a star. Twelve of the same disc with the same star in it is a
 * set you can tell apart only by reading, and the metal says how hard a badge
 * is, not which one it is -- so a row of them was four colours and one shape.
 * Rank insignia work the other way round: the shape is the rank, and you
 * know which one you are looking at before you read anything.
 *
 * Drawn as strokes on a 24-unit grid and struck *into* the metal rather than
 * laid on top of it -- see `.award-emblem`. Strokes rather than fills because
 * at twenty-two pixels a filled shape is a blob, and because a line has an
 * edge, which is what an impression in metal has.
 *
 * Every one is a picture of the thing it is for. A badge called Voice-over
 * shows a microphone, which is worth more than any purely decorative crest:
 * the shape is doing the same job the name is.
 */
const EMBLEMS: Record<string, ReactNode> = {
  // A tutorial is something you watch.
  first: <path d="M9.6 7.2 17 12l-7.4 4.8z" />,

  // Own Script: a page, with a folded corner and writing on it.
  scripted: (
    <>
      <path d="M7 3.6h6.4L18 8.2v12.2H7z" />
      <path d="M13.4 3.6v4.6H18" />
      <path d="M9.8 12.4h4.4M9.8 15.6h3" />
    </>
  ),

  // Local Mode: a chip -- it ran here, on this machine, with no model.
  local: (
    <>
      <rect x="7.6" y="7.6" width="8.8" height="8.8" rx="1.6" />
      <path d="M10.4 4v3.6M13.6 4v3.6M10.4 16.4V20M13.6 16.4V20" />
      <path d="M4 10.4h3.6M4 13.6h3.6M16.4 10.4H20M16.4 13.6H20" />
    </>
  ),

  // Voice-over: a microphone, stand and all.
  voiced: (
    <>
      <rect x="9.2" y="3.4" width="5.6" height="10.2" rx="2.8" />
      <path d="M6 11.4a6 6 0 0 0 12 0" />
      <path d="M12 17.4v3.2" />
    </>
  ),

  // Code Runner: the brackets code lives between.
  runner: (
    <>
      <path d="M9.4 7.4 5.4 12l4 4.6" />
      <path d="M14.6 7.4l4 4.6-4 4.6" />
    </>
  ),

  // Long-form: a film frame, wide, which is the whole of what it is.
  epic: (
    <>
      <rect x="2.8" y="7" width="18.4" height="10" rx="1.6" />
      <path d="M6.8 9.6v4.8M17.2 9.6v4.8" />
    </>
  ),

  // Undeterred: down, and then up and out the top.
  undeterred: (
    <>
      <path d="M3.6 17.2 9 11.8l3.2 3.2 8.2-8.2" />
      <path d="M15.6 6.8h4.8v4.8" />
    </>
  ),

  // Steady: a flame, which is what a streak is drawn as everywhere.
  steady: (
    <path d="M12 20.2a5.2 5.2 0 0 0 5.2-5.2c0-3.4-5.2-9.2-5.2-9.2s-5.2 5.8-5.2 9.2a5.2 5.2 0 0 0 5.2 5.2z" />
  ),

  // Marathon: an hourglass, for the hour rather than the video.
  marathon: (
    <>
      <path d="M6.6 3.8h10.8M6.6 20.2h10.8" />
      <path d="M8.2 3.8c0 4.2 3.8 5.4 3.8 8.2s-3.8 4-3.8 8.2" />
      <path d="M15.8 3.8c0 4.2-3.8 5.4-3.8 8.2s3.8 4 3.8 8.2" />
    </>
  ),

  // Prolific: a grid of them.
  prolific: (
    <>
      <rect x="4" y="4" width="6.6" height="6.6" rx="1.2" />
      <rect x="13.4" y="4" width="6.6" height="6.6" rx="1.2" />
      <rect x="4" y="13.4" width="6.6" height="6.6" rx="1.2" />
      <rect x="13.4" y="13.4" width="6.6" height="6.6" rx="1.2" />
    </>
  ),

  // Series: one frame with the next one behind it.
  series: (
    <>
      <rect x="8.4" y="4" width="12" height="9.6" rx="1.4" />
      <path d="M4.6 7.6v8.4a3.4 3.4 0 0 0 3.4 3.4h8.6" />
    </>
  ),

  // Devoted: a crown, for the thirty days rather than the seven.
  devoted: (
    <>
      <path d="M4 17.4 5.2 6.8l4.8 4L12 4.6l2 6.2 4.8-4L20 17.4z" />
      <path d="M5.6 20.4h12.8" />
    </>
  ),
};

/** An emblem for an id nobody has drawn one for: a plain stud. */
const UNKNOWN = <circle cx="12" cy="12" r="5" />;

export function BadgeEmblem({ id }: { id: string }) {
  return (
    <svg className="award-emblem" viewBox="0 0 24 24" aria-hidden focusable="false">
      {EMBLEMS[id] ?? UNKNOWN}
    </svg>
  );
}
