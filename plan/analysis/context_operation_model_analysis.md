# Context operation model analysis

*2026-08-10. Non-binding analysis supporting D87 and the context-operation
amendment to `plan/designs/open_query_space_design.md`.*

## Question

The clean-cut query surface retained `resolve_entity`, `question_context`, and
`current_context`. That surface is small, but its mental model is not clean:

- `question_context` is named after its input rather than the authority of its
  output. Its default output is source testimony, but optional flags add facts
  and entity candidates to the same operation.
- `current_context` exposes only the `facts_current` projection even though the
  fact layer preserves world-valid and system-belief history.
- entity grounding is either hidden inside an optional channel or absent from
  the context operation. A caller cannot consistently resolve an ambiguity once
  and reuse the confirmed identities across testimony and fact retrieval.

The problem is therefore not a missing search algorithm. It is that the public
operation boundaries do not match RememberStack's two truth layers.

## Sources inspected

- `plan/designs/open_query_space_design.md`, especially §§1–3, 8–11.
- `plan/designs/retrieval_design.md`, especially the D41/D48/D49 contracts.
- `plan/designs/agent_retrieval_surface_design.md`, the superseded catalog that
  introduced `current_context` and the mixed compound operations.
- `src/rememberstack/surfaces/query_engine.py`: `question_context`,
  `current_context`, entity resolution, relation/observation as-of reads, and
  their PostgreSQL confirmation statements.
- `src/rememberstack/model/envelope.py`: the single-grain and composite D49
  contracts.
- `memory_v1.facts_current`, `facts_visible_history`, `facts_as_of`,
  `claims_live`, `claims_visible_history`, and the semantic P1 bridge as bound
  in the open-query design.
- `decisions.md`, especially D41, D48–D50, D82, D83, and D85.

## Findings

### The existing names hide the two authorities

Claims and current source passages answer “what did sources say?” Relations and
observations answer “what does the system hold true?” Those are separate
authorities even when one answer needs both. `question_context` does not tell a
caller which authority it returns, while `include_facts` and `include_entities`
make its output depend on unrelated booleans.

`all_sources_context` would not repair the problem. Results are bounded rather
than exhaustive, P3 is not read by that operation, and adjudicated facts are not
sources. `source_context` is closer, but “source” can mean document metadata or
raw originals. The existing corpus already calls claims immutable source
testimony, so `testimony_context` is the most direct layer name.

### `current_context` leaves the historical fact authority on the hard path

The implementation semantically nominates fact labels from P1 and confirms
them by joining `memory_v1.facts_current`. A relation or observation whose
world-valid interval ended is therefore rejected even when the system still
believes that historical fact. Historical fact membership remains accessible
through SQL and timestamped primitives, but the ordinary semantic fact
operation cannot request it.

This is particularly costly for temporal questions: a caller must abandon the
one-call path, discover the query schema, and compose semantic nomination with
historical PostgreSQL confirmation. The storage model is bi-temporal, but the
easy fact operation is not.

### `only_current` is not a sufficient temporal contract

A boolean has no unambiguous false case. It cannot distinguish:

- facts valid at one world-time instant;
- facts overlapping a world-time interval;
- the complete currently accepted historical timeline; or
- what the system believed at an earlier system time.

The ordinary context contract should select world time explicitly and always
state that it answers from the current system belief. Reconstructing “what did
the system believe at T?” is a distinct audit intent already served by
`facts_as_of(valid_at, believed_at, ...)` and open SQL. Folding that second
intent into the default context tool would require historical evidence-state
reconstruction as well as fact membership and would blur the common answer
path. The returned facts still disclose both stored clocks.

### Entity resolution should be reusable but not mandatory

Resolved survivor IDs are a precision boundary, not a prerequisite for search.
A question may contain no explicit entity, an extraction may not have produced
one, or semantic retrieval may be the only useful entry. Requiring an entity
would create a recall failure.

When the caller has resolved an ambiguity, however, every context operation
should accept the same optional `entity_ids` and confirm them in PostgreSQL.
The context operations must not silently re-resolve names. One explicit
`resolve_entity` result can then anchor all later reads.

For multiple anchors, an eligible result touches at least one confirmed entity;
ranking prefers candidates covering more anchors. This puts testimony mentioning
both anchors and a direct A–B relation ahead of comparable one-anchor results
without excluding observations or useful bridge facts. A public any/all switch
is unnecessary. The scope must constrain nomination before its bounded depth;
filtering a global top-k afterward recreates the recall failure this design is
meant to remove.

### Composition must preserve, not flatten, the layers

A combined operation is useful because ordinary question answering often needs
both source recall and adjudicated facts. It must not introduce a third search
implementation or merge claims and facts into one relevance list.

`answer_context` should call the same `testimony_context` and `fact_context`
authorities and return their complete responses as two named sections. Each
section keeps its own grain, negative, truncation, freshness, dropped count,
and time disclosure. A small `ContextBundle/v1` wrapper containing the two D49
envelopes is clearer than extending a flat envelope so that one truncation or
negative appears to describe both independent reads.

## Options

### Keep `question_context` and add more flags

Rejected. More flags preserve a mixed-grain operation whose name still says
nothing about its output authority. It also keeps fact and entity retrieval as
special cases inside testimony retrieval.

### Replace `current_context` with `fact_context` but add `only_current`

Rejected. The name improves, but the boolean cannot express point, interval,
history, or the second clock without contradictory companion parameters.

### Expose only the two orthogonal operations

Viable but not selected. It has the smallest catalog, but makes the most common
general-answer path spend two agent calls and reproduce the same orchestration
in every consumer. It remains the fallback if measurements show that the
composite operation provides no call, latency, or answer-quality benefit.

### Two layer operations plus one pure composite

Selected:

1. `testimony_context` returns high-recall claims and source passages only.
2. `fact_context` returns temporally selected relations and observations plus
   evidence attached specifically to those facts.
3. `answer_context` returns the two complete child responses without blending.
4. `resolve_entity` remains the explicit identity authority.

This creates one memorable routing rule: what was said → testimony; what is or
was true → facts; both → answer; ambiguous identity → resolve.

This selection consciously amends the existing anti-accretion gate rather than
pretending the fourth tool already passed its frequency thresholds.
`answer_context` is a narrow exception because it has no independent retrieval
authority, settings, ranking, hydration, or transformation: its entire result is
the two literal complete child responses. The exception does not apply to a
second bundle or to a composition that changes a child. If that exact-equivalence
constraint proves unhelpful, removing the wrapper leaves both authorities
untouched.

## Contract consequences

- `question_context` and `current_context` are removed outright. There is no
  alias, deprecation window, or compatibility adapter because the library has
  no consumers requiring one.
- `include_facts` and `include_entities` disappear. Entity candidates remain the
  output of `resolve_entity`; context operations consume optional confirmed
  IDs rather than returning unrelated candidates.
- `testimony_context`, `fact_context`, and `answer_context` require `query` and
  accept one to twenty optional unique `entity_ids`. Omitting IDs preserves
  deployment-wide semantic retrieval. Every supplied ID must be a current
  survivor in the deployment; any unavailable ID produces one opaque
  `unknown_entity` result rather than partial filtering.
- `fact_context` accepts an explicit world-time selector: current, one
  valid-time instant, an overlapping valid-time interval, or past-through-now
  history. Future-starting facts require an explicit `at`/`overlap` request.
  Every mode answers from the current system belief and returns both stored clocks.
  Historical system-belief reconstruction remains the explicit
  `facts_as_of(valid_at, believed_at, ...)`/open-SQL audit path.
- `answer_context` accepts the same fact-time selector and passes the same
  confirmed entity IDs to both children. The time selector controls the fact
  section; testimony remains source testimony with its own asserted and
  claim-valid clocks rather than being silently promoted to the selected fact
  state.
- The four assured operations remain zero-LLM, bounded, D48-confirmed, and
  discoverable through the existing API/SDK/CLI/MCP operation transport.
- The open SQL/Cypher, saved-query, direct primitive, and P3 surfaces remain
  independent infrastructure. Removing redundant intent tools does not remove
  underlying retrieval capability.

## Risks and required proof

- Entity- and fact-time-scoped nomination must restrict the eligible set before
  candidate depth and final top-k; global top-k followed by filtering creates a
  recall ceiling. Rebuildable P1 scope metadata or PostgreSQL-selected eligible
  IDs scored by P1 are acceptable; hydration-only filtering is not.
- Historical fact nomination must be confirmed against the chosen temporal
  authority, not nominated from `facts_current` and then relabeled historical.
- Historical mode must exclude facts the current system has retracted as
  incorrect while retaining ended world-valid intervals the system still
  believes were true. It must not conflate `valid_until` with
  `invalidated_at`.
- Under a frozen store, projection generations, and evaluation clock, the
  composite response must prove field-for-field child equivalence with no
  bundle-layer exceptions: calling `answer_context` cannot change or regenerate
  any child field. A child execution failure returns no half-bundle.
- The catalog, manifest hash, consumption skill, public documentation, and
  benchmark protocol identity must roll atomically when implementation ships.
