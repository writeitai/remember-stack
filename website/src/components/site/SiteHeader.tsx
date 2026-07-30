import Link from "next/link";
import { Github } from "lucide-react";
import { SearchCommand } from "@/components/search/SearchCommand";
import { BrandLockup } from "@/components/site/BrandLockup";

export function SiteHeader() {
  return (
    <header className="sticky top-0 z-40 border-b border-border bg-background/90 shadow-[0_1px_0_rgba(16,31,74,0.02)] backdrop-blur-xl">
      <div className="container mx-auto flex h-16 items-center justify-between gap-4">
        <Link
          href="/"
          aria-label="RememberStack documentation home"
          className="rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4"
        >
          <BrandLockup priority />
        </Link>

        <div className="flex items-center gap-2 sm:gap-4">
          <SearchCommand />
          <Link
            href="/docs"
            className="hidden rounded-sm text-sm font-medium text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4 sm:inline"
          >
            Docs
          </Link>
          <a
            href="https://github.com/writeitai/remember-stack"
            target="_blank"
            rel="noreferrer"
            className="rounded-sm text-muted-foreground outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-4"
            aria-label="RememberStack on GitHub"
          >
            <Github className="h-5 w-5" />
          </a>
        </div>
      </div>
    </header>
  );
}
