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

The four things the product promises, in the order its own page promises
them:

- A video tutorial from any topic: the lesson written, the code run with its
  real output on screen, narrated, and captioned.
- A voice-over, from plain text instead of a topic.
- A published video, posted to the channels an account connects.
- An MP4 and a matching .srt for every one, on a signed link.

## How it works

1. Submit a topic, a script, or a lesson file.
2. The lesson is written: code, explanation, slides and narration.
3. Every code snippet runs in a sandbox with no network, and its real output
   appears in the video.
4. Frames are drawn and encoded; nothing is screen-recorded.
5. The file is served from storage on a signed link.

## Submitting a lesson yourself

\`POST /jobs\` takes a \`lesson\` object, or the same JSON as text in \`script\`.
Either way it is rendered exactly as written -- nothing rewrites it and no
model is called for it, so it costs no allowance beyond the render. This is
how to draw a diagram rather than describe one to be drawn.

A lesson is a title and a list of sections. A section is either \`code\` with
narration anchored to fragments of it, or \`explain\` -- a narrated slide:

    {
      "title": "How a loop runs",
      "sections": [{
        "name": "One pass",
        "explain": {
          "heading": "Around the loop",
          "diagram": {
            "layout": "cycle",
            "nodes": [{"id": "i", "label": "next item"},
                      {"id": "b", "label": "body"}],
            "edges": [["i", "b"], ["b", "i"]]
          },
          "sequence": ["i", "b"]
        },
        "narration": ["Take the next item.", "Run the body.", "Round again."]
      }]
    }

A slide shows a \`diagram\`, or \`columns\` of text, or a \`sequence\` row. One
narrated sentence reveals the next piece of it, in the order given: for a
diagram that is one node per sentence, and the arrows into a new node are
drawn in as that sentence is spoken. Shapes are box, round, cylinder, cloud,
document, person and stack; layouts are row, column, grid, cycle, tree and
stack.

### Showing something change

A diagram can also report on the shapes that are already there, which is how a
slide demonstrates rather than describes: the sentence says what happened and
the picture does it, while the sentence is spoken. Give the diagram
\`"steps"\`, one per sentence after the heading:

    "steps": [
      {"show": ["users", "machine", "db"], "pill": "1,000 users"},
      {"pill": "100,000 users", "tone": "busy"},
      {"status": {"machine": "busy"}, "pill": "struggling", "tone": "busy"},
      {"status": {"machine": "failed"}, "pill": "overloaded", "tone": "failed"}
    ]

\`show\` names the shapes on screen, cumulatively -- use it when the picture
should be whole from the start and only change afterwards. \`status\` marks a
shape \`ok\`, \`busy\` or \`failed\`, and it stays until something changes it.
\`pill\` is a short reading in a box above the diagram, coloured by \`tone\`.
The narration then needs one sentence more than there are steps.

Any of it that is wrong is refused with a 422 naming the part, before the job
is queued and before the allowance is touched.

## Facts

- Pricing: free to start, with paid plans and credit packs below.
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
