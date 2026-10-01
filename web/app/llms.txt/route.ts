import { SITE_TAGLINE, SITE_URL } from "../lib/seo";

/**
 * What this is, in the shape an assistant reads it.
 *
 * `llms.txt` is the convention that a language model should be able to fetch
 * one small file and know what a site offers, rather than inferring it from
 * markup meant for a browser. It costs one route and it is the difference
 * between being described accurately by something that answers questions and
 * being described from whatever it managed to scrape.
 *
 * The prices are read from the API rather than written here. An assistant
 * that quotes a stale price is worse than one that says it does not know,
 * and this is the file most likely to be quoted.
 */

export const revalidate = 3600;

export async function GET() {
  // Imported lazily so a build with no API running still serves the file --
  // the prose below is the part that matters and it does not need a server.
  const { serverGet } = await import("../lib/api");
  const plans = await serverGet<{
    currency: string;
    plans: { id: string; price_usd: number; tagline: string }[];
    credit_packs: { credits: number; usd: number; note: string }[];
  }>("/plans");

  const priceLines = plans
    ? [
        "",
        "## Pricing",
        "",
        ...plans.plans.map(
          (plan) =>
            `- ${plan.id}: $${plan.price_usd}/month${plan.price_usd === 0 ? " (free forever)" : ""}` +
            (plan.tagline ? ` — ${plan.tagline}` : "")
        ),
        "",
        "A credit is one minute of finished video, charged when the render",
        "finishes. Past the daily allowance, one credit a minute:",
        "",
        ...plans.credit_packs.map(
          (pack) => `- ${pack.credits} credits (${pack.credits} minutes): $${pack.usd}`
        ),
      ]
    : [];

  const body = `# videosynx

> ${SITE_TAGLINE}

videosynx turns a topic into a finished, narrated video tutorial. It writes
the lesson, runs the code it wrote, narrates every line in a natural voice,
burns in captions, and hands back an MP4 with a matching .srt. Give it plain
text instead and it produces a voice-over.

It also publishes: a finished video can be posted to the channels an account
connects.

## What it makes

- Coding tutorials, from a sentence of subject, on any topic.
- Illustrated explanations, for concepts that need a picture before code.
- Voice-overs, from text that has already been written.
- Captions, burned into the frame and timed to the narration.
- A downloadable MP4 and SRT for every one.

## How it works

1. Submit a topic, a script, or a lesson file.
2. The lesson is written: code, explanation, slides and narration.
3. Every code snippet runs in a sandbox with no network, and its real output
   appears in the video.
4. Frames are drawn and encoded; nothing is screen-recorded.
5. The file is served from storage on a signed link.

## Facts

- Pricing: free to start. ${plans ? `Paid plans and credit packs are listed below.` : ""}
- Voices: 54 voices across 9 languages, plus a premium tier.
- Output: 720p on the free plan, 1080p on the paid ones; MP4 plus SRT.
- Accounts: the dashboard is behind a sign-in and is not indexed.
- API: a public HTTP API; \`GET /stats\`, \`GET /plans\` and \`GET /examples\`
  need no account.
${priceLines.join("\n")}

## Links

- [The product](${SITE_URL}/): what it is, what it makes, and what it costs.
- [Pricing](${SITE_URL}/#pricing): plans and credit packs.
- [Examples](${SITE_URL}/#examples): real rendered tutorials.
- [Dashboard](${SITE_URL}/dashboard): the signed-in workspace. Not indexed.
`;

  return new Response(body, {
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=0, s-maxage=3600, stale-while-revalidate=86400",
    },
  });
}
