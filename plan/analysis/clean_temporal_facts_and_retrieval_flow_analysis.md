# Clean Temporal Facts and Entity-First Retrieval Flow — Analysis

**Date:** 2026-09-21  
**Status:** Non-binding working analysis  
**Problem Area:** Fact label temporal representation, vector embedding purity, and agent retrieval discipline

---

## 1. Problem Framing & Motivation

Recent end-to-end benchmark forensic evaluations on the LoCoMo conversation suite (e.g. `conv-42` under `locomo-v38-jev-conv42-dev`) revealed two critical architectural issues across the ingestion and retrieval layers:

### Problem 1: Hardcoded `[world time: ...]` in Postgres and Embeddings
In worker P1 (`rememberstack.workers.p1`), observation and relation labels are formatted before persistence and vector embedding as:
```python
label = f"{observation.obs_label} [world time: {describe_fact_window(window=window)}]"
```
This produces database rows in the `observations` and `relations` tables such as:
* `"Joanna said she printed her first full screenplay on 2022-01-21. [world time: 2022-01-21 (day precision)]"`
* `"Joanna said her new screenplay is her own story. [world time: world date unknown]"`
* `"Nate said he won his second tournament. [world time: 2022-04-25 through 2022-05-01 (day precision)]"`

This design exhibits four severe defects:
1. **Vector Embedding Contamination:** The embedding worker passes this formatted `label` directly into the embedding model (`P1FactRow(label=label)`). For all undated facts (~80% of personal memory observations), every vector embedding contains the identical 5-token suffix `"[world time: world date unknown]"`. This creates artificial semantic clustering among unrelated facts simply because their occurrence date is unknown.
2. **Database Schema Denormalization:** The PostgreSQL tables `observations` and `relations` already have first-class, typed columns: `valid_from (timestamptz)`, `valid_until (timestamptz)`, and `valid_precision (enum)`. Stamping display-formatted brackets into the text columns `obs_label` and `fact_label` destroys clean separation of concerns.
3. **Consumer & LLM Confusion:** "World time" is internal bitemporal jargon (distinguishing real-world validity from database transaction time). To an external LLM answering questions, "world time" sounds like UTC, time zones, or virtual game clocks. Furthermore, returning `"[world time: world date unknown]"` wastes prompt tokens and clutters context.
4. **String Parsing Burden:** Downstream consumers (agents, SDKs, UIs) must regex-parse or strip the label string to recover clean prose or extract the dates.

### Problem 2: Inverted Retrieval Hierarchy (Claims-First vs. Fact-First)
The benchmark answering prompt currently instructs:
> *"1. Recall: use question_context first for ordinary questions; it combines semantic and exact-text retrieval over claims and live source passages."*

This prompt instruction forces answering agents to:
1. Immediately issue flat semantic searches over `claims_and_sources_context` or `combined_context`.
2. Skip entity resolution (`resolve_entity`), thereby missing entity-anchored facts that are scattered across different sessions.
3. Bypass the de-duplicated, Jev-adjudicated **fact layer**, falling victim to top-K truncation.

**Forensic Evidence from `conv-42/qa/0011` ("What is Joanna allergic to?"):**
* Ingestion extracted **all 4 allergies** into the `observations` table on entity `Joanna`:
  1. `Joanna said she can't have dairy`
  2. `Joanna said that she is lactose intolerant`
  3. `Joanna said she allergic to most reptiles and animals with fur`
  4. `Joanna said she had recently found out that she was also allergic to cockroaches`
* The agent called `combined_context({'query': 'What is Joanna allergic to?'})`.
* Because Joanna did not repeat the word "allergic" when discussing dairy, vector search ranked dairy lower than pet allergy commentary, cutting dairy off at top-15.
* Had the agent resolved entity `Joanna` and queried Joanna's facts first, all 33 observations on Joanna would have been delivered in one coherent payload, answering the question completely and accurately.

---

## 2. Technical Options & Alternatives Considered

### Option A: Retain `[world time: ...]` in Postgres, strip it at the API layer
* *Approach:* Keep P1 writing `[world time: ...]` to `obs_label`, but use a regex or string manipulation at the HTTP API response serialization layer to remove it.
* *Critique:* Rejected. This leaves the vector embedding contamination intact, keeps Postgres denormalized, and introduces brittle string stripping at every query surface.

### Option B: Clean labels in Postgres & Embeddings; surface structured temporal fields (Chosen)
* *Approach:*
  1. In `workers/p1.py`, set `obs_label = statement` and `fact_label = deterministic_fact_label(subject, predicate, object)`. No brackets, no suffixes.
  2. The vector embedding model embeds only the clean semantic text.
  3. The API response for facts (`FactResult`) continues to carry typed temporal bounds (`valid_from`, `valid_until`, `valid_precision`) in its structured `validity` object, matching database column names.
  4. Prompt serialization presents clean prose. If an event date is known, it is presented with transparent labels (e.g. `(occurred: 2022-01-21)` or `(valid: Jan 2022)`). If unknown, no temporal noise is emitted.
* *Merits:* Simple, robust, eliminates vector pollution, respects database schema normalization, and removes cognitive friction for LLMs.

### Option C: Retrieval Flow — Entity First $\rightarrow$ Fact Layer First $\rightarrow$ Sources Fallback (Chosen)
* *Approach:*
  1. **Identify Entities:** When a question names specific entities (people, places, projects), resolve them first (`resolve_entity`) and query the fact layer with those entity anchors.
  2. **Fact Layer First:** Use `facts_context` (or entity observations) to reach verified, de-duplicated, attributed knowledge.
  3. **Sources / Claims Fallback:** If the fact layer does not contain the answer, or if the question explicitly asks for verbatim dialogue quotes, conversational tone, or raw text excerpts, fall back to `claims_and_sources_context`.
* *Merits:* Matches human memory cognitive retrieval (semantic memory first, episodic transcript fallback). Maximizes the utility of RememberStack's core value proposition (the adjudicated fact layer).

---

## 3. Date Semantics for Answering Agents

To eliminate temporal confusion, the answering agent must be explicitly taught the exact semantic meaning of each date field:
1. **`valid_from` / `valid_until` (Event / World Time):**
   * *Meaning:* The actual calendar date or time range when the event occurred or the state held true in the real world.
   * *Usage:* Use this date when the user asks *"When did X happen?"*, *"How long has X been Y?"*, or *"In what year did X do Z?"*.
2. **`asserted_at` (Conversation / Source Timestamp):**
   * *Meaning:* The timestamp when the message was sent or the document was recorded.
   * *Usage:* Use this to anchor relative time expressions. If the text says *"I finished it last Friday"* or *"yesterday"*, calculate the date relative to `asserted_at`.
3. **`valid_precision` (Granularity):**
   * *Values:* `instant`, `day`, `month`, `quarter`, `year`, `open`, `unknown`.
   * *Usage:* Do not invent finer precision than the source gives (e.g., do not turn a `year` precision into a specific day).

---

## 4. Operational and Migration Consequences

1. **Backwards Compatibility:**
   * `FactResult` in `Envelope` already contains `validity: Validity`. Removing the string suffix from `FactResult.label` makes `label` match `statement`.
   * Existing SDKs and CLIs accessing `fact.label` receive cleaner text; those accessing `fact.validity.valid_from` continue to receive the structured timestamp.
2. **Re-indexing / Migration:**
   * Changing P1's label formulation requires existing stores to re-run the label/embed stage (`P1`) or update `obs_label = statement` and `fact_label = clean_label`.
   * Because `obs_label_version` tracks the component version, bumping `p1-fact-label` cleanly triggers deterministic re-labeling and re-embedding.
