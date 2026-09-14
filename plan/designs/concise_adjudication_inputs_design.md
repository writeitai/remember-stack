# Concise evidence presentation for ordinary adjudication

**Status:** D121, accepted 2026-09-14; binding when merged.
**Analysis:** [lean processing evidence](../analysis/lean_processing_contracts.md).
**Related:** [D120 prompt clarity](processing_prompt_clarity_design.md).

## Decision and boundary

The database prepares a complete validation snapshot so a decision can be checked
after inference. The model needs its semantic evidence, not database bookkeeping.
Derive a deterministic, compact presentation from the same snapshot. Do not use
another model to summarize it. Preserve D118 locking, fingerprinting, compare-and-
swap publication, revalidation, atomic application and source-erasure inventory.

For this projection change, candidate and witness membership are unchanged.
D123 nomination is a separately measured change. Input compaction must not hide
evidence by imposing a smaller token cap and silently dropping rows.

## Model-visible information

Present the incoming assertion; each candidate fact's complete meaning and chosen
world window; original source claims and supporting exact passages; source time
distinct from raw world dates; attribution and testimony currency; relevant
support/contradiction links; window-witness links; and original assertions and
their current assignment needed for any permitted support move. Preserve input
limits/truncation disclosures and existing confidence/evidence information that
can affect the decision. Context-only facts are identified as non-editable.

Remove deployment IDs, generation strings, membership hashes, repeated structural
keys and database audit timestamps that provide no semantic authority. Do not
remove reporting dates or source identity: distinct sources with identical words
remain distinct testimony. Keep a documented field mapping from the full snapshot
to the presentation, with each omitted field's administrative purpose.

Factor repeated text into a dictionary referenced by source/claim/assertion rows.
Exact text sharing is storage/presentation deduplication, not evidence identity
deduplication. Preserve each occurrence's source attribution, dates and provenance.
Do not paraphrase or discard qualifications. One source passage can support several
assertions without being copied into each row.

## Short references and response translation

Use deterministic attempt-local names such as F1 (fact), C1 (claim), A1 (assertion)
and E1 (entity). Build bijective typed mappings from the prepared snapshot's stable
ordered rows. New-fact handles occupy a separate namespace. Real deployment IDs
and UUIDs remain internal. These references have no cross-attempt meaning.

Provide a closed model-facing response schema accepting those handles for every
existing operation, including window witnesses, support moves and contradictions.
Translate it through the exact prepared attempt's mapping into the existing
`FactApplicationDecision` and writer. Unknown handles, wrong kinds, ambiguous
names and context-only mutation targets fail validation; never recover by guessing.

The renderer/response-adapter version is included in the adjudicator generation
and prepared-input fingerprint; no new registry is required. Deterministically
rebuild mappings from frozen inputs, or store them as part of the same prepared
payload if necessary; no independently mutable mapping table or second content
cache. A retry of an attempt preserves mappings. A new attempt cannot consume a
reply for an older attempt, even if both contain a name F1. Existing attempt/CAS
and snapshot checks remain authoritative. Publication stores the validated
translated decision under the existing source-existence checks.

The ordinary no-candidate deterministic path continues using internal objects;
it need not round-trip through model handles. Invalid provider references follow
the existing failed-decision retry behavior with no partial writes.

## Security, deletion and operational consequences

The compact view is untrusted evidence inside trusted instructions. It grants no
new database access or operations. Do not expose or log secrets while measuring
inputs. Full prepared inputs and translated decisions remain under existing
forget ownership; the projection must not create an untracked durable copy.
If diagnostic fixtures retain source text, existing deletion and fixture policies
apply. No new data store, model call, lock spanning inference or scheduler.

## Acceptance evidence

- Round-trip every permitted response reference and reject wrong/stale handles.
- Compare semantic field membership before/after projection, including identical
  text in different sources, attributed/withdrawn claims and correction witnesses.
- Exercise support moves, new handles, explicit date clearing, contradictions,
  snapshot changes, source deletion and late provider answers under PostgreSQL.
- Measure complete rendered input plus schema tokens against the full snapshot
  on representative small/large inputs. Label reconstructed fixtures honestly.
- Run D120 assertion-preservation scenarios. Mock outputs prove translation and
  application, not real-model semantic quality.

Roll adjudicator generation/protocol fingerprints where required. Public output
schemas need not change merely because internal prompt handles change. Report
measured reduction without promising a fixed ratio or total conversation price.
The alternative of fewer witnesses loses to correctness; the alternative of
model-generated summaries loses to added cost and possible semantic loss.
