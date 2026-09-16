import type { MDXComponents } from "mdx/types";
import type { AnchorHTMLAttributes, HTMLAttributes } from "react";
import Link from "next/link";

// Route internal links through next/link so they navigate client-side and pick
// up `trailingSlash: true` (avoiding a GitHub Pages 301 on every doc link).
// External links open in a new tab; in-page anchors stay plain <a>.
function MdxAnchor({
  href = "",
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  if (href.startsWith("/")) {
    return (
      <Link href={href} {...props}>
        {children}
      </Link>
    );
  }
  const external = /^https?:\/\//.test(href);
  return (
    <a
      href={href}
      {...(external ? { target: "_blank", rel: "noreferrer" } : {})}
      {...props}
    >
      {children}
    </a>
  );
}

function MdxLead({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p className={["lead-statement", className].filter(Boolean).join(" ")} {...props}>
      {children}
    </p>
  );
}

// Required by @next/mdx. Global MDX element styling is handled by the
// `prose` classes on the docs <article>.
export function useMDXComponents(components: MDXComponents): MDXComponents {
  return { ...components, a: MdxAnchor, Lead: MdxLead };
}
