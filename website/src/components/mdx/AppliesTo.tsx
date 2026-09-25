import { docsCloud } from "@/lib/docs/cloud";

/**
 * Badge under a page title saying whether the page applies to remember.dev,
 * self-hosted, or both. The public build documents only the self-hosted
 * product, so there the badge says nothing and is not rendered.
 */
export function AppliesTo({ products }: { products: string[] }) {
  if (!docsCloud) return null;
  const label = (product: string) =>
    product === "self-hosted" ? "Self-hosted" : product;
  return (
    <p className="not-prose -mt-2 mb-6 flex flex-wrap items-center gap-2 text-xs">
      <span className="text-muted-foreground">Applies to</span>
      {products.map((product) => (
        <span
          key={product}
          className={
            product === "remember.dev"
              ? "rounded-full border border-teal/40 bg-teal-soft px-2.5 py-0.5 font-medium text-teal"
              : "rounded-full border border-coral/40 bg-coral-soft px-2.5 py-0.5 font-medium text-coral"
          }
        >
          {label(product)}
        </span>
      ))}
    </p>
  );
}
