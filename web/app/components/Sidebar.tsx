"use client";

import Link from "next/link";

/**
 * The dashboard's navigation.
 *
 * The entries are anchors into one page rather than routes of their own:
 * everything they point at is a section of it, and separate routes would mean
 * several more pages that each load the same jobs, settings and allowance. A
 * link that scrolls says what it does; a link that swaps to a page showing the
 * same data would not.
 */
export function Sidebar({
  email,
  level,
  percent,
  onSettings,
  onSignOut,
}: {
  email: string | null;
  level: number | null;
  /** How far into the current level, 0-100, from the server's own XP. */
  percent: number | null;
  onSettings: () => void;
  onSignOut?: () => void;
}) {
  // No profile to read a name from -- the account is an id, a tier and an
  // email -- so the initial is the most that can honestly be shown.
  const initial = (email ?? "?").trim().charAt(0).toUpperCase() || "?";

  const items: { label: string; href?: string; onClick?: () => void }[] = [
    { label: "Dashboard", href: "#top" },
    { label: "My tutorials", href: "#videos" },
    { label: "Create tutorial", href: "#new" },
    { label: "Achievements", href: "#achievements" },
    // Leaderboard is deliberately absent: it ranks accounts against each
    // other, and this instance has one. A table of one is not a leaderboard.
    { label: "Settings", onClick: onSettings },
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
        {items.map((item, i) =>
          item.href ? (
            <a key={item.label} href={item.href} className={i === 0 ? "is-current" : ""}>
              {item.label}
            </a>
          ) : (
            <button key={item.label} className="sidebar-link" onClick={item.onClick}>
              {item.label}
            </button>
          )
        )}
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
