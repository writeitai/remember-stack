import type { NextConfig } from "next";
import createMDX from "@next/mdx";

// Build switch for the hosted-service (remember.dev) docs. Off by default: the
// public build has no `page.cloud.mdx` routes, and `<Cloud>` passages, cloud
// tabs and the AppliesTo badge render nothing. `DOCS_CLOUD=1` builds them all.
const docsCloud = process.env.DOCS_CLOUD === "1";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  assetPrefix: "/docs",
  // Fully static site — exported to `out/` and served at remember.dev/docs.
  output: "export",
  // Directory-style URLs (`/docs/getting-started/`) resolve to index.html on a
  // static host that does not rewrite clean URLs.
  trailingSlash: true,
  // Let `page.mdx` files be routes, the way the Next.js docs are authored.
  // `page.cloud.mdx` is a route only in the cloud build.
  pageExtensions: [
    ...(docsCloud ? ["cloud.mdx"] : []),
    "js", "jsx", "md", "mdx", "ts", "tsx",
  ],
  // Inlined at build time, so every component sees the value decided here.
  env: { DOCS_CLOUD: docsCloud ? "1" : "0" },
  images: { unoptimized: true },
};

const withMDX = createMDX({
  options: {
    remarkPlugins: ["remark-gfm"],
    rehypePlugins: [
      "rehype-slug",
      ["rehype-pretty-code", { theme: "github-dark", keepBackground: false }],
    ],
  },
});

export default withMDX(nextConfig);
