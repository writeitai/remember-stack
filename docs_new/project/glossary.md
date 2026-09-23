---
title: Glossary
description: Every term the RememberStack and remember.dev documentation uses, in one or two plain sentences, with a link to where it is explained.
applies_to: [remember.dev, self-hosted]
---

# Glossary

The documentation uses each of these terms in one sense only. Where a term
has a field or value in the API, it is shown in code.

## Products

**RememberStack**
: The open-source memory engine for AI agents. It turns documents into
  claims, facts and entities, and serves them to agents without a language
  model on the read path.

**`remember` package**
: The Python client, CLI and MCP server for RememberStack (`pip install
  remember`). The same code talks to remember.dev and to a self-hosted
  engine.

**remember.dev**
: The managed service that runs RememberStack for you. See
  [What remember.dev runs for you](../cloud/overview.md).

**Self-hosted**
: Running the RememberStack engine yourself, with Docker Compose. See
  [Install](../self-hosting/install.md).

## Accounts and deployments

**Deployment**
: One running RememberStack memory: its own database, storage and
  credentials. Everything you ingest and query lives in exactly one
  deployment.

**Organisation** (remember.dev only)
: The remember.dev account that holds billing and the team. See
  [Organisations, projects and members](../cloud/organisations-and-projects.md).

**Project** (remember.dev only)
: A unit of work inside an organisation. Each project gets one isolated
  deployment. See
  [Organisations, projects and members](../cloud/organisations-and-projects.md).

## Sources and documents

**Document** (`doc_id`)
: One logical file over its whole life, such as a spec or a meeting
  transcript. Its identity stays the same when its content changes. See
  [Documents, versions and sources](../concepts/documents-and-sources.md).

**Version** (`version_id`)
: One snapshot of a document's bytes. Versions are append-only and never
  edited. See [Documents, versions and sources](../concepts/documents-and-sources.md).

**Source** (`source_kind` + `source_ref`)
: Where a document comes from, given as a pair: the class of source and the
  item's stable ID within it. The pair is the document's identity. See
  [Documents, versions and sources](../concepts/documents-and-sources.md#source-source_kind-and-source_ref).

**Content object**
: The stored bytes of a file, kept once per content hash however many
  versions or documents share them.

**Representation** (`representation_id`)
: One conversion of a version into text (`document.md`). Evidence positions
  point into a representation.

**Section**
: A part of a document found from its structure, such as its headings, with
  a role. Chunks and claims know which section they belong to.

**Chunk** (`chunk_id`)
: A passage of a document's converted text: a run of whole blocks, not
  overlapping any other chunk. Chunks are what claims are extracted from and
  what passage search returns.

**Versioning mode** (`snapshot`, `living`)
: What a new version of a document means. In `snapshot` mode (the default)
  every version stays standing testimony; in `living` mode the newest
  version replaces what older ones said. See
  [Updating a source](../concepts/updating-sources.md).

**`source_modified_at`**
: When the source says the content was written or last changed. It becomes
  the said-on time of every claim from that version.

**`source_version_ref`**
: An optional revision marker from the source system, such as an ETag or
  commit SHA, stored on the version.

**Ingested by** (`ingested_by`)
: The actor that created a version: a `user`, an `api_credential` or a
  `service`. See
  [Documents, versions and sources](../concepts/documents-and-sources.md#who-ingested-a-version).

## Claims and facts

**Claim** (`claim_id`)
: One standalone statement a source made, tied to the exact text that
  supports it. Claims never change. See [Claims](../concepts/claims.md).

**Selection**
: The first extraction step: decides which statements in a chunk become
  claims and records a reason for each one it drops. See
  [Claims](../concepts/claims.md#1-selection-what-is-worth-keeping).

**Claimify**
: The second extraction step: rewrites kept statements into standalone
  claims and cites their supporting passages. See
  [Claims](../concepts/claims.md#2-claimify-make-each-statement-stand-alone).

**Grounding gate**
: The deterministic check that refuses any claim whose cited passages or
  added words are not in the source. See
  [Claims](../concepts/claims.md#3-the-grounding-gate-no-text-without-a-source).

**Attributed claim** (`is_attributed`)
: A claim that records someone's statement or stance ("Dana believes…")
  rather than asserting something directly.

**Current testimony** (`is_current_testimony`)
: Whether a claim still counts as what its source currently says. Claims
  stop being current when re-extracted, superseded in a living document, or
  deleted. See [Claims](../concepts/claims.md#current-and-superseded-testimony).

**Fact** (`fact_id`)
: A piece of knowledge the memory holds true, built from claims: either a
  relation or an observation. Facts change as evidence arrives. See
  [Facts](../concepts/facts.md).

**Relation**
: A fact that connects two entities with a predicate ("Ravi works_on billing
  migration"). Relations are the edges of the graph.

**Observation**
: A fact about one entity: a value, property, state or stance. Observations
  are searchable but are not graph edges.

**Predicate**
: The name of a relation's connection, from a governed vocabulary of 16
  core predicates, an `other:` escape value, or an extension pack. See
  [Facts](../concepts/facts.md#predicates).

**Extension pack**
: A named set of extra predicates for a domain, such as `work`.

**Adjudication**
: The step that decides how a new assertion changes the facts: add, confirm,
  adjust, supersede or contradict. See
  [Facts](../concepts/facts.md#how-a-claim-changes-the-facts).

**Evidence count** (`evidence_count`)
: The number of distinct documents whose current testimony supports a fact.

**Support withdrawn** (`support: "withdrawn"`)
: A fact whose only support disappeared because of a processing change
  rather than a source change. It is still returned, flagged. See
  [Facts](../concepts/facts.md#support-withdrawn).

**Retraction**
: A fact losing belief because a living source removed, or a deletion took
  away, its only support. Recorded as `retracted_source_removal`. See
  [Updating a source](../concepts/updating-sources.md).

**Label**
: A fact's readable sentence, without dates.

## Entities

**Entity** (`entity_id`)
: One real-world referent (a person, team, project, event) however it is
  spelled. Entities have no type. See [Entities](../concepts/entities.md).

**Alias**
: A spelling a source used for an entity.

**Profile**
: A short prose summary of an entity built from its important facts, used
  to decide identity.

**Resolution**
: Matching a name to an entity. At write time a cascade of steps decides; at
  query time `resolve_entity` returns candidates and never guesses. See
  [Entities](../concepts/entities.md).

**Tier** (`T0`–`T3`)
: The resolution step that found an entity candidate: exact alias, similar
  spelling, similar sound, profile embedding.

**Merge**
: Redirecting one entity into another when both are the same referent.
  Merges keep a snapshot and can be undone.

## Evidence and disagreement

**Evidence**
: The claims that support or dispute a fact, and the source text that
  supports a claim. See [Evidence](../concepts/evidence.md).

**Evidence span** (`evidence_spans`)
: A character range in a version's converted text that supports a claim. A
  claim has one or more.

**Stance** (`supports`, `contradicts`)
: Whether a claim backs a fact or disputes it.

**Contradiction**
: Two or more facts that cannot all be true, returned together as a
  contradiction group. See [Contradictions](../concepts/contradictions.md).

**Co-member**
: Another fact in the same contradiction group.

**Corroboration**
: Independent documents stating the same thing, counted by distinct
  documents, never by versions or repetitions.

**Supersession**
: A later fact replacing an earlier one because the world changed; the
  earlier fact's window is closed at the later one's start.

**Transcript**
: A fact's decision history: every adjudication, with outcome, method and
  confidence. See [Evidence](../concepts/evidence.md#why-do-we-believe-this).

## Time

**World time** (valid time)
: When something was true or happened: `valid_from`, `valid_until` and
  `valid_precision` on facts; the `claim_valid_*` fields on claims. See
  [Time](../concepts/time.md).

**Said-on time** (`asserted_at`)
: When a source made a statement.

**Belief time** (`ingested_at`, `invalidated_at`)
: When the memory learned a fact and when it stopped believing it.

**Precision** (`valid_precision`)
: How exact a window is: `instant`, `day`, `month`, `quarter`, `year`,
  `open` (known start, ongoing) or `unknown`.

**Claim valid kind** (`claim_valid_kind`)
: What a claim's stated time describes: `event_time`, `effective_period`,
  `measurement_period` or `proposition_validity`.

**Time mode** (`current`, `at`, `overlap`, `history`)
: Which facts a query selects by world time. See
  [Time](../concepts/time.md#asking-about-time).

**Temporal match** (`confirmed`, `possible`)
: Whether a fact's window establishes that it matches the query's time, or
  only fails to rule it out.

**Temporal scope** (`temporal_scope`)
: The part of every result that states which time it describes and when it
  was evaluated.

## Processing

**Pipeline**
: The stages that turn an ingested version into claims, facts and search
  indexes, in the background. See [The pipeline](../concepts/pipeline.md).

**Stage**
: One step of the pipeline, such as `convert` or `extract_claims`, with a
  status per version.

**Dead letter** (`dead_letter`)
: Work that failed and will not be retried automatically.

**Readiness**
: Whether given versions have finished processing and the capabilities you
  need (`pipeline`, `p1`, `live_graph`, `p3`) are ready, so the content can
  be recalled. See [The pipeline](../concepts/pipeline.md#readiness).

## Reading

**Assured operation**
: One of four fixed, registered reads: `resolve_entity`, `facts_context`,
  `claims_and_sources_context`, `combined_context`. See
  [Retrieval](../concepts/retrieval.md) and
  [Assured operations](../reference/assured-operations.md).

**Primitive**
: A lower-level read such as search, lookup, graph traversal or hydration.
  See [Retrieval](../concepts/retrieval.md#retrieval-primitives).

**Envelope**
: The object every read returns: the results plus an account of their
  grain, time, completeness and emptiness. See
  [Reading a result](../concepts/reading-results.md).

**ContextBundle/v2**
: The result of `combined_context`: a claims-and-sources envelope and a
  facts envelope side by side.

**Grain** (`fact`, `evidence`, `composite`, `compiled`)
: What kind of truth an envelope holds.

**Negative** (`unknown_entity`, `known_empty`, `boundary`)
: The typed reason an answer is empty.

**Hydration**
: Re-reading search candidates from live data before returning them, and
  dropping those that no longer hold (`dropped_by_hydration`).

**Semantic search**
: Search by meaning over embeddings (vectors), with pgvector.

**BM25**
: Keyword search that ranks by term matches, with PostgreSQL's
  `pg_textsearch`.

**RRF** (reciprocal rank fusion)
: Merging several ranked lists by summing `1 / (60 + rank)` for each item.

**Query space** (`memory_v1`)
: The prepared, read-only views and functions that SQL queries run over. See
  [Query space](../reference/query-space.md).

**SQL queries**
: Reading memory with SQL over the query space. Every statement is parsed
  and validated against the query space before it runs. See
  [Explore memory with SQL](../guides/sql.md).

**Saved query**
: A reviewed SQL query stored under a name and run with parameters. See
  [Saved queries](../guides/saved-queries.md).

**MCP**
: The Model Context Protocol, which lets coding agents call memory as
  tools. See [Connect your coding agent](../start/connect-your-agent.md).
