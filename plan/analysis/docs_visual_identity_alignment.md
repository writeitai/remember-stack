# OSS documentation visual identity alignment

**Status:** non-binding analysis. **Date:** 2026-07-30.

## Question

How should the repository-owned OSS documentation at `docs.remember.dev` adopt
the visual identity already used by the RememberStack product at
`remember.dev`, without making the docs depend on the managed-cloud runtime or
weakening their accessibility?

The domain cutover made the existing mismatch visible: the OSS site still uses
the inherited WriteIt sand/green palette, Hanken Grotesk, and a placeholder
circle in its header. The product site uses a different, explicit
RememberStack system and the canonical network-graph mark. A reader moving
between the two sites currently sees two brands even though the domain
ownership design says they are two surfaces of one product identity.

## Source evidence

The managed-cloud repository is the current visual source of truth. Snapshot
`writeitai/ultimate-memory-cloud@d492d204e1ecae874234fc2879296679e73b8780`
contains:

- `fe/src/app/globals.css`: the production color tokens and font-family
  mappings.
- `fe/src/app/layout.tsx`: Inter for body copy and Space Grotesk for display
  text, loaded through `next/font`.
- `fe/public/brand/README.md`: the asset and lockup contract. It names the
  color network graph as canonical, marks the dual-ring variant as legacy,
  specifies a graph mark plus `RememberStack` plus a coral rule and domain
  subline, and prohibits a `CLOUD` tag in OSS public chrome.
- `fe/public/brand/mark.svg`: the canonical coral-and-teal graph vector.
- `fe/src/components/brand/BrandLockup.tsx`: the implemented spacing,
  hierarchy, and typography for the public lockup.

These are pinned source paths rather than an inference from a screenshot.
Source:
[`writeitai/ultimate-memory-cloud` at `d492d20`](https://github.com/writeitai/ultimate-memory-cloud/tree/d492d204e1ecae874234fc2879296679e73b8780/fe),
retrieved 2026-07-30.

The resulting brand contract is:

| Role | Value |
| --- | --- |
| Ink / primary | `#101f4a` |
| Secondary ink | `#5b6684` |
| Tertiary ink | `#8e97ae` |
| Coral / coral soft | `#ee5b44` / `#fdeeeb` |
| Teal / teal soft | `#3e9b8e` / `#ecf6f4` |
| Cream | `#f5ebd0` |
| Line / line soft | `#e7eaf1` / `#f1f3f8` |
| Surface | `#fafbfd` |
| Navy range | `#0a1230`, `#101c42`, `#18244f`, `#25335f` |
| Body / display fonts | Inter / Space Grotesk |
| Canonical public mark | Color network-graph SVG |

Coral and teal each have less than 4.5:1 contrast against the white/surface
background. They are safe for decoration, large text, rules, focus indicators,
and underlines, but not for ordinary body-sized text. Ink (`#101f4a`) and
secondary ink (`#5b6684`) exceed the WCAG AA normal-text threshold on the
surface. The docs theme should preserve the product colors while keeping link
text and navigation labels in those readable inks.

## Alternatives

### A. Keep the inherited WriteIt theme

This is the smallest code change, but it leaves a stale brand after the domain
cutover and continues to present a generic placeholder instead of the actual
logo. It is rejected because the two canonical surfaces would visibly disagree
about product identity.

### B. Load the product site's CSS, fonts, and logo at runtime

This would avoid local duplication but would add the managed-cloud origin to
the OSS documentation's availability and content-security boundary. A cloud
deploy, asset rename, or cross-origin policy change could break the
repository-owned static site. It is rejected because D66 requires a
self-contained Pages artifact.

### C. Copy the versioned brand contract and canonical vector into this site

The docs keep their repository-local build and hosting while using the same
explicit tokens, fonts, and canonical mark as the product. The copied asset
retains source provenance in the binding design and site README. Future brand
changes require an intentional same-PR sync rather than silently changing a
deployed OSS artifact.

This is recommended. It duplicates a small, stable presentation contract, not
runtime logic or content authority.

## Implementation and operational consequences

- `website/src/app/globals.css` owns a local mapping of the complete production
  palette onto the existing docs semantic tokens. Body text remains ink;
  coral and teal remain branded accents.
- `website/src/app/layout.tsx` loads Inter and Space Grotesk with `next/font`.
  Next emits the font files with the static export, so browsers do not depend
  on Google Fonts at runtime.
- `website/public/brand/mark.svg` is copied byte-for-byte from the pinned
  managed-cloud source. A small local lockup component combines it with the
  wordmark and `docs.remember.dev` subline. The header and homepage use the
  canonical graph mark; no legacy ring or `CLOUD` tag is introduced.
- The existing static-export, Pagefind, navigation, and content contracts do
  not change. There is no new request-time dependency and no DNS or hosting
  change.
- Build validation must cover static export, type checking, Pagefind output,
  internal-link checks, and visual inspection at desktop and mobile widths.
  The mark must retain its aspect ratio, the header must not overflow, focus
  states must remain visible, and body-sized text must retain AA contrast.

Failure is visible as a missing logo request, fallback system fonts, unreadable
navigation state, mobile header overflow, or a static-export/build failure.
Recovery is a normal revert of the presentation commit: no user data, DNS,
content model, or control-plane state is involved. If the product brand changes
again, update the copied token/asset provenance and this analysis before
changing the binding visual contract.
