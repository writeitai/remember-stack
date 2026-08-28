# Entity-resolution uncertainty and convergence — failure analysis

**Date:** 2026-08-28

**Status:** analysis; non-binding evidence for D99 and the D95 amendment

**Run examined:** LoCoMo `conv-26`, RememberStack v0.6.0 at
`ec457a6a3cce0207af0455b9043af53991d29a68`, PostgreSQL 19beta3,
`RS-LoCoMo-Full-v15`

## 1. Question

D95 correctly stopped exact-name matching from silently merging homonyms. Its
remaining binary premise was wrong: when T4 could not prove that a mention and
candidate were the same referent, the resolver treated that uncertainty as
positive evidence that they were different. This analysis asks how resolution
can preserve the false-merge safety of D95 without turning early, thin evidence
into permanent fragmentation.

## 2. Observed failure

The fresh run completed all 19 conversation sessions and reached P1, live-graph,
and P3 readiness. Its identity state was nevertheless unstable:

| Diagnostic | Result |
| --- | ---: |
| Active entities | 48 |
| Active exact-name `Caroline` entities | 19 |
| Active exact-name `Melanie` entities | 4 |
| Current resolution decisions | 1,089 |
| T3 decisions | 0 |
| Automatic resolution exclusions | 87 |
| Exclusions between Caroline fragments | 58 |
| Merge events | 0 |

The first Caroline was minted because no candidate existed. The second arrived
109 milliseconds later. T4's stored rationale said the names matched but the
candidate had no profile or distinguishing facts, so identity could not be
established. The binary verdict recorded a confident non-match, minted a second
Caroline, and wrote a durable cannot-link. That is evidence of uncertainty, not
evidence of two people.

The pattern then compounded. Candidate loading stopped at ten entities and T4
adjudication stopped at three. A rejection of those three could still authorize
another mint even though candidates four through ten—and eventually candidates
beyond ten—were unchecked. Exact candidates were oldest-first, so recent,
contextually relevant fragments could disappear from the checked prefix.

All active entities eventually had current profile summaries and embeddings.
No production caller invoked `EntityClusterer.recluster_neighborhood`, so no
later lifecycle reconsidered the early decisions. Even if it had run, the 58
automatic exclusions would have told clustering that many Caroline pairs were
forbidden to merge. Automatic cluster merge was also disabled; a large
Caroline proposal would correctly route to review under the blast-radius guard.

The result affected retrieval. `resolve_entity("Caroline")` returned 19
candidates and the answer agent returned `Unknown` without using fact-text
context that contained the requested identity. The eight-item score remained
5/8. One unrelated item exhausted the already-bounded malformed-reader retry;
that does not explain identity fragmentation.

## 3. Causal mechanisms

### 3.1 Binary T4 output gave uncertainty too much authority

`AdjudicationVerdict.match` has only true and false. The prompt asks whether two
references are the same, but a false answer can mean either:

- facts distinguish two referents; or
- the available facts are too thin to decide.

Only the first statement supports a cannot-link. The implementation cannot
represent the second, so it turns both into a new authoritative identity and a
durable exclusion.

### 3.2 Bounded work was mistaken for exhaustive evidence

`blocking_limit` and `t4_max_candidates` are necessary cost and latency bounds.
They do not prove that the correct referent is absent from the unchecked tail.
The resolver did not retain a completeness bit, so “the checked prefix did not
match” became “the registry contains no match.”

### 3.3 T3 had no usable recovery path

T3 may accept only when exactly one candidate exists. That is the current D95
safety rule, not an implementation accident. A current profile summary and an
embedding whose model, input policy, and text hash attest to the same input are
independent prerequisites. The run's zero T3 decisions proves the tier was
inactive, but the aggregate decision rows do not say which prerequisite failed
for each mention.

### 3.4 Convergence existed as code, not as a lifecycle

The clusterer implements bounded neighborhood re-decision, reversible redirects,
cannot-links, and blast-radius review. Only tests call it. Profiles becoming
current therefore had no identity consequence, even though the binding registry
design says new evidence jointly re-decides the local pocket.

### 3.5 Provider latency extended the lemma critical section

`CascadeResolver.resolve` takes a transaction-scoped PostgreSQL advisory lock
for the normalized lemma and performs both T3 embedding and T4 generation before
commit. The database lock is correct for serializing identity writes, but the
external request does not need to occupy the locked transaction. One normalization
item timed out on this path. The separate supersession timeout used a per-entity
observation/profile lock and must not be attributed to the lemma lock.

## 4. Alternatives

| Alternative | Disposition | Reason |
| --- | --- | --- |
| Tune T4 wording or confidence only | Reject | A binary schema still cannot distinguish absence of proof from proof of difference. |
| Restore exact-name auto-merge | Reject | Reintroduces the father/son failure D95 exists to prevent. |
| Remove candidate limits | Reject | Unbounded provider work is not a scale contract. |
| Fail ingestion until identity is certain | Reject | Thin or genuinely ambiguous evidence could park ordinary ingestion indefinitely. |
| Mint a merge-eligible provisional fragment | Choose | Preserves ingest availability without claiming exhaustive novelty or writing a false cannot-link. |
| Run global re-clustering | Reject | Work grows with the deployment rather than the touched identity neighborhood. |
| Wire bounded neighborhood convergence after profile refresh | Choose | Uses the existing reversible mechanism when the evidence it needs becomes current. |
| Enable every automatic cluster merge immediately | Reject | The distance cut lacks accepted calibration; current review and blast-radius safety remains binding. |
| Increase lock timeout | Reject | Hides provider latency inside a database transaction and increases tail contention. |
| Snapshot, call provider unlocked, revalidate | Choose | Preserves serialized commits while keeping network latency outside the critical section. |

## 5. Resulting contract

The binding design should require:

1. T4 returns `same`, `different`, or `insufficient_evidence`.
2. Only `different`, supported by the adjudicated candidate evidence, creates a
   durable automatic exclusion.
3. Candidate generation returns whether its result was untruncated by the
   configured limit. This is work-prefix completeness, not perfect blocking
   recall. T4 likewise
   records how many candidates were adjudicated.
4. A mint is an authoritative cascade outcome only when candidate search was
   untruncated and every surfaced candidate received a supported `different`
   verdict. That authority is bounded by blocking recall. Every other mint
   is a merge-eligible provisional fragment with the reason retained in the
   append-only decision evidence.
5. Current profile publication invokes bounded convergence for the touched
   identity neighborhood. Missing/stale profiles remain ambiguity; configured
   merge and blast-radius review guards remain in force.
6. T3 records one deterministic outcome: accepted, below threshold, multiple
   candidates, missing/stale profile, missing/wrong-generation embedding, or
   input-hash mismatch. Aggregate metrics use the bounded reason vocabulary;
   entity ids stay in the decision audit, not metric labels.
7. Resolver provider calls use snapshot → unlocked call → locked revalidation.
   Changed inputs discard the stale result and retry a bounded number of times.
8. Retrieval ambiguity remains explicit, but an answer agent may not treat
   `resolve_entity` alone as content evidence before returning `Unknown`; it
   must use one bounded testimony/fact/context path.

The existing malformed structured-answer retry already satisfies the bounded
reader-recovery recommendation. No second retry subsystem is required.

The migration classifies pre-D99 automatic exclusions as ineffective
`legacy_binary` audit rows because their binary verdict cannot prove difference;
human exclusions remain effective. A later supported-different or human
decision may revalidate the pair, and a superseding decision may retire it.

## 6. Acceptance evidence

- Repeated Caroline and Melanie fixtures produce one coherent convergence
  proposal after profiles become current; accepting the proposal yields one
  active survivor for each.
- Father/son and same-name-colleague fixtures remain separate.
- `insufficient_evidence` writes no exclusion.
- Candidate or T4 truncation cannot claim authoritative novelty.
- Every non-T3 decision retains exactly one T3 outcome reason.
- A resolver-state change during an unlocked provider call forces revalidation
  and retry; no stale decision commits.
- One- and six-worker runs have the same post-convergence partition.
- The deterministic one-/six-worker acceptance workload drains with zero dead
  letters. Repeated production contention still has the ordinary visible DLQ
  and typed replay path after the outer work-ledger attempt budget.
- An ambiguous entity lookup followed by `Unknown` is rejected until a bounded
  content-bearing read has been attempted.

Only after these deterministic gates pass does another paid `conv-26` run
measure a changed identity contract rather than model and scheduling variance.
