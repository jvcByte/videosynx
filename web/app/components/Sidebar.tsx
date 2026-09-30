"use client";

import Link from "next/link";
import { View } from "../lib/view";

/**
 * The dashboard's navigation.
 *
 * Each entry is a view of the same page rather than a route of its own, but
 * they are links all the same: the address carries the view, so the back
 * button works, a refresh keeps you where you were, and one can be sent to
 * someone. A button that set a variable would do none of those.
 */
export function Sidebar({
  email,
  level,
  percent,
  view,
  onSignOut,
}: {
  email: string | null;
  level: number | null;
  /** How far into the current level, 0-100, from the server's own XP. */
  percent: number | null;
  /** Which view is on screen, so the nav can mark it. */
  view: View;
  onSignOut?: () => void;
}) {
  // No profile to read a name from -- the account is an id, a tier and an
  // email -- so the initial is the most that can honestly be shown.
  const initial = (email ?? "?").trim().charAt(0).toUpperCase() || "?";

  const items: { label: string; view: View }[] = [
    { label: "Dashboard", view: "overview" },
    { label: "Your Videos", view: "videos" },
    { label: "Create Video", view: "create" },
    { label: "Text to speech", view: "speech" },
    { label: "Wallet", view: "wallet" },
    { label: "Achievements", view: "achievements" },
    // One entry for everything not built yet rather than one each. Four more
    // rows would push this bar off the side of a phone, and the list grows.
    { label: "Coming soon", view: "coming-soon" },
    // Leaderboard is deliberately absent: it ranks accounts against each
    // other, and this instance has one. A table of one is not a leaderboard.
    { label: "Settings", view: "settings" },
  ];

  return (
    <aside className="sidebar">
      <Link className="sidebar-logo" href="/">
        <img src="/logo-mark.webp" alt="" width={30} height={30} />
        <span>
          <b>videosynx</b>
          <small>Learn · Build · Create</small>
        </span>
      </Link>

      <nav className="sidebar-nav">
        {items.map((item) => (
          <a
            key={item.view}
            href={`#${item.view}`}
            // Not a class: which one is current is a fact about the page, and
            // `aria-current` is how that fact is already spelled.
            aria-current={item.view === view ? "page" : undefined}
            // Nothing on the page carries these ids, so the browser does not
            // scroll on its own -- without this, arriving from a long list of
            // videos leaves you halfway down a short view. Not in the hook:
            // that would fire on Back too, and undo scroll restoration.
            onClick={() => window.scrollTo(0, 0)}
          >
            {item.label}
          </a>
        ))}
      </nav>

      {/* The design's motivational block. The bar is the account's real
          progress through its level -- it was `level * 18` at first, which
          is a number that looks like progress and is not. */}
      <div className="sidebar-note">
        <p>Small steps build great programmers.</p>
        <span className="sidebar-note-level">
          {level === null ? "Keep going!" : `Level ${level} · keep going!`}
        </span>
        <span className="sidebar-note-bar" aria-hidden>
          <span style={{ width: `${percent ?? 0}%` }} />
        </span>
      </div>

      <div className="sidebar-foot">
        {email && (
          <div className="sidebar-user">
            <span className="sidebar-avatar" aria-hidden>{initial}</span>
            <span className="sidebar-email" title={email}>{email}</span>
          </div>
        )}
        {onSignOut && (
          <button className="ghost sidebar-signout" onClick={onSignOut}>Sign out</button>
        )}
      </div>
    </aside>
  );
}
