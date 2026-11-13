# Proposed reader evidence-completeness fix

Status: proposed, not binding. The owner requested Opus approval before
implementation. Adoption trigger: approval of this revised reader-only scope.
The initial combined proposal was rejected; no runtime code was implemented.
Rationale: [analysis](../analysis/evidence_fidelity_20261113.md).

## Reader behavior

Teach the ordinary consumption skill and corresponding benchmark reader to:

- Retain the actual requested place/date/entity name rather than reducing it to
  "the lake" or "the event". Account for distinct supported list members, with
  correct owners, times and planned/completed status.
- Inspect claims/source chunks when facts do not supply a necessary detail or
  complete owner/reply. With an adjacent result ordered in document order,
  identify the supplied center ID and continue from the first or last returned
  `chunk_id` only on the side where needed context is still missing. If fewer
  than `window` neighbors exist on that side and no drops/truncation obscure the
  result, that side has reached the document boundary; stop expanding it.
  A missing center, drops or truncation do not establish a document boundary.
- Stop when the requested parts are supported, at a known boundary, or at the
  existing budget. Do not fabricate missing list members, dates or ownership.
  General consumers retain honest negatives; the benchmark retains its existing
  best-supported-answer policy, without inventing facts to satisfy a reference.

Add this adjacency advice to the ordinary skill and bump its revision. In the
benchmark, replace the existing final-check block (distinct evidence, dialogue
adjacency, concrete values and completeness near `protocol.py:426–445`), the
list-completeness paragraph and repeated adjacency reminder with a shorter
consolidated rule set. Preserve the existing needed meanings. Net benchmark
prompt length must not grow; no additional closing checklist.

## Scope, identity and limits

No E2/converter/image identity, model/provider, judge, tool catalog, SQL query,
retrieval allowance or processing-profile changes. Keep the v45 protocol family
name so saved source readiness still resolves; the new prompt automatically
gets a new protocol fingerprint on an independently prepared scoring fork.
Do not overwrite or reprime old runs. Prove retained-store preparation works
with the new reader and leaves its source unchanged. No benchmark run is part
of this PR.

Record the existing extractor-generation/text-origin coupling as a known gap in
`CLAUDE.md` and explain it in analysis. It is excluded from this change; the
calendar candidate is not safe to roll under that coupling. The visual-label
fix is a separate PR and will be measurable independently from this reader
checkpoint.

On approval, reconcile retrieval/consumption design, public querying guidance
and `plan/README.md`, and remove this unchosen proposal. Validate skill revision,
protocol bounds, prompt size and retained-ingestion source immutability. Submit
implementation and results to Opus before marking ready. Mocked checks establish
wiring, not a benchmark improvement or a corrected score.
