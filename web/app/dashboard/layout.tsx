import type { Metadata } from "next";

/**
 * The dashboard is nobody's business but its owner's.
 *
 * `noindex` here as well as the refusal in `robots.ts`, because they are
 * different instructions and only one of them travels with the page. A
 * crawler that reaches `/dashboard` from a link somebody pasted -- rather
 * than from a crawl that started at the root -- never reads robots.txt for
 * it, and a page that carries its own instruction cannot be fetched without
 * being told.
 *
 * `nocache` too: an archived copy of somebody's workspace outlives the
 * account it belonged to.
 *
 * A separate file because this page is a client component, and a client
 * component cannot export metadata -- the exports below are read by the
 * server that renders it, and there is nowhere in the page itself to say so.
 */
export const metadata: Metadata = {
  title: "Workspace",
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: { index: false, follow: false, noimageindex: true },
  },
};

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return children;
}
