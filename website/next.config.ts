import type { NextConfig } from "next";
import createMDX from "@next/mdx";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  // Fully static site — exported to `out/` and served by GitHub Pages.
  output: "export",
  // Directory-style URLs (`/docs/getting-started/`) resolve to index.html on a
  // static host that does not rewrite clean URLs (GitHub Pages).
  trailingSlash: true,
  // Let `page.mdx` files be routes, the way the Next.js docs are authored.
  pageExtensions: ["js", "jsx", "md", "mdx", "ts", "tsx"],
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
