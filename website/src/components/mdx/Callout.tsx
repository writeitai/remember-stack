import type { ReactNode } from "react";

/** A note or warning box inside a docs page. */
export function Callout({
  type = "note",
  title,
  children,
}: {
  type?: "note" | "warning";
  title?: string;
  children: ReactNode;
}) {
  const warning = type === "warning";
  return (
    <div
      role={warning ? "alert" : "note"}
      className={
        warning
          ? "my-6 rounded-lg border border-coral/40 bg-coral-soft px-5 py-1 [&>p]:my-3"
          : "my-6 rounded-lg border border-teal/40 bg-teal-soft px-5 py-1 [&>p]:my-3"
      }
    >
      <p className={warning ? "font-semibold text-coral" : "font-semibold text-teal"}>
        {title ?? (warning ? "Warning" : "Note")}
      </p>
      {children}
    </div>
  );
}
