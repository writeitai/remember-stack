// Thin client-side wrapper around Pagefind.
//
// Pagefind indexes the static export at build time (see `postbuild` in
// package.json) and writes a small JS API to `/pagefind/pagefind.js`. That file
// only exists in the deployed `out/` build — not during `next dev` — so we load
// it lazily and indirectly, keeping the bundler from trying to resolve it.

export type PagefindResult = {
  url: string;
  title: string;
  excerpt: string;
};

/** Outcome of a search, so the UI can distinguish "no hits" from "unavailable". */
export type SearchOutcome = {
  status: "empty" | "ok" | "unavailable" | "error";
  results: PagefindResult[];
};

type PagefindDocument = {
  url: string;
  meta?: { title?: string };
  excerpt: string;
};

export function sanitizeDocUrl(rawUrl: string): string {
  let url = (rawUrl || "").trim();
  // Strip duplicate /docs/docs/ prefix if Pagefind baseUrl combined with an already prefixed route
  while (url.startsWith("/docs/docs/")) {
    url = url.slice(5);
  }
  return url;
}

type PagefindApi = {
  options?: (opts: { baseUrl?: string }) => Promise<void>;
  init?: () => Promise<void>;
  search: (query: string) => Promise<{
    results: Array<{ id: string; data: () => Promise<PagefindDocument> }>;
  }>;
};

// `new Function` keeps webpack/Turbopack from statically analysing the import.
const dynamicImport = new Function(
  "url",
  "return import(url)"
) as (url: string) => Promise<PagefindApi>;

let pagefindPromise: Promise<PagefindApi | null> | null = null;

function importPagefind(): Promise<PagefindApi | null> {
  // Try canonical /docs/pagefind/pagefind.js first to prevent collisions with the
  // root application, falling back to /pagefind/pagefind.js for standalone previews.
  const docsUrl = new URL("/docs/pagefind/pagefind.js", window.location.href).href;
  return dynamicImport(docsUrl)
    .catch(() => {
      const rootUrl = new URL("/pagefind/pagefind.js", window.location.href).href;
      return dynamicImport(rootUrl);
    })
    .then(async (mod) => {
      if (mod.options) {
        await mod.options({ baseUrl: "/" });
      }
      if (mod.init) await mod.init();
      return mod;
    })
    .catch(() => {
      // Don't cache the failure permanently — allow a later retry (e.g. the
      // index finished deploying, or a transient network error).
      pagefindPromise = null;
      return null;
    });
}

/** Load (once) the Pagefind API, or null if the index is currently unavailable. */
export function loadPagefind(): Promise<PagefindApi | null> {
  if (!pagefindPromise) {
    pagefindPromise = importPagefind();
  }
  return pagefindPromise;
}

/** Run a search. Never rejects — failures are reported via `status`. */
export async function searchDocs(query: string): Promise<SearchOutcome> {
  if (!query.trim()) return { status: "empty", results: [] };

  const pagefind = await loadPagefind();
  if (!pagefind) return { status: "unavailable", results: [] };

  try {
    const search = await pagefind.search(query);
    const top = search.results.slice(0, 8);
    const docs = await Promise.all(top.map((r) => r.data()));
    const results = docs.map((d) => ({
      url: sanitizeDocUrl(d.url),
      title: d.meta?.title ?? d.url,
      excerpt: d.excerpt,
    }));
    return { status: "ok", results };
  } catch {
    return { status: "error", results: [] };
  }
}
