# LoCoMo `conv-42`: recurring events collapse in the observation layer

**Date:** 2026-09-03

**Status:** analysis (non-binding); the decision it motivated is D106

**Scope:** why a store that extracted all seven of a speaker's tournament wins
as clean, dated claims and resolved one entity for him ended with four win
facts — and what the counting question's reader then did with it. Evidence is
read-only SQL against a retained v0.11.0 store; nothing here changes retrieval,
identity, or the answer prompt.

## 1. Run coordinates

| Field | Value |
| --- | --- |
| Engine | RememberStack v0.11.0, revision `e7b173a19e8a992ec57bf75ce6593373ab2fc2c5` |
| Protocol | `RS-LoCoMo-Full-v18`, publication tier, fingerprint `2c2d3070d7620b176c6396e1245f07e8e59f44bec01b09d837ac2e144b064df8` |
| Sample | `conv-42` (29 sessions, 629 turns, 199 retained questions) |
| Deployment | `3f134f36-6b59-4060-9318-bf77f2a3463b` on the managed-cloud benchmark host |
| Dataset SHA-256 | `79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4` |
| Scoring-base backup | `gs://remember-stack-locomo-backups/2c2d3070…/e7b173a1…/2026-09-01T12-19-28.807846Z/conv-42/20260902T072007Z-75e029f2` (verified) |
| Ingest outcome | 1,772 stage units succeeded, one `normalize_relations` dead letter replayed; 1,298 claims, 716 observations, 22 active entities, zero duplicate-name groups, one Nate and one Joanna |

The answer stage stopped at 182 of 199 questions when the provider account
ran out of credits; the judge did not run. The two counting questions this
document is about were answered before the stop.

## 2. The seven wins, layer by layer

The gold answer to `conv-42/qa/0080` ("How many tournaments has Nate won?")
is **seven**, with evidence in seven sessions spread over ten months.

**Extraction (E2) — 7 of 7.** Every win became a decontextualized, attributed
claim with a resolved D41 event window (`claim_valid_kind = 'event_time'`,
day precision):

| Session | Claim text | Resolved event date |
| --- | --- | --- |
| D1 | Nate said he won his first video game tournament last week. | 2022-01-14 |
| D10 | Nate said that Nate won Nate's second tournament last week. | 2022-04-25 |
| D14 | Nate just won another regional video game tournament last week. | 2022-05-27 |
| D17 | Nate won Nate's fourth video game tournament on Friday. | 2022-07-08 |
| D19 | Nate won an international tournament yesterday. | 2022-08-21 |
| D22 | Nate said that Nate won a really big video game tournament last week. | 2022-09-29 |
| D27 | Nate won the final of a big Valorant tournament last Saturday. | 2022-11-05 |

**Identity (D100/D102) — clean.** One active Nate entity (568 resolved
mentions), one Joanna (655). Every claim above is anchored on that Nate.

**Fact layer (E3 + D43 adjudication) — 4 of 7.** The `observation_adjudications`
transcript for the same claims:

| Incoming claim | Outcome | Absorbed into |
| --- | --- | --- |
| won his first video game tournament | `add` | — (observation, Jan) |
| won Nate's second tournament | `add` | — (observation, May) |
| won another regional tournament | `add` | — ("Winning the tournament was a huge confidence boost", Jun) |
| won Nate's fourth tournament | relation | `Nate —other:won→ "Nate's fourth video game tournament"` (the tournament minted as an entity) |
| won an international tournament yesterday | `noop` (evidence) | "Nate has been winning a few gaming tournaments" (May, undated summary) |
| won a really big tournament last week | `noop` (evidence) | **"won his first video game tournament last week" (Jan)** |
| won the final of a big Valorant tournament | `noop` (evidence) | "Nate has been winning a few gaming tournaments" |

Three wins were folded into earlier facts as evidence rows. The October →
January merge is the decisive specimen: two claims whose resolved event
dates were nine months apart, judged the same fact because both say "last
week". The adjudicator's verdict prompt at that generation rendered exactly
two strings — `EXISTING` and `NEW` — and nothing about time, although both
claims carried resolved windows in the same table row the adjudicator read
`asserted_at` from.

**Reader — "At least five".** The answer agent made one `answer_context`
call with the raw question and received all seven win claims in the
testimony envelope (sessions D1, D10, D14, D17, D19, D22, D27 — every gold
turn) and, in the fact envelope, the four surviving win facts plus the
summary "has been winning a few". It answered "At least five" against seven.
The fact envelope's undercount is the plausible anchor; the run cannot prove
the reader's arithmetic either way, and it ran at the protocol's
reasoning-effort `none`.

## 3. The participation question: ten lineages into boilerplate

`conv-42/qa/0078` ("How many video game tournaments has Nate participated
in?", gold nine) was answered "Four". Its fact-layer counterpart is one
observation, "Nate is a participant.", with `evidence_count = 10`. Its
supporting claims:

- nine are the rendered session header — "Participants: Joanna and Nate",
  "Nate is a participant.", "Joanna and Nate are participants." — one per
  ingested session, source-faithful document framing;
- two are real, dated tournament entries: "currently participating in the
  video game tournament again" (2022-03-24) and "tried playing in the local
  Street Fighter tournament this time" (2022-04-25).

The boilerplate is harmless on its own. The defect is that two dated events
were absorbed into an undated statement that means "participant of this
conversation": semantic adjacency ("participating" ≈ "participant")
overrode content and time. The header claims themselves are outside this
analysis (they are what the source says; whether document framing should
seed observations at all is a separate question for E2 selection).

## 4. Where the fix belongs

Not extraction: the summary "has been winning a few" and the header lines are
source-faithful claims (D32), and every win reached the store dated. Not
identity: one entity. Not retrieval: every win reached the reader. The defect
is in the adjudicator's *inputs* — it decided sameness from two strings while
the temporal discriminator sat unread — and in a missing rule: a dated event
is not a re-assertion of anything undated, and two dated events on different
days are two events regardless of wording.

D106 adds that rule as the deterministic rung D43's design always named
before the model call, and shows both timelines in the prompt for the pairs
that still need judging. Replaying the seven wins through the new rung in the
test suite yields seven observations and buys no verdict for any
disjoint-window pair; the vague summary and the boilerplate state each keep
their own row.

## 5. What this does not establish

- No score. The run's judge did not execute, and 17 questions were never
  answered; the per-question reads above are the answer agent's generated
  answers against gold, not judged results.
- No claim that the temporal rung alone fixes the counting question. The
  reader saw seven wins in testimony and still undercounted; whether a
  complete fact layer changes its arithmetic is the next run's question.
- No change to observation `valid_from` (still the claim's `asserted_at`),
  to the E2 selection of document-framing claims, or to the reader's
  reasoning effort. Each is a separate decision.
