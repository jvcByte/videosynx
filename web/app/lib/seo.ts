/**
 * Where this site is, and the facts about it that a machine reads.
 *
 * Everything here exists because a crawler has no JavaScript and no context.
 * Google renders a page on a second pass and most AI crawlers do not render
 * it at all, so anything a machine needs -- the canonical address, the price,
 * what the thing is -- has to be in the HTML the first request returns. The
 * landing page has been a server component since the prices moved into it;
 * this is the other half of that decision.
 */

/**
 * The origin every absolute URL is built from.
 *
 * Set once, because a canonical URL that disagrees with the sitemap and the
 * OpenGraph tags is worse than no canonical at all -- it tells a crawler two
 * different things about the same page. `metadataBase` uses it, so the OG
 * image resolves on a deploy that is not on localhost.
 */
export const SITE_URL = (
  process.env.VIDEOSYNX_SITE_URL ?? "http://localhost:3000"
).replace(/\/+$/, "");

export const SITE_NAME = "videosynx";

/** One sentence, used wherever a machine wants a description. */
export const SITE_TAGLINE =
  "Turn a topic into a finished video tutorial, and publish it to your channels.";

/** The crawlers that read the page and quote it back to somebody. */
export const AI_CRAWLERS = [
  // OpenAI: training, search indexing, and the one that fetches when a user
  // asks a question with a URL in it.
  "GPTBot",
  "OAI-SearchBot",
  "ChatGPT-User",
  // Anthropic, the same three shapes.
  "ClaudeBot",
  "Claude-SearchBot",
  "Claude-User",
  // The answer engines.
  "PerplexityBot",
  "Perplexity-User",
  "YouBot",
  "DuckAssistBot",
  "MistralAI-User",
  // Google's and Apple's model-training feeds, which are separate switches
  // from their search indexes and are never on by default.
  "Google-Extended",
  "Applebot-Extended",
  // The common crawl everyone trains on.
  "CCBot",
  // Everyone else who asks politely.
  "Bytespider",
  "Amazonbot",
  "meta-externalagent",
  "cohere-ai",
  "ImagesiftBot",
  "Timpibot",
];

/**
 * The price and what it buys, as `offers` for the product.
 *
 * Read from the API rather than written here, so the figure a crawler quotes
 * is the figure the service charges. A hardcoded "$19" in a JSON-LD block is
 * a price that goes stale silently, and it goes stale in the one place nobody
 * looks at the page to check.
 */
export function offerFor(plans: {
  plans: { id: string; price_usd: number; tagline: string }[];
  currency: string;
}) {
  return plans.plans.map((plan) => ({
    "@type": "Offer",
    name: plan.tagline || plan.id,
    price: plan.price_usd,
    priceCurrency: plans.currency.toUpperCase(),
    // A free plan is not "in stock" or "out of stock"; it is free, which is
    // what a zero price says.
    availability: "https://schema.org/InStock",
  }));
}

/**
 * Other places this project is, if the operator has any.
 *
 * `sameAs` is how a search engine connects an organization to its other
 * profiles, and an empty array is worse than omitting the key -- it asserts
 * that the list is empty. Unset, the whole property is left off.
 */
export function socialProfiles(): string[] {
  return (process.env.VIDEOSYNX_SOCIAL_URLS ?? "")
    .split(",")
    .map((url) => url.trim())
    .filter(Boolean);
}

/** The logo, as schema.org wants it: an absolute URL to an image. */
export const LOGO_URL = `${SITE_URL}/logo-mark.webp`;

/** The identity of the thing itself, for the graph on every page. */
export function organisationSchema() {
  return {
    "@type": "Organization",
    "@id": `${SITE_URL}/#organization`,
    name: SITE_NAME,
    url: SITE_URL,
    logo: {
      "@type": "ImageObject",
      url: LOGO_URL,
    },
    description: SITE_TAGLINE,
    ...(socialProfiles().length ? { sameAs: socialProfiles() } : {}),
  };
}

/** The site, so a search engine knows which page is the canonical one. */
export function websiteSchema() {
  return {
    "@type": "WebSite",
    "@id": `${SITE_URL}/#website`,
    url: SITE_URL,
    name: SITE_NAME,
    description: SITE_TAGLINE,
    publisher: { "@id": `${SITE_URL}/#organization` },
    inLanguage: "en",
  };
}
