# Documentation domain ownership reconciliation

**Status:** non-binding analysis. **Date:** 2026-07-30.

## Question

Where should the repository-owned RememberStack documentation site be published
now that `remember.dev` also hosts the managed cloud product?

This is not merely a DNS question. The answer determines which repository owns
the apex, whether the cloud application can keep its same-origin routes, who is
authoritative for OSS documentation, and whether readers see one coherent
RememberStack product or two competing sites.

## Current evidence

- The managed-cloud binding design allocates the `remember.dev` apex to the
  product site, cloud documentation, blog, and authenticated application, while
  reserving `docs.remember.dev` for the OSS repository's own GitHub Pages site:
  `/Users/jpuc/code/moje/ultimate_memory_cloud_9/ultimate-memory-cloud/design/designs/public-web-site-docs-blog.md`
  §3.2 and that repository's D14.
- Production inventory in
  `/Users/jpuc/code/moje/ultimate_memory_cloud_9/ultimate-memory-cloud/infra/prod.yaml`
  records the apex as a live Cloudflare Pages site and
  `docs.remember.dev` as the reserved OSS hostname.
- GitHub's Pages API reported on 2026-07-30 that this repository still used
  `ultimate-memory.writeit.ai` as its custom domain with an approved
  certificate. The latest main-branch documentation deployment was successful.
- Live DNS on 2026-07-30 resolved `remember.dev` through Cloudflare,
  `ultimate-memory.writeit.ai` to `writeitai.github.io`, and
  `docs.remember.dev` to the deliberately invalid placeholder
  `docs-placeholder.remember.dev.invalid`.
- This repository's D66 amendment and D76 currently claim the apex for the OSS
  site. That conflicts with both the managed-cloud binding design and the
  as-built production topology. Neither repository can be trusted cold while
  both claims remain binding.

GitHub requires a custom subdomain to be bound in the repository's Pages
settings and then represented by a DNS `CNAME` that points directly to the
owner's GitHub Pages domain, without the repository name. GitHub also recommends
verifying the parent domain and configuring the Pages custom domain before DNS
to reduce takeover risk. Source:
[Managing a custom domain for your GitHub Pages site](https://docs.github.com/en/pages/configuring-a-custom-domain-for-your-github-pages-site/managing-a-custom-domain-for-your-github-pages-site),
retrieved 2026-07-30.

Cloudflare's DNS-only mode returns the configured origin target and does not
route HTTP traffic through Cloudflare's reverse proxy. That preserves the
repository-owned GitHub Pages trust boundary. Source:
[Cloudflare DNS proxy status](https://developers.cloudflare.com/dns/proxy-status/),
retrieved 2026-07-30.

## Alternatives

### A. `docs.remember.dev` on this repository's GitHub Pages

The apex remains the product and managed-cloud home. The OSS documentation site
keeps its own build, deployment workflow, content authority, search index, and
GitHub Pages hosting. Cloudflare contributes only authoritative DNS: a
DNS-only `CNAME` from `docs.remember.dev` to `writeitai.github.io`.

This is the smallest change that matches production reality. It keeps cloud
routes such as `/app` and `/app/api` intact, avoids copying OSS documentation
into a private cloud project, and gives the OSS site a RememberStack-branded
canonical URL.

Cost: the product has two hostnames. Navigation and metadata must clearly label
the apex as the product/cloud home and the subdomain as technical OSS
documentation.

### B. Replace the `remember.dev` apex with the OSS GitHub Pages site

This would make D66's existing text literally true, but it would displace the
live cloud Pages project and its same-origin application/API surface. The cloud
site would need a new hostname, followed by cookie, OAuth, Stripe, email,
canonical-metadata, and public-link migration.

This is rejected because the requested outcome is a documentation-domain
cutover, not a managed-product origin migration. It creates broad availability
and identity risk without improving OSS correctness or ownership.

### C. Publish OSS documentation under `remember.dev/docs`

The cloud Pages project already owns that path for managed-offering
documentation. Serving a second repository there would require copying the OSS
artifact into the cloud project or adding a path proxy. Copying creates two
authorities and can drift; proxying makes the private cloud edge part of the OSS
documentation availability path.

This is rejected because it collides with cloud documentation and violates
D66's repository-local hosting boundary.

## Recommendation

Select alternative A:

- `https://remember.dev` is the canonical RememberStack product and managed
  cloud home.
- `https://docs.remember.dev` is the canonical technical documentation home for
  the open-source engine.
- Cloud `/docs/**` remains documentation for the managed offering only.
- The OSS repository remains the sole content and deployment authority for OSS
  documentation; the cloud zone owns only the DNS record.

This is one product identity with two clearly scoped surfaces, not separate OSS
and cloud brands.

## Cutover, failure, and recovery

The safe order is:

1. Update this repository's design, metadata, and committed `CNAME`.
2. Verify `remember.dev` for the `writeitai` GitHub organization.
3. Bind `docs.remember.dev` in this repository's Pages settings before
   publishing the DNS record.
4. Replace the invalid placeholder with a DNS-only `CNAME` to
   `writeitai.github.io`.
5. Wait for GitHub's certificate, enable HTTPS enforcement, and smoke the
   homepage, documentation routes, `_next` assets, and Pagefind search.
6. Update cloud navigation only after the new hostname resolves.
7. Serve permanent path-preserving redirects from
   `ultimate-memory.writeit.ai` when a separate HTTPS redirect origin is
   available.

Failure is visible as unresolved DNS, a Pages domain-verification error, a
pending certificate, missing static assets, or search-index failure. Do not
paper over those states by proxying through the managed cloud project. Rollback
restores `ultimate-memory.writeit.ai` as the Pages custom domain and restores the
invalid `docs.remember.dev` placeholder; the cloud apex remains untouched
throughout.

Security consequences:

- Keep the GitHub domain-verification TXT record.
- Do not use wildcard DNS.
- Bind the hostname in GitHub before DNS.
- Keep `docs.remember.dev` DNS-only so GitHub, not the managed cloud project,
  terminates and serves the OSS site.
