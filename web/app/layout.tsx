import type { Metadata } from "next";
import "./globals.css";

import {
  SITE_NAME,
  SITE_TAGLINE,
  SITE_URL,
  organisationSchema,
  socialProfiles,
  websiteSchema,
} from "./lib/seo";

export const metadata: Metadata = {
  // What every relative URL on the site is resolved against -- the OG image,
  // the canonical tag, the icons. Without it Next serves them relative, and a
  // crawler on another origin cannot fetch a relative image.
  metadataBase: new URL(SITE_URL),
  title: {
    default: `${SITE_NAME} — turn a topic into a finished video tutorial`,
    // Every page says what page it is, then the site. The other way round
    // puts the same word first in every result and buries the difference.
    template: `%s — ${SITE_NAME}`,
  },
  description: SITE_TAGLINE,
  applicationName: SITE_NAME,
  keywords: [
    "video tutorial generator",
    "text to speech",
    "narrated video",
    "programming tutorials",
    "screen recording alternative",
    "AI video generation",
    "explainer video",
  ],
  authors: [{ name: SITE_NAME, url: SITE_URL }],
  creator: SITE_NAME,
  publisher: SITE_NAME,
  alternates: { canonical: "/" },
  openGraph: {
    type: "website",
    siteName: SITE_NAME,
    locale: "en",
    url: SITE_URL,
    title: `${SITE_NAME} — turn a topic into a finished video tutorial`,
    description: SITE_TAGLINE,
  },
  twitter: {
    // `summary_large_image` even with no account: the card is what makes a
    // link worth clicking in a timeline, and it is the image that decides it.
    card: "summary_large_image",
    title: `${SITE_NAME} — turn a topic into a finished video tutorial`,
    description: SITE_TAGLINE,
  },
  robots: {
    index: true,
    follow: true,
    // What a crawler may do with what it found. `max-image-preview:large` is
    // the one that matters: without it a search result shows a thumbnail, and
    // the whole product is pictures.
    googleBot: {
      index: true,
      follow: true,
      "max-image-preview": "large",
      "max-snippet": -1,
      "max-video-preview": -1,
    },
  },
  formatDetection: { telephone: false, address: false, email: false },
  ...(socialProfiles().length
    ? { other: { "og:see_also": socialProfiles().join(" ") } }
    : {}),
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

/**
 * The graph every page carries: who this is and where it lives.
 *
 * One `<script type="application/ld+json">` rather than several, with the
 * nodes referenced by `@id`. Two blocks describing the same organization are
 * two organizations as far as a parser is concerned.
 *
 * Rendered here rather than fetched, because it is the same on every page and
 * a crawler should not have to run anything to find out who wrote this.
 */
function IdentitySchema() {
  const graph = {
    "@context": "https://schema.org",
    "@graph": [organisationSchema(), websiteSchema()],
  };
  return (
    <script
      type="application/ld+json"
      // The only way to put JSON-LD in a document. The content is ours and
      // built from a frozen object, not from anything a visitor supplied.
      dangerouslySetInnerHTML={{ __html: JSON.stringify(graph) }}
    />
  );
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: APPLY_THEME }} />
        <IdentitySchema />
      </head>
      <body>{children}</body>
    </html>
  );
}
