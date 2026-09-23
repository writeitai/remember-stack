"use client";

import { Children, isValidElement, useState, type ReactElement, type ReactNode } from "react";

type TabProps = { label: string; children: ReactNode };

/** One labelled panel inside <Tabs>. */
export function Tab({ children }: TabProps) {
  return <>{children}</>;
}

/** Content tabs (remember.dev first, then Self-hosted). All panels stay in the page for search. */
export function Tabs({ children }: { children: ReactNode }) {
  const tabs = Children.toArray(children).filter(
    (child): child is ReactElement<TabProps> => isValidElement(child),
  );
  const [active, setActive] = useState(0);
  return (
    <div className="my-6 rounded-lg border border-border">
      <div role="tablist" className="not-prose flex gap-1 border-b border-border px-2 pt-2">
        {tabs.map((tab, index) => (
          <button
            key={tab.props.label}
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
            {tab.props.label}
          </button>
        ))}
      </div>
      {tabs.map((tab, index) => (
        <div key={tab.props.label} role="tabpanel" hidden={index !== active} className="px-5 [&>*:first-child]:mt-4">
          {tab.props.children}
        </div>
      ))}
    </div>
  );
}
