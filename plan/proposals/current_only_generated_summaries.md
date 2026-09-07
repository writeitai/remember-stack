# Alternative: current-only generated summaries

**Status:** unchosen alternative to [D118 §6](../designs/mutable_fact_windows_design.md#6-profiles-and-generated-knowledge),
2026-09-07. Not implementation authority.

## When this would win

Adopt only if the product requires a cached profile or generated page to assert
the entity's current state directly, rather than date-qualified historical facts
or an explicitly dated snapshot. Demonstrate a consumer need that an ordinary
current fact query cannot meet, and measure the refresh/read cost against it.

## Required contract

Time alone can change such content: a future appointment activates, or a tenure
expires, without ingestion. Register the next relevant start/end boundary and
use existing scheduled ledger work to invalidate and repair the content. Candidate
selection must include future facts before top-k truncation, or an omitted future
appointment can activate without any known deadline.

A scheduler is insufficient by itself. Readers and publication must reject stale
current content after its deadline, including delayed work, failed work and
restarts. Text, vectors, parent pages and routing that consume that current
meaning must obey the same freshness contract. Mutation and forget invalidation
remain required as well.

This does not automatically justify the old generic D110 certificate, source and
event schema. Choose the smallest concrete storage that provides the necessary
deadline and dependency checks, with complete failure/recovery and deletion proofs.

## Why it was not selected

Entity profiles support identity resolution; historical employers and achievements
are useful inputs. Stable date-qualified selection preserves that information
without a wall-clock dependency. D118 therefore chooses dated content and explicit
fact-time queries. Current-only cached prose would add refresh and stale-read
machinery for a product promise that is not required by that choice.
