import type { MetadataRoute } from "next";

import { SITE_URL } from "./lib/seo";

/**
 * Everything a stranger is allowed to read.
 *
 * One entry, because there is one public page. The examples on it are videos
 * rather than pages -- they open in a new tab from a signed link -- and the
 * dashboard is behind a sign-in, so neither belongs here.
 *
 * A sitemap with one URL is still worth having: it is the canonical list of
 * what a crawler should fetch, and it says by omission that everything else
 * is not for it. The dashboard is also marked `noindex` on the page itself,
 * because a robots.txt refusal and a meta tag are different instructions and
 * a URL that leaks into a search result by another route needs the second.
 */
export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: `${SITE_URL}/`,
      lastModified: new Date(),
      changeFrequency: "weekly",
      // The page it links to is what changes; this is the marketing page for
      // it, and it is rewritten when the product is.
      priority: 1,
    },
  ];
}
