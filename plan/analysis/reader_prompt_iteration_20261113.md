# General reader reasoning: retained-data prompt trials

Non-binding analysis, 2026-11-13. This continues the source/reader fidelity
investigation and the accepted consumption contract. It does not establish an
accuracy improvement. The owner authorized Opus 5.5 HIGH to adjust the answer
prompt, with Codex operating the tests, benchmark runs and infrastructure.

The first full conv-43 replay of PR593 is finishing. Its result cannot improve
on the published 151/178 baseline; all 178 answers were generated without
answer/tool failures or database-budget deadlines. Seven previously diagnosed
reader errors received all cited original dialogue utterances in both runs.
Quote visibility does not by itself prove semantic sufficiency. Detailed frozen
results and raw source evidence live in the cloud benchmark workbooks/GCS, not
in the runtime prompt.

The experiment freezes the completed replay's incorrect item IDs as a development
cohort. A candidate should recover at least half of that cohort with the same
Luna Pro HIGH answerer and judge, judge prompt, retrieval settings and call
budgets. Then regenerate and judge all 178 questions with that exact candidate
prompt; count gains and regressions against the frozen replay. Development-only
results must never be presented as a new full-conversation score or combined
with the best answers from earlier trials.

Opus owns the wording and examples. They must express reusable memory reasoning:
complete relationship-specific lists, matching repeated events, indirect
references, ordinary-language interpretation and qualified correction of a
mistaken premise. The exact choices follow evidence inspection; this is not an
instruction to force a gold label when sources disagree. No conversation IDs,
turn IDs, benchmark names/answers or dataset-specific entities belong in runtime
rules or examples. Generic examples may illustrate the reusable distinction.
Keep the answer schema, tool contracts and budgets unchanged, and keep the
prompt within the existing 18,284-character size guard.

Codex runs and records each trial through the existing native `retrieval-ablation
--answer-prompt` option. It freezes the exact template bytes and SHA256 while
using the already ingested v45 store, its existing P3 projections and the attested
reader-serving revision. The prompt source is committed on latest main; its
commit/hash is recorded separately from the unchanged evaluator/serving source.
No old input is relabelled as v46 and no visual product is regenerated. The
merged visual-label change is outside this retained-data experiment.

Every candidate and coherent result checkpoint is committed and visible in a
PR. Codex runs validation, saves native answers/tool traces/judgments for every
trial to separate immutable output directories and verifies GCS copies. The
original run, baseline answers and processing counters stay unchanged. The
final all-question run is required before claiming that the miss reduction
holds across the conversation.

## Candidate 1 (Opus 5.5 HIGH): what it changes and why

This candidate was written from the initial diagnostic snapshot: 170 of 178
answers graded, 29 incorrect. The final miss cohort may differ. The candidate
has not been run, so this section predicts behaviour; it does not report a
result.

**What the misses showed.** The source text was present in every inspected
card, so most misses were reasoning failures, not retrieval failures. They fell
into recurring patterns:

- **Incomplete lists.** For "what has X read" or "which cities has X been
  to", the reader dropped items where the person showed experience only
  indirectly ("X is great", "a series I love", "one of my favorite cities to
  explore"). An author list missed an author that general knowledge derives from
  a listed title. A list of school stages stopped at high school although the
  same sentence went on to college.
- **Mixing up repeated episodes.** One person had two injuries, one reported
  as current ("this injury") and one described in retrospect ("last season").
  The reader merged them, then dated or counted the wrong one, and picked a book
  from another period as the one read "during the recovery".
- **Missed indirect references.** A later deal with an unnamed company was not
  linked to the brand the person had earlier said they hoped to work with. A
  shop suggestion ignored the specific creator whose work the person said they
  love.
- **Image inference over speech.** A team was inferred from a branded object
  in a photo instead of from the person's own spoken support. A meal was read
  from a photo instead of from "I make X a lot".
- **Wrong person in the question.** When the question names the wrong
  participant, the reader answered with unrelated content about the named
  person instead of saying who actually did it.
- **Literal titles reinterpreted.** A quoted title that looks like an
  ordinary word was replaced by a guessed title.

**What the candidate adds.** It adds one block, "Memory reasoning patterns", to
the answer prompt. Each pattern comes with a short invented example: complete
lists (including indirect experience), the requested kind and span, episodes,
indirect references, suggestions, direct statements first, wrong-person
premises, and verbatim names. A short pre-answer checklist at the end of the
prompt repeats these. The candidate does not force an answer that the sources
contradict. Six misses where the assessment found the reference itself in
conflict with the sources stay out of scope: a speaker swap where the named
person has their own answer, relative dates that point to another month, and
an answer that belongs to a different instrument. For a speaker swap, the
prompt only allows a short note that names the other participant. Questions
whose expected answer needs one specific outside-world title or organization,
plus two ambiguous counting or interval cases, are not targeted.

**What was cut to make room.** The candidate merges instructions that the
previous prompt repeated two or three times. These cover entity resolution, the
union-of-list rule, the preference and attachment-ownership checks, the feelings
and shared-attribute rules, and the end-of-prompt checklist. All wording pinned by
`test_locomo_protocol.py` is kept, and the template stays within the
18,284-character guard. The answer schema, tool contracts, budgets, models,
judge and retrieval settings are unchanged.

**Risks to watch in the full 178-question run:**

- Lists may grow longer and include doubtful items.
- The extra source query for a list's item class uses one more tool call.
- A preference shown in a photo may now lose to a weaker spoken mention.
- The wrong-person note may confuse the judge when the named person's own
  answer was the expected one.
- Merging repeated instructions may weaken behaviour that the repetition
  previously reinforced, mainly attachment ownership and adjacent-chunk
  expansion.

**Calibration revision, made before the first run.** A self-check found three
places where candidate 1 claimed more than the evidence supports. All three are
now fixed:

- **Praise alone does not prove experience.** A bare "X is great" now counts
  only when the context shows first-hand contact, for example the person
  describes the content or compares it with other books they have read. Hearsay,
  wishes and plans do not count. Items with weaker support are marked
  "(likely)".
- **Two injury reports can be one injury.** A current report and a report from
  a clearly earlier time count as separate occurrences unless the source says it
  is the same ongoing injury ("still hurts"). A recurrence ("hurt it again")
  counts as another occurrence. The injury count in the example is qualified as
  "likely" because the body part of the current injury comes only from the
  photo.
- **"During the recovery" needs dated support.** The example now gives a
  "fully healed" date, so the book read while recovering has a recovery window
  to fall into. The earlier example picked that book without showing the
  recovery was still ongoing.

The template is now 18,277 characters.
