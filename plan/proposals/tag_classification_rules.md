# Proposal: tag classification rules

**Status:** not chosen (2026-11-13). Live proposal; see the adoption trigger.
**Builds on:** D156, [document tags](../designs/document_tags_design.md). D156
assignments record no origin; this proposal would add one so that rules can
manage their own assignments without touching a caller's.

## The idea

A deployment declares **rules** that assign registered tags to documents
automatically, so a caller does not have to tag every document itself.

The motivating example is one memory holding the work of several WriteIt.ai
products. Every document should say which product it belongs to:
`project:remember`, `project:mountable` or `project:gitpin`. Repository
documents and board tickets reveal their product through where they came
from; an email thread or a meeting note may only reveal it through what it
talks about.

Rules come in two kinds, with very different costs.

### 1. Metadata rules: deterministic, no model

A metadata rule assigns a tag when a document's source identity or D134
metadata matches a condition:

| Condition | Tag |
|---|---|
| `source_path` starts with `github.com/writeitai/gitpin/` | `project:gitpin` |
| `source_kind = autoboard-ticket` and `source_ref` starts with `mountable/` | `project:mountable` |
| an author's address ends with `@remember.dev` | `project:remember` |

Conditions use only stored fields: `source_kind`, `source_ref` and
`source_path` (exact or prefix), `family`, author and recipient addresses
(exact or domain), `thread_ref`. They are evaluated when a version or a
metadata observation is ingested, and for every live lineage when a rule is
created or changed (a bounded background job). They make no model calls, so
they cost the same as a caller tagging at ingest.

### 2. Classifier rules: model-based, paid

A classifier rule asks a model to choose among candidate tags using the
tags' **descriptions**:

> Choose at most one of `project:remember`, `project:mountable`,
> `project:gitpin` for this document, or none if it is about none of them.

- **Input:** the document's title, D134 metadata and its existing overview
  summary, not its full text, which bounds the cost of each call. The model
  returns a tag from the candidate set, or none, plus the passage that
  justifies the choice; the justification is validated against the source
  like any other evidence span.
- **"At most one" belongs to the rule, not to the tags.** D156 has no tag
  groups. The constraint lives in the rule ("pick at most one of these"), so
  tags stay simple and a caller can still assign two projects by hand.
- **Cost and reuse.** One model call for each new version whose classifier
  input changed. The cache key is the classifier input hash: overview hash,
  metadata used, the candidate tags' names and descriptions, the rule
  version and the model. Editing a candidate tag's description therefore
  re-classifies every document once; that boundary must be declared, as for
  every D56 reuse key. The classifier is its own stage and enters no
  extraction or embedding reuse key.
- **Skip what people already decided.** A classifier rule does not run on a
  document that a caller has already tagged with any of its candidates.

### Assignments, corrections and disclosure

- `document_tags` gains an `origin` column (`caller` | `rule`) and a
  `rule_id`; rule assignments are stored beside caller assignments.
  Re-evaluating a rule replaces only its own assignments.
- A person who removes a rule-assigned tag records a **suppression** for
  that document and tag, which every rule respects; otherwise the next
  re-evaluation would put the tag back.
- Wherever tags are returned, a rule assignment says which rule made it and,
  for a classifier, its justification passage. Filters match a tag from any
  origin.

### Sketch of storage

`tag_rules(rule_id, kind metadata | classifier, candidate tag_ids, condition
or instruction, at_most_one, enabled, version)`; `document_tags.origin`
(`caller` | `rule`) plus a `rule_id`; `document_tag_suppressions(doc_id,
tag_id, created_at)`.

## Why it is not chosen now

The first deployment that needs tags (the WriteIt.ai memory) ingests
repository documents and board tickets, whose product is known to the
caller at ingest, so callers can tag deterministically with no rule engine.
Classifier rules add a paid processing stage, a prompt, an evaluation set
for classification quality and a correction workflow. That cost needs
evidence that caller tagging falls short.

## Adoption trigger

- **Metadata rules:** several callers or connectors repeat the same mapping
  from source fields to tags, or documents arrive through a path that cannot
  tag at ingest (for example, a mailbox connector).
- **Classifier rules:** in a real deployment, a measured share of documents
  (emails, meeting notes, chat exports) carry no reliable product signal in
  their metadata, and agents miss them in product-filtered reads.
