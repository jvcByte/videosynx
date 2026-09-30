"use client";

import { useEffect, useState } from "react";

/**
 * Dark or light.
 *
 * The attribute is set on <html> by a script in the layout before the page
 * paints, so this only has to read what that script decided and offer to
 * change it. Reading it in an effect rather than during render keeps the
 * server and client markup identical -- the server cannot know which theme a
 * browser holds, and guessing produces a mismatch.
 */
export const THEME_KEY = "videosynx-theme";

export function ThemeToggle() {
  const [theme, setTheme] = useState<"dark" | "light" | null>(null);

  useEffect(() => {
    const current = document.documentElement.dataset.theme;
    setTheme(current === "light" ? "light" : "dark");
  }, []);

  function flip() {
    const next = theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    setTheme(next);
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      // A browser refusing storage is not a reason to refuse the change; it
      // just means the choice does not survive the tab.
    }
  }

  return (
    <button
      className="ghost theme-toggle"
      onClick={flip}
      aria-label={`Switch to the ${theme === "light" ? "dark" : "light"} theme`}
      title={`Switch to the ${theme === "light" ? "dark" : "light"} theme`}
    >
      {/* Nothing until the effect has read the attribute: rendering one icon
          and swapping it is a flash of the wrong one. */}
      {theme === "light" ? "☾" : theme === "dark" ? "☀" : " "}
    </button>
  );
}
