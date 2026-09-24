import type { ReactNode } from "react";
import { docsCloud } from "@/lib/docs/cloud";

/** Hosted-service (remember.dev) content: rendered only in the cloud build. */
export function Cloud({ children }: { children: ReactNode }) {
  return docsCloud ? <>{children}</> : null;
}
