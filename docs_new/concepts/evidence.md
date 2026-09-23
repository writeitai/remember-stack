---
title: Evidence and provenance
description: How every fact and claim in RememberStack points back to exact characters in a specific version of a source, and how to follow that path.
applies_to: [remember.dev, self-hosted]
---

# Evidence and provenance

An agent that cannot show where a statement came from cannot be checked,
and an agent that cannot be checked will not be trusted with real work. "The
cutover is on June 8" is useful. "The cutover is on June 8, per Ravi in the
2026-04-28 standup, characters 1,204 to 1,262 of that transcript" is
something a person can verify in ten seconds.

In RememberStack every fact is backed by claims, and every claim is backed by
exact character spans in one immutable version of one document. You can
walk from any answer down to the text that produced it, and see why the
memory decided what it did.

## Evidence spans

A [claim](claims.md) points to its source in two ways:

- **The origin span**: `source_span`, with `char_start` and `char_end`. This
  is the passage in the target chunk where the extractor found the
  statement. It never changes.
- **All supporting spans**: `evidence_spans`, a list of `{char_start,
  char_end}` ranges. A coherent statement is often supported by several
  sentences, sometimes in different passages ("Ravi owns the schema change"
  in one paragraph, "it ships in June" two paragraphs later). The list
  holds every range the claim relies on, origin first.

Spans are half-open (`char_end` is one past the last character) and
always lie within one version of the document, in one conversion of it.
They are character positions in the version's converted text,
`document.md`: the Markdown RememberStack produced from your file. For
Markdown and plain text this is close to your original; for PDFs, office
files and images it is the converted reading of them. RememberStack does not ingest audio yet.

The extractor does not write these positions itself. The engine labels the
source passages it shows the model; the model cites labels; the engine
turns the labels into positions and checks them. A claim cannot point at
text that is not there.

When a new version of a document keeps a passage unchanged, the claim keeps
its `claim_id` and its spans are mapped onto the new version. The `memory_v1`
view `claim_occurrences_live` lists where each current claim appears, with
`evidence_spans` in that version's text.

## The IDs along the path

| ID | Identifies |
|---|---|
| `fact_id` | A relation or observation. |
| `claim_id` | One claim. |
| `chunk_id` | The passage (a run of whole blocks of the converted text) the claim came from. |
| `doc_id` | The document (all versions). |
| `version_id` | One snapshot of the document's bytes. |
| `representation_id` | One conversion of that version into text. |

A claim result carries `claim_id`, `doc_id` and `chunk_id`. A passage result
(`ChunkEvidenceResult`) also carries `version_id`, `representation_id`, its
own `char_start`/`char_end`, its section's role and the version's
`source_modified_at`.

## From a fact to the characters

Here is the complete path, from a fact in an answer to the text.

**1. The answer names the fact.** A `facts_context` result lists facts, and
links each one to a few of its claims in `fact_evidence`:

```json
{"fact_kind": "relation", "fact_id": "…", "claim_id": "…", "stance": "supports"}
```

The claims themselves are in the same envelope's `evidence` list, and
`evidence_totals` says how many exist in total for each fact and stance, so
you know when you are seeing a sample. See
[Reading a result](reading-results.md).

**2. Hydrate the fact for all its evidence.** For a relation,
`hydrate_relation` returns the fact, every supporting claim with its spans,
and the source documents:

```python
import remember

with remember.Client() as memory:
    answer = memory.facts_context("who owns the billing migration schema change")
    fact = answer.facts[0]
    if fact.kind == "relation":
        full = memory.hydrate_relation(relation_id=fact.fact_id)
        for claim in full.evidence:
            print(claim.claim_text)
            print("  said on", claim.asserted_at, "in", claim.doc_id)
            print("  origin:", claim.char_start, claim.char_end, repr(claim.source_span))
            for span in claim.evidence_spans:
                print("  support:", span.char_start, span.char_end)
        for source in full.sources:
            print(source.doc_id, source.title, source.source_kind, source.markdown_uri)
```

Hydration works on invalidated relations too, and says so in their
`validity`. It is the audit path: it reports what happened rather than
refusing to answer.

**3. Open the text.** Each source carries `markdown_uri`, the object-store
key of the converted text the spans index into. For a passage, read the
chunk directly (`search_chunks`, `adjacent_chunks`) or query `chunks_live`
with SQL queries over the query space.

**4. For converted media, find the original location.** When the text came
from a conversion, OCR or an image description, the
`claim_occurrences_live` view says so and where:

| Column | Meaning |
|---|---|
| `derivation_kind` | How the text was derived from the source, for example `markitdown` (document conversion), `ocr` (text read from an image) or `vlm_description` (a vision model's description of an image). Empty when the conversion recorded no label. |
| `evidence_mode` | How mediated it is: `source_expression` (the source's own words), `model_observation` (a model described what it saw or heard), `model_interpretation`. |
| `source_locators` | Where in the original the text sits, such as a page or a time range. |

A claim that rests on a model's description of an image is labelled as such,
so an agent can weigh it accordingly.

## Why do we believe this

Evidence says what the sources said. The **transcript** says what the memory
decided about it. `transcript_relation` returns a relation's decision history,
oldest decision first:

```python
with remember.Client() as memory:
    history = memory.transcript_relation(relation_id=fact.fact_id)
    for entry in history.transcript:
        print(entry.decided_at, entry.outcome, entry.method, entry.confidence)
```

Each entry (`TranscriptEntry`) has:

| Field | Meaning |
|---|---|
| `subject_kind` | What the decision was about (`relation`). |
| `outcome` | What was decided: `add`, `update`, `noop`, `contradict`, `retracted_source_removal`, and others. |
| `method` | How: for example `novelty_gate` (no candidates, so added without a model), `small_model` (a model decided), `exact` (a deterministic rule). |
| `confidence` | The decision's confidence, where one exists. |
| `related_id` | The other fact involved, if any. |
| `decided_by` | Who decided (the engine or a person). |
| `decided_at` | When. |
| `features` | The decision's details, such as the window before and after. |

The transcript returns the 40 most recent entries. When more exist, the
oldest are left out and the envelope's `truncation` says so.

There is no transcript route for observations or entities yet. With SQL
queries over the query space, the `memory_v1` view `identity_events_visible`
shows entity identity decisions, and `evidence_lineage` shows which
documents support each fact.

## Where to go next

- [Cite the source of an answer](../guides/cite-sources.md): turn this path
  into citations in your agent's output.
- [Contradictions](contradictions.md): evidence with stance `contradicts`.
- [Entities and facts routes](../reference/http-api/entities-and-facts.md):
  hydrate and transcript.
- [Query space](../reference/query-space.md): the evidence views.
