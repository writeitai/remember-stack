import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
import { docsCloud } from "@/lib/docs/cloud";
import { TabsClient } from "@/components/mdx/TabsClient";

// `cloud` marks a hosted-service tab, dropped from the public build.
type TabProps = { label: string; cloud?: boolean; children: ReactNode };

/** One labelled panel inside <Tabs>. */
export function Tab({ children }: TabProps) {
  return <>{children}</>;
}

/**
 * Content tabs (remember.dev first, then Self-hosted). Cloud tabs are dropped
 * here, on the server, so the public build never ships their content. A group
 * left with one tab renders as plain content.
 */
export function Tabs({ children }: { children: ReactNode }) {
  const tabs = Children.toArray(children).filter(
    (child): child is ReactElement<TabProps> =>
      isValidElement<TabProps>(child) && (docsCloud || !child.props.cloud),
  );
  if (tabs.length === 1) return <>{tabs[0].props.children}</>;
  return (
    <TabsClient
      tabs={tabs.map((tab) => ({ label: tab.props.label, content: tab.props.children }))}
    />
  );
}
