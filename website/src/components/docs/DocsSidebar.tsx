"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/utils";
import { docsNavigation, type NavItem } from "@/lib/docs/navigation";

function isActive(pathname: string, href: string) {
  const norm = (p: string) => (p.length > 1 ? p.replace(/\/$/, "") : p);
  return norm(pathname) === norm(href);
}

function NavLink({ item }: { item: NavItem }) {
  const pathname = usePathname();
  const active = isActive(pathname, item.href);

  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "block rounded-md px-3 py-1.5 text-sm transition-colors",
        active
          ? "bg-brand-soft font-medium text-foreground shadow-[inset_3px_0_0_var(--brand)]"
          : "text-muted-foreground hover:bg-muted hover:text-foreground"
      )}
    >
      {item.title}
    </Link>
  );
}

function NavSection({ item }: { item: NavItem }) {
  return (
    <div>
      <p className="font-display px-3 py-1.5 text-sm font-semibold text-foreground">
        {item.title}
      </p>
      {item.children && (
        <div className="ml-3 space-y-0.5 border-l border-border pl-3">
          {item.children.map((child) => (
            <NavLink key={child.href} item={child} />
          ))}
        </div>
      )}
    </div>
  );
}

export function DocsSidebar() {
  return (
    <nav className="space-y-1" aria-label="Documentation">
      {docsNavigation.map((item) =>
        item.children ? (
          <NavSection key={item.href} item={item} />
        ) : (
          <NavLink key={item.href} item={item} />
        )
      )}
    </nav>
  );
}
