import { ImageResponse } from "next/og";

import { SITE_NAME, SITE_TAGLINE } from "./lib/seo";

/**
 * The card every link to this site unfurls into.
 *
 * Drawn rather than stored, because a PNG in `public/` is a picture of the
 * product as it was the day somebody exported it -- and the headline on this
 * page has changed three times since it was written. This is rendered from
 * the same words the page says.
 *
 * It is the first thing most people will ever see of this, in a timeline or a
 * search result or a link preview in a chat, and it is fetched by every one of
 * the crawlers in `robots.ts` whether or not they render the page.
 */
export const size = { width: 1200, height: 630 };
export const contentType = "image/png";
export const alt = `${SITE_NAME} — ${SITE_TAGLINE}`;

export default function OpengraphImage() {
  return new ImageResponse(
    (
      <div
        style={{
          width: "100%",
          height: "100%",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          padding: "72px 80px",
          // The app's own dark: a green-black, not a neutral one.
          background: "linear-gradient(150deg, #0a0f0e 0%, #12211c 60%, #0d3a2b 100%)",
          color: "#eef4f1",
          fontFamily: "sans-serif",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
          <div
            style={{
              width: 46,
              height: 46,
              borderRadius: 12,
              background: "#e8b04b",
              color: "#1a1204",
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              fontSize: 28,
              fontWeight: 700,
            }}
          >
            ▶
          </div>
          <div style={{ fontSize: 34, fontWeight: 700, letterSpacing: -0.5 }}>
            {SITE_NAME}
          </div>
        </div>

        <div
          style={{
            display: "flex",
            fontSize: 68,
            fontWeight: 700,
            lineHeight: 1.1,
            letterSpacing: -2,
            maxWidth: 940,
          }}
        >
          Turn a topic into a finished video tutorial.
        </div>

        {/* Not the tagline again -- that says what the headline already said.
            This line is the part that makes it worth a click: what actually
            comes out, and that it costs nothing to try. */}
        <div style={{ display: "flex", fontSize: 28, color: "#8b9a95" }}>
          Real code execution · 54 voices, 9 languages · captions and MP4 · free to start
        </div>
      </div>
    ),
    size
  );
}
