/** Badge under a page title saying whether the page applies to remember.dev, self-hosted, or both. */
export function AppliesTo({ products }: { products: string[] }) {
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
