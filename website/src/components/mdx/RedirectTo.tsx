/** Static-export redirect for a retired docs route: meta refresh plus a visible link. */
export function RedirectTo({ href, title }: { href: string; title: string }) {
  return (
    <>
      <meta httpEquiv="refresh" content={`0; url=${href}`} />
      <link rel="canonical" href={`https://remember.dev${href}`} />
      <p>
        This page moved to <a href={href}>{title}</a>.
      </p>
    </>
  );
}
