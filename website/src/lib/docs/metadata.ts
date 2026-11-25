import type { Metadata } from "next";

/** Keep the canonical and Open Graph URLs aligned on both docs hosts. */
export function docsPageMetadata(canonical: string): Metadata {
  return {
    alternates: { canonical },
    openGraph: {
      // Next fills title and description from the current page's metadata.
      url: canonical,
      siteName: "Remember Documentation",
      type: "website",
      images: [
        {
          url: "https://docs.remember.dev/brand/docs-social.png",
          type: "image/png",
          width: 1200,
          height: 630,
          alt: "Remember brain mark",
        },
      ],
    },
  };
}
