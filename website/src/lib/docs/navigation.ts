export type NavItem = {
  title: string;
  href: string;
  children?: NavItem[];
};

// Single source of truth for the docs sidebar order and grouping. It also
// drives prev/next pagination. Add a page here when you add its page.mdx.
// Order is progressive disclosure: why → core model → write path → read path
// → operate → reference.
export const docsNavigation: NavItem[] = [
  {
    title: "Start here",
    href: "/docs",
    children: [
      { title: "Why RememberStack", href: "/docs" },
      { title: "Getting started", href: "/docs/getting-started" },
      { title: "vs passage RAG", href: "/docs/why" },
    ],
  },
  {
    title: "Core model",
    href: "/docs/concepts",
    children: [
      { title: "Concepts", href: "/docs/concepts" },
      { title: "Architecture", href: "/docs/architecture" },
      { title: "Knowledge (Plane K)", href: "/docs/knowledge" },
    ],
  },
  {
    title: "Ingestion",
    href: "/docs/ingestion",
    children: [
      { title: "Ingestion overview", href: "/docs/ingestion" },
      { title: "Pipeline stages", href: "/docs/ingestion/pipeline" },
      { title: "Lifecycle & versions", href: "/docs/ingestion/lifecycle" },
    ],
  },
  {
    title: "Retrieval",
    href: "/docs/retrieval",
    children: [
      { title: "Retrieval overview", href: "/docs/retrieval" },
      { title: "Response envelope", href: "/docs/retrieval/envelope" },
      { title: "Open query space", href: "/docs/retrieval/open-query" },
      { title: "Primitives catalog", href: "/docs/retrieval/primitives" },
    ],
  },
  {
    title: "Operate",
    href: "/docs/mounts",
    children: [
      { title: "Mounts and skill", href: "/docs/mounts" },
      { title: "Self-host deployment", href: "/docs/deployment" },
      { title: "Configuration", href: "/docs/configuration" },
      { title: "Troubleshooting", href: "/docs/troubleshooting" },
      { title: "Evaluation", href: "/docs/evaluation" },
      { title: "Project status", href: "/docs/project-status" },
    ],
  },
  {
    title: "Reference",
    href: "/docs/reference/api",
    children: [
      { title: "API Reference", href: "/docs/reference/api" },
      { title: "CLI Reference", href: "/docs/reference/cli" },
      { title: "MCP Reference", href: "/docs/reference/mcp" },
    ],
  },
];

export function flattenNavigation(items: NavItem[]): NavItem[] {
  const result: NavItem[] = [];
  for (const item of items) {
    result.push(item);
    if (item.children) {
      result.push(...flattenNavigation(item.children));
    }
  }
  return result;
}

export function findAdjacentPages(pathname: string): {
  prev: NavItem | null;
  next: NavItem | null;
} {
  // De-duplicate on href so a section header that points at its first child
  // does not create a self-adjacency.
  const seen = new Set<string>();
  const flat = flattenNavigation(docsNavigation).filter((item) => {
    if (seen.has(item.href)) return false;
    seen.add(item.href);
    return true;
  });

  const normalize = (p: string) => (p.length > 1 ? p.replace(/\/$/, "") : p);
  const target = normalize(pathname);
  const index = flat.findIndex((item) => normalize(item.href) === target);
  if (index === -1) {
    return { prev: null, next: null };
  }
  return {
    prev: index > 0 ? flat[index - 1] : null,
    next: index < flat.length - 1 ? flat[index + 1] : null,
  };
}
