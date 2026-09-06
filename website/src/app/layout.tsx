import type { Metadata } from "next";
import { Inter, Space_Grotesk } from "next/font/google";
import "./globals.css";
import { SiteHeader } from "@/components/site/SiteHeader";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
});

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  variable: "--font-space-grotesk",
});

const siteUrl = "https://remember.dev";
const docsUrl = "https://remember.dev/docs";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "Remember — Documentation",
    template: "%s — Remember",
  },
  description:
    "Open memory infrastructure for AI agents: auditable, navigable knowledge at scale.",
  icons: {
    icon: "/brand/mark.svg",
  },
  openGraph: {
    title: "Remember — Documentation",
    description:
      "A memory system for AI agents: millions of documents distilled into auditable, navigable knowledge.",
    siteName: "Remember Documentation",
    type: "website",
  },
  alternates: {
    canonical: "./",
  },
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body
        className={`${inter.variable} ${spaceGrotesk.variable} font-sans antialiased`}
      >
        <SiteHeader />
        <main>{children}</main>
      </body>
    </html>
  );
}
