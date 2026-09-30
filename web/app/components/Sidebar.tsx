"use client";

import Link from "next/link";

/**
 * The dashboard's navigation.
 *
 * The entries are anchors into one page rather than routes of their own:
 * three of the four are sections of it, and separate routes would mean three
 * more pages that each have to load the same jobs, settings and allowance.
 * A link that scrolls is honest about that; a link that swaps to a page
 * showing the same data would not be.
 */
export function Sidebar({
  email,
  onSettings,
  onSignOut,
}: {
  email: string | null;
  onSettings: () => void;
  onSignOut?: () => void;
}) {
  // No profile to read a name from -- the account is an id, a tier and an
  // email -- so the initial is the most that can honestly be shown.
  const initial = (email ?? "?").trim().charAt(0).toUpperCase() || "?";

  return (
    <aside className="sidebar">
      <Link className="sidebar-logo" href="/">
        <img src="/logo-mark.png" alt="" width={20} height={20} />
        pycoding
      </Link>

      <nav className="sidebar-nav">
        <a href="#top" className="is-current">Dashboard</a>
        <a href="#videos">My tutorials</a>
        <a href="#new">Create tutorial</a>
        <button className="sidebar-link" onClick={onSettings}>Settings</button>
      </nav>

      <div className="sidebar-foot">
        {email && (
          <div className="sidebar-user">
            <span className="sidebar-avatar" aria-hidden>{initial}</span>
            <span className="sidebar-email" title={email}>{email}</span>
          </div>
        )}
        {onSignOut && (
          <button className="ghost sidebar-signout" onClick={onSignOut}>
            Sign out
          </button>
        )}
      </div>
    </aside>
  );
}
