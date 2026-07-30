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

const siteUrl = "https://docs.remember.dev";

export const metadata: Metadata = {
  metadataBase: new URL(siteUrl),
  title: {
    default: "RememberStack — Documentation",
    template: "%s — RememberStack",
  },
  description:
    "Open memory infrastructure for AI agents: auditable, navigable knowledge at scale.",
  icons: {
    icon: "/brand/mark.svg",
  },
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
      <body
        className={`${inter.variable} ${spaceGrotesk.variable} font-sans antialiased`}
      >
        <SiteHeader />
        <main>{children}</main>
      </body>
    </html>
  );
}
