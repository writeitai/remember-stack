import type { Metadata } from "next";
import { Hanken_Grotesk } from "next/font/google";
import "./globals.css";
import { SiteHeader } from "@/components/site/SiteHeader";

// Open-font stand-in for writeit.ai's domain-locked proxima-nova. Self-hosted
// by next/font so the site stays a self-contained module.
const hanken = Hanken_Grotesk({
  subsets: ["latin"],
  variable: "--font-hanken",
});

const siteUrl = "https://docs.remember.dev";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "RememberStack — Documentation",
    template: "%s — RememberStack",
  },
  description:
    "Open memory infrastructure for AI agents: auditable, navigable knowledge at scale.",
  openGraph: {
    title: "RememberStack — Documentation",
    description:
      "A memory system for AI agents: millions of documents distilled into auditable, navigable knowledge.",
    url: siteUrl,
    siteName: "RememberStack",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className={`${hanken.variable} font-sans antialiased`}>
        <SiteHeader />
        <main>{children}</main>
      </body>
    </html>
  );
}
