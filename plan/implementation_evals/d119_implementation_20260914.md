# D119 implementation and verification

Date: 2026-09-14. PR: https://github.com/writeitai/remember-stack/pull/400.
Binding scope: [multi-span extraction and version reuse](../designs/multi_span_claim_extraction_design.md).
Runtime reviewed and tested at `3cdf874640b7a1d25ac70178b1bcd0e134c60c8d`;
the following checkpoint only records this evidence and corrects LoCoMo names
to Full-v29 (the extractor and schema generations already changed).

## Result

One coherent claim can cite several supplied source passages. The engine maps
labels to exact character ranges and requires a target passage accepted by
Selection as the origin. Additional passages can provide the rest of the
evidence; summaries cannot. Complete spans live on each source occurrence,
while the immutable claim keeps its original anchor. API evidence coordinates
identify that original representation; the SQL occurrence view exposes the
current version's positions.

Content-identical extraction inputs reuse claim IDs and remap every span
through the matching target/previous/next windows. Missing sides and side
identity are part of the input hash. Changing required context invalidates
reuse. No substring-first-match re-anchoring is used. Existing provenance and
forget paths consume the complete list. The migration refuses populated claim
stores; it does not convert or erase them.

## Verification

The final sequential PostgreSQL run passed **8 tests in 609.85 seconds** on an
isolated disposable database. It covered the actual E0–E3 worker lifecycle
across **500 versions**, origin-coordinate API hydration, the SQL span CHECK,
actual Alembic populated-store refusal, the live manifest, and public query
prose. The 500-version source says “Alice Novak is an engineer at Acme” and,
in a separate passage, “She joined Acme in 2024.” Both passages are required
to ground the normalized claim. All 500 occurrences contain both correctly
remapped ranges, retain one claim identity, and a real lifecycle recount
keeps one source lineage and fact evidence count 1. Editing the source context
then causes extraction instead of reuse. Each acceptance test starts with a
fresh schema; truncating deployments alone was insufficient isolation.

Command (local test database supplied through REMEMBERSTACK_DATABASE_URL):

```bash
uv run pytest src/tests/workers/test_d119_versions.py \
  src/tests/spine/test_d119_multi_span.py \
  src/tests/spine/test_d119_migration.py \
  src/tests/spine/test_query_space_batch_a.py::test_live_introspection_equals_the_checked_in_manifest \
  src/tests/surfaces/test_open_query_batch_f.py::test_core_prose_is_authority_for_live_graph_and_claims_verbatim \
  -q --tb=short
```

Independent final reviews: [Antigravity](d119_agy_review_3cdf8746.md) and
[Cursor](d119_cursor_review_3cdf8746.md), both approve with no material findings.
Their reports distinguish independently run checks from parent test evidence.
Parent also inspected the final changes and fixed the origin-coordinate
mismatch, SQL NULL/privilege handling, version test isolation, generated
contract pins and Compose upgrade revision assertions before those reviews.

## Limits and minor review observations

These use deterministic providers and synthetic documents. They prove version
reuse and storage/provenance behavior, not real-model semantic quality or
billed-token savings. No paid processing or remote benchmark store was used.
Exact source ranges establish provenance; they do not prove entailment.

Cursor identified no blocker. Its minor notes are retained in the report:
internal direct record insertion retains an origin-only default when no
span list is supplied (production Claimify supplies the full list); E1/E2
use different section identifiers but fail reuse safely on disagreement;
old unused substring helpers remain; the SQL shape helper tolerates extra
keys. These do not warrant a new mechanism or an expanded refactor here.
The stale PR description is replaced at final publication.

The final Full-v29 metadata changes passed 151 protocol, runner and store-backup
tests. CI on the reviewed runtime passed all lanes, including workers,
surfaces, Compose quickstart and the client compatibility matrix. The metadata
checkpoint receives its own CI run before merge.

The Full-v29 adapter names isolate D119 results from the old Full-v28 run.
D120/D121 is a separate Full-v30 contract; D122/D123 will have its own generation.
Provider bindings are unchanged. Broader processing cost/quality remains to be
measured on explicitly configured models after all processing changes land.
