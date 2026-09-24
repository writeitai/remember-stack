"use client";

import { useState, type ReactNode } from "react";

export type TabPanel = { label: string; content: ReactNode };

/** The interactive tab strip behind <Tabs>. All panels stay in the page for search. */
export function TabsClient({ tabs }: { tabs: TabPanel[] }) {
  const [active, setActive] = useState(0);
  return (
    <div className="my-6 rounded-lg border border-border">
      <div role="tablist" className="not-prose flex gap-1 border-b border-border px-2 pt-2">
        {tabs.map((tab, index) => (
          <button
            key={tab.label}
            type="button"
            role="tab"
            aria-selected={index === active}
            onClick={() => setActive(index)}
            className={
              index === active
                ? "rounded-t-md border-b-2 border-brand px-3 py-1.5 text-sm font-medium text-foreground"
                : "rounded-t-md px-3 py-1.5 text-sm text-muted-foreground hover:text-foreground"
            }
          >
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab, index) => (
        <div key={tab.label} role="tabpanel" hidden={index !== active} className="px-5 [&>*:first-child]:mt-4">
          {tab.content}
        </div>
      ))}
    </div>
  );
}
