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
    title: "Overview & Architecture",
    href: "/docs",
    children: [
      { title: "Why Remember", href: "/docs" },
      { title: "vs Vector RAG", href: "/docs/why" },
      { title: "Concepts", href: "/docs/concepts" },
      { title: "Architecture", href: "/docs/architecture" },
      { title: "Knowledge (Plane K)", href: "/docs/knowledge" },
    ],
  },
  {
    title: "Universal Quickstart",
    href: "/docs/getting-started",
    children: [
      { title: "Getting started", href: "/docs/getting-started" },
      { title: "Agent harnesses", href: "/docs/harnesses" },
    ],
  },
  {
    title: "Core Capabilities & SDK",
    href: "/docs/reference/sdk",
    children: [
      { title: "Python SDK (remember)", href: "/docs/reference/sdk" },
      { title: "Platform CLI", href: "/docs/reference/cli" },
      { title: "Model Context Protocol (MCP)", href: "/docs/reference/mcp" },
      { title: "Assured retrieval", href: "/docs/retrieval" },
      { title: "Response envelope", href: "/docs/retrieval/envelope" },
      { title: "Open query space (SQL)", href: "/docs/retrieval/open-query" },
      { title: "Primitives catalog", href: "/docs/retrieval/primitives" },
      { title: "REST API reference", href: "/docs/reference/api" },
    ],
  },
  {
    title: "Self-Hosting & Operations",
    href: "/docs/deployment",
    children: [
      { title: "Docker Compose quickstart", href: "/docs/deployment" },
      { title: "Ingestion overview", href: "/docs/ingestion" },
      { title: "Pipeline stages (E0–E3)", href: "/docs/ingestion/pipeline" },
      { title: "Lifecycle & versions", href: "/docs/ingestion/lifecycle" },
      { title: "Mounts and skill", href: "/docs/mounts" },
      { title: "Configuration", href: "/docs/configuration" },
      { title: "Troubleshooting", href: "/docs/troubleshooting" },
      { title: "Evaluation (eval-banana)", href: "/docs/evaluation" },
      { title: "Project status", href: "/docs/project-status" },
    ],
  },
  {
    title: "Remember Cloud Platform",
    href: "/docs/cloud/architecture",
    children: [
      { title: "Cloud architecture", href: "/docs/cloud/architecture" },
      { title: "Cloud CLI", href: "/docs/cloud/cli" },
      { title: "Billing & credits", href: "/docs/cloud/billing" },
      { title: "Security & isolation", href: "/docs/cloud/security" },
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
