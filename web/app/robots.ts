import type { MetadataRoute } from "next";

import { AI_CRAWLERS, SITE_URL } from "./lib/seo";

/**
 * Who may read what.
 *
 * The landing page is the whole of the public site, so it is open to
 * everything. Everything else is behind a sign-in and is refused -- not
 * because a crawler could get in, but so that a shared link to somebody's
 * dashboard does not end up indexed under our name with their email on it.
 *
 * The AI crawlers are named and allowed. Left to the wildcard they are
 * allowed anyway, and naming them is not a permission but a statement: it is
 * a decision to be readable by the things people now ask instead of
 * searching, recorded where anyone auditing the site can find it.
 */
export default function robots(): MetadataRoute.Robots {
  const refused = ["/dashboard", "/api/", "/auth/"];

  return {
    rules: [
      { userAgent: "*", allow: "/", disallow: refused },
      // Crawlers that honour a named group use it *instead of* the wildcard,
      // so this group has to repeat the refusals rather than inherit them.
      { userAgent: AI_CRAWLERS, allow: "/", disallow: refused },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
