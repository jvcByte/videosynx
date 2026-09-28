import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "pycoding",
  description: "Generate narrated coding tutorial videos from a topic.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
