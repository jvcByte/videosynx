import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "videosynx",
  description: "Generate narrated coding tutorial videos from a topic.",
};

/**
 * Which theme to paint, decided before the first frame.
 *
 * Deliberately a blocking inline script rather than an effect in a component.
 * A theme applied after hydration is a theme applied after the page has
 * already painted the other one, which is the white flash everyone
 * recognises. It is four lines and it has to be here; there is nowhere later
 * that works.
 *
 * The saved choice wins, then what the system asks for, then dark -- the
 * dashboard's own look, and the fallback for a browser that expresses no
 * preference at all.
 */
const APPLY_THEME = `
try {
  var saved = localStorage.getItem("videosynx-theme");
  var theme = (saved === "light" || saved === "dark") ? saved
    : (window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");
  document.documentElement.dataset.theme = theme;
} catch (e) {
  document.documentElement.dataset.theme = "dark";
}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: APPLY_THEME }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
