export type NavItem = {
  title: string;
  href: string;
  children?: NavItem[];
};

// Single source of truth for the docs sidebar order and grouping. It also
// drives prev/next pagination. Add a page here when you add its page.mdx.
//
// Hosted-service pages (`page.cloud.mdx`) sit inside
// `...(process.env.DOCS_CLOUD === "1" ? [...] : [])`. The value is inlined at
// build time, so the public build's bundle does not contain these entries.
export const docsNavigation: NavItem[] = [
  {
    title: "Start",
    href: "/docs",
    children: [
      { title: "What is a memory system?", href: "/docs/start/what-is-a-memory-system" },
      { title: "Why RememberStack", href: "/docs" },
      { title: "A five-minute tour", href: "/docs/start/how-it-works" },
      ...(process.env.DOCS_CLOUD === "1"
        ? [{ title: "remember.dev or self-hosted?", href: "/docs/start/choose" }]
        : []),
      { title: "Quickstart", href: "/docs/start/quickstart" },
      { title: "Connect your coding agent", href: "/docs/start/connect-your-agent" },
    ],
  },
  {
    title: "Concepts",
    href: "/docs/concepts/documents-and-sources",
    children: [
      { title: "Documents, versions and sources", href: "/docs/concepts/documents-and-sources" },
      { title: "Claims: what a source said", href: "/docs/concepts/claims" },
      { title: "Facts: what is held true", href: "/docs/concepts/facts" },
      { title: "Entities and identity", href: "/docs/concepts/entities" },
      { title: "Time", href: "/docs/concepts/time" },
      { title: "Evidence and provenance", href: "/docs/concepts/evidence" },
      { title: "Contradictions, corroboration and supersession", href: "/docs/concepts/contradictions" },
      { title: "Updating a source: snapshot and living", href: "/docs/concepts/updating-sources" },
      { title: "The pipeline and readiness", href: "/docs/concepts/pipeline" },
      { title: "What lives where", href: "/docs/concepts/architecture" },
      { title: "Retrieval: operations, search, graph and SQL", href: "/docs/concepts/retrieval" },
      { title: "Reading a result", href: "/docs/concepts/reading-results" },
    ],
  },
  {
    title: "Guides",
    href: "/docs/guides/ingest-files",
    children: [
      { title: "Supported file types", href: "/docs/guides/file-types" },
      { title: "Bring your existing data", href: "/docs/guides/bring-your-data" },
      { title: "Ingest files", href: "/docs/guides/ingest-files" },
      { title: "Ingest conversations and transcripts", href: "/docs/guides/ingest-conversations" },
      { title: "Keep a source up to date", href: "/docs/guides/keep-sources-current" },
      { title: "Wait until a document is queryable", href: "/docs/guides/wait-for-readiness" },
      { title: "Give an agent context", href: "/docs/guides/agent-context" },
      { title: "Ask about the past", href: "/docs/guides/ask-about-the-past" },
      { title: "Cite the source of an answer", href: "/docs/guides/cite-sources" },
      { title: "Handle unknowns and ambiguity", href: "/docs/guides/unknowns-and-ambiguity" },
      { title: "Explore memory with SQL", href: "/docs/guides/sql" },
      { title: "Saved queries", href: "/docs/guides/saved-queries" },
      { title: "Build a memory-backed agent", href: "/docs/guides/build-an-agent" },
    ],
  },
  ...(process.env.DOCS_CLOUD === "1"
    ? [
      {
        title: "remember.dev",
        href: "/docs/cloud/overview",
        children: [
          { title: "What remember.dev runs for you", href: "/docs/cloud/overview" },
          { title: "Organisations, projects and members", href: "/docs/cloud/organisations-and-projects" },
          { title: "Tokens and sign-in", href: "/docs/cloud/tokens-and-sign-in" },
          { title: "Hosted MCP", href: "/docs/cloud/hosted-mcp" },
          { title: "Pricing and credits", href: "/docs/cloud/pricing" },
          { title: "Spend caps and auto top-up", href: "/docs/cloud/spend-controls" },
          { title: "Limits", href: "/docs/cloud/limits" },
          { title: "Files and mounts", href: "/docs/cloud/files-and-mounts" },
          { title: "What remember.dev serves", href: "/docs/cloud/compatibility" },
          { title: "Data handling and security", href: "/docs/cloud/data-and-security" },
          { title: "Leaving", href: "/docs/cloud/leaving" },
          { title: "Support", href: "/docs/cloud/support" },
        ],
      },
      ]
    : []),
  {
    title: "Self-hosting",
    href: "/docs/self-hosting/requirements",
    children: [
      { title: "Requirements", href: "/docs/self-hosting/requirements" },
      { title: "Install with Docker Compose", href: "/docs/self-hosting/install" },
      { title: "Configuration", href: "/docs/self-hosting/configuration" },
      { title: "Models and providers", href: "/docs/self-hosting/models" },
      { title: "File formats and converters", href: "/docs/self-hosting/converters" },
      { title: "Authentication and scopes", href: "/docs/self-hosting/authentication" },
      { title: "Scaling", href: "/docs/self-hosting/scaling" },
      { title: "Operating the pipeline", href: "/docs/self-hosting/operating" },
      { title: "Troubleshooting", href: "/docs/self-hosting/troubleshooting" },
      { title: "Observability", href: "/docs/self-hosting/observability" },
      { title: "Upgrades and migrations", href: "/docs/self-hosting/upgrades" },
      { title: "Filesystem views", href: "/docs/self-hosting/filesystem-views" },
    ],
  },
  {
    title: "Reference",
    href: "/docs/reference/http-api",
    children: [
      { title: "HTTP API conventions", href: "/docs/reference/http-api" },
      { title: "Ingest, readiness and documents", href: "/docs/reference/http-api/ingest" },
      { title: "Assured operation routes", href: "/docs/reference/http-api/operations" },
      { title: "Entities and facts", href: "/docs/reference/http-api/entities-and-facts" },
      { title: "Search and adjacent chunks", href: "/docs/reference/http-api/search" },
      { title: "Graph", href: "/docs/reference/http-api/graph" },
      { title: "SQL queries", href: "/docs/reference/http-api/query" },
      { title: "Deployment info and health", href: "/docs/reference/http-api/deployment" },
      { title: "Assured operations", href: "/docs/reference/assured-operations" },
      { title: "Result types", href: "/docs/reference/result-types" },
      { title: "Query space memory_v1", href: "/docs/reference/query-space" },
      { title: "Python SDK", href: "/docs/reference/python-sdk" },
      { title: "CLI", href: "/docs/reference/cli" },
      { title: "MCP tools", href: "/docs/reference/mcp" },
      { title: "Configuration variables", href: "/docs/reference/configuration" },
      { title: "Errors and status codes", href: "/docs/reference/errors" },
      ...(process.env.DOCS_CLOUD === "1"
        ? [{ title: "remember.dev API", href: "/docs/reference/cloud-api" }]
        : []),
    ],
  },
  {
    title: "Project",
    href: "/docs/project/benchmarks",
    children: [
      { title: "Benchmarks and how we measure", href: "/docs/project/benchmarks" },
      { title: "Releases and changelog", href: "/docs/project/changelog" },
      { title: "What is not built yet", href: "/docs/project/not-built-yet" },
      { title: "Glossary", href: "/docs/project/glossary" },
      { title: "Contributing, license and trademarks", href: "/docs/project/contributing" },
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
