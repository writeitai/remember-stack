"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Command } from "cmdk";
import { FileText, Search } from "lucide-react";
import { searchDocs, type SearchOutcome } from "@/lib/search/pagefind";

const EMPTY: SearchOutcome = { status: "empty", results: [] };

export function SearchCommand() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [outcome, setOutcome] = useState<SearchOutcome>(EMPTY);
  const [loading, setLoading] = useState(false);
  const [shortcut, setShortcut] = useState("⌘K");
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Monotonic id so an older, slower search can't overwrite a newer one.
  const requestIdRef = useRef(0);

  // Show the platform-correct shortcut hint after mount (avoids hydration drift).
  useEffect(() => {
    const isMac =
      typeof navigator !== "undefined" &&
      /Mac|iPhone|iPad|iPod/.test(navigator.platform || navigator.userAgent);
    if (!isMac) setShortcut("Ctrl K");
  }, []);

  // Cmd/Ctrl+K toggles the palette from anywhere on the site.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key.toLowerCase() === "k" && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Debounced Pagefind query, race-safe against out-of-order resolutions.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    const q = query.trim();
    if (!q) {
      setOutcome(EMPTY);
      setLoading(false);
      return;
    }
    const requestId = ++requestIdRef.current;
    setLoading(true);
    debounceRef.current = setTimeout(() => {
      searchDocs(q).then((res) => {
        // Ignore if a newer query superseded this one.
        if (requestId !== requestIdRef.current) return;
        setOutcome(res);
        setLoading(false);
      });
    }, 150);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [query]);

  // Reset transient state whenever the dialog closes; invalidate in-flight work.
  useEffect(() => {
    if (!open) {
      requestIdRef.current++;
      setQuery("");
      setOutcome(EMPTY);
      setLoading(false);
    }
  }, [open]);

  const onSelect = useCallback(
    (url: string) => {
      setOpen(false);
      router.push(url);
    },
    [router]
  );

  const { status, results } = outcome;

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-sm text-muted-foreground outline-none transition-colors hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4"
        aria-label="Search documentation"
      >
        <Search className="h-4 w-4 text-coral" />
        <span className="hidden sm:inline">Search docs</span>
        <kbd className="ml-1 hidden rounded border border-border bg-secondary px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground sm:inline">
          {shortcut}
        </kbd>
      </button>

      <Command.Dialog
        open={open}
        onOpenChange={setOpen}
        label="Search documentation"
        shouldFilter={false}
        loop
        overlayClassName="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm"
        contentClassName="fixed left-1/2 top-[12%] z-50 w-[calc(100%-2rem)] max-w-xl -translate-x-1/2"
        className="overflow-hidden rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-border px-4">
          <Search className="h-4 w-4 shrink-0 text-muted-foreground" />
          <Command.Input
            value={query}
            onValueChange={setQuery}
            placeholder="Search the docs..."
            className="h-12 w-full bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          />
        </div>
        <Command.List className="max-h-[60vh] overflow-y-auto p-2">
          {loading && (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              Searching...
            </div>
          )}
          {!loading && status === "empty" && (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              Type to search the documentation.
            </div>
          )}
          {!loading && status === "ok" && results.length === 0 && (
            <Command.Empty className="px-3 py-6 text-center text-sm text-muted-foreground">
              No results found.
            </Command.Empty>
          )}
          {!loading && status === "unavailable" && (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              Search isn&apos;t available yet. It works on the deployed site.
            </div>
          )}
          {!loading && status === "error" && (
            <div className="px-3 py-6 text-center text-sm text-muted-foreground">
              Something went wrong. Try a different search.
            </div>
          )}
          {results.map((result) => (
            <Command.Item
              key={result.url}
              value={result.url}
              onSelect={() => onSelect(result.url)}
              className="flex cursor-pointer flex-col gap-1 rounded-md px-3 py-2 text-sm data-[selected=true]:bg-brand-soft data-[selected=true]:text-accent-foreground"
            >
              <span className="flex items-center gap-2 font-medium">
                <FileText className="h-3.5 w-3.5 shrink-0 text-teal" />
                {result.title}
              </span>
              <span
                className="line-clamp-2 text-xs text-muted-foreground"
                dangerouslySetInnerHTML={{ __html: result.excerpt }}
              />
            </Command.Item>
          ))}
        </Command.List>
        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-2 text-[11px] text-muted-foreground">
          <span>
            <kbd className="rounded border border-border bg-secondary px-1 font-mono">
              ↵
            </kbd>{" "}
            to open
          </span>
          <span>
            <kbd className="rounded border border-border bg-secondary px-1 font-mono">
              esc
            </kbd>{" "}
            to close
          </span>
        </div>
      </Command.Dialog>
    </>
  );
}
