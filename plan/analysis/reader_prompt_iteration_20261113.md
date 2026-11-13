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
reader-serving revision. Each prompt candidate is committed on the feature
branch of PR601. That branch was based on main at `3633134bf` when the
experiment was created, and PR601 is not merged. The prompt commit and hash are
recorded separately from the unchanged evaluator/serving source.
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

## Candidate 2: diagnosis of trial 1 and changes

**Trial 1 result (measured by Codex).** Candidate 1 answered 7 of the 29 frozen
misses correctly. There were no answer, judge or tool failures and no database
deadlines. This counts only the development cohort and is not a
full-conversation score. The diagnosis below comes from the projected reader
trace of each item, which keeps the content the reader saw and omits only rank
and debug bookkeeping.

**Why the remaining fixable misses failed:**

- **Books and authors.** Both books the answers omitted were in the reader's
  trace. In one, the person praises a book and compares it with another
  series, in reply to a partner who says the book is "one of my favorites too".
  Candidate 1's calibration ("a bare 'X is great' counts only when...") made
  the reader treat this as not proven, and it dropped the item instead of
  qualifying it. The other book was mentioned only as "a show based on a book
  series that I love". The prompt line "liking an adaptation is not liking the
  original work" could be read as excluding it, although the statement is
  explicitly about the books. The reader's queries also asked only for books the
  person "explicitly says he read, finished", which favours the literal verb.
- **"What sports does he like".** The decisive utterance was never retrieved.
  The reader's queries listed guessed sports by name, which steered retrieval
  away from the real activity. Only weaker signals were in the trace: the
  person saying "I love the ocean", plus a surfboard caption.
- **School stages.** The trace contained "kept playing through middle and high
  school before earning a college scholarship". The reader did not upgrade the
  scholarship to playing in college, which is correct, but it then dropped the
  stage entirely instead of keeping it with the source's own qualifier.
- **Supported team.** The wrong-person rule fired too broadly. One speaker
  said a team "is my team" and the named person replied that the team was
  solid. The reader answered that the other speaker supports the team and the
  named person "only said they were solid". Agreement in reply is the
  replier's own attitude.
- **Year-restricted injury date.** The reader chose a retrospective "last
  season" injury. A current injury reported in the requested year was visible
  in the trace.
- **Quoted placeholder title.** The reader again replaced a quoted title that
  looks like an ordinary word with a guess. The candidate 1 rule did not say to
  give the quoted text first.

**What candidate 2 changes:**

- **Relationship-specific lists.** "Likes" covers anything described with
  enjoyment, while "has read/visited" needs first-hand contact. List queries
  are written in plain class and relationship words, not guessed names or a
  single literal verb. Implied contact counts, including praise from someone
  who knows the work, a partner's "favorite too", and a statement about the
  books behind a screen version. A reply such as "I don't ski, but..." reveals
  the other speaker's activity. Supported items are kept with an affirmative
  qualifier, never dropped and never overstated.
- **Adaptations.** The rule now excludes only liking the adaptation alone.
- **Direct statements.** A spoken statement still beats an image inference
  when the two conflict, but it does not remove compatible items from a list.
- **Wrong-person premises.** The rule applies only when the named person has
  no statement of their own on the subject. Agreement in reply counts as the
  replier's attitude.
- **Episodes.** "In <year>" prefers the episode reported in that year over a
  retrospective one of uncertain date.
- **Verbatim titles.** A quoted title is given first, exactly as written.
- **Suggestions.** When memory names no option, common real options from
  general knowledge may be named. Earlier named candidates still win over
  outside guesses. A future role is a concrete occupation.

To make room, the precision definitions and the end-of-prompt checklist were
compressed. The template is about 18,280 characters, within the
18,284-character guard.

**Not targeted.** Reference conflicts and wrong-speaker references, the
interval and count disagreements, and suggestions whose expected answer is one
specific real title or organization are not targeted. Nothing here forces a
reference that conflicts with the sources.

**Risks:**

- The broader "likes" scope and "(likely)" items may lengthen list answers and
  bring in weak items.
- Applying the wrong-person rule more narrowly may lose one of trial 1's
  recoveries.
- Naming options from general knowledge may push some answers away from the
  conversation's own candidates.

**Candidate 2 calibration, made before the first run.** Three new rules
claimed more than ordinary conversation supports, so all three were narrowed:

- **Negative self-disclosure.** "I don't ski, but..." credits the partner with
  the activity only when the partner's own preceding words show it, for example
  "I ski every winter". After a plain question ("Do you ski?") it says nothing
  about the partner.
- **Club support.** Agreement in reply counts as the replier's likely
  preference only inside a talk about which team each person supports. General
  praise of a club ("X played well") is not affiliation.
- **Year-restricted episodes.** "In <year>" prefers an episode described as
  current ("this injury") when reported in that year. A retrospective episode
  is dated by its stated event time, never by when it was mentioned.

To stay within the 18,284-character guard, the attachment-description and
limitations paragraphs were reworded more tightly. Their rules are unchanged.

## Candidate 3: diagnosis of trial 2 and changes

**Trial 2 result (measured by Codex).** Candidate 2 answered 8 of the 29 frozen
misses correctly. It newly fixed four items, lost three that candidate 1 had
recovered, and kept five recoveries. There were no execution errors. This
counts only the development cohort.

**Diagnosis from the projected traces.** For the remaining misses, the needed
chunks were almost always retrieved. The failures happened when the answer
was assembled:

- **Answers are built from extracted claim rows, so facts that appear only in
  chunk text are lost.** The book praised in a reply ("X is great, but have you
  read Y? Also awesome", after a partner's "one of my favorites too") was in the
  claims-and-sources response in both list questions. It exists only in chunk
  text, with no claim row, and the answers omitted it although the prompt's own
  example described this exact pattern. Likewise, the current injury's claim
  rows never name the body part; only the same-turn photo caption and its search
  text do. The retrospective injury's claim names it explicitly, so the reader
  kept choosing the retrospective one.
- **Query wording still narrows retrieval.** Queries kept using literal verbs
  ("read, finished") and guessed instances (named sports). One query even built
  the expected school stages into its wording. The reader typically stopped
  after 3–4 of its 8 calls. A rule buried inside a long catalogue of patterns
  did not change these habits.
- **Strong implication was still dropped.** "Earned a college scholarship" and
  "after college, I was drafted" were both claim rows, yet the answer omitted
  college. The qualified-label rule existed but was not applied.
- **The narrowed wrong-person rule misfires.** The call question correctly
  said the other participant made the call, then answered with an unrelated
  statement by the named person (a game report). The narrowing ("only when the
  named person has no statement of their own on the subject") let any
  statement by the named person count as being "on the subject". The question's
  event should define the subject.
- **Constraints split across items.** The slow-cooker question has two
  answers that each satisfy one constraint. A photographed slow-cooker dish
  matches "slow cooker", and a stated dish made "a lot" (photographed on a
  stove) matches "often". Candidate 1 reported both and was graded correct;
  candidate 2 chose the photo and was graded wrong. In the recovery-book
  question, both books fall inside the recovery window: one finished days after
  the injury, the other read while the person still reported recovering. The
  source supports giving both with dates rather than choosing one.
- **Source consistency of the new wins.**
  - "At least five" games matches the five distinct wins in the sources.
  - The verbatim quoted title is source-consistent.
  - Team support ("likely; called them solid", in a team-support exchange) is
    source-consistent.
  - The favorite-series win is weakly supported. The only evidence for that
    person is being "a fan too" of the film adaptation, and the series is
    elsewhere explicitly the other speaker's favorite. Treat it as a probable
    judge-lenient pass, not a source-backed recovery.

**What candidate 3 changes.** The catalogue of separate pattern sections is
replaced by one ordered answer procedure:

1. **Frame the question:** relation, constraints and the kind of value.
2. **Search** in plain class words. Scan the chunk text and captions, not
   only claim rows, because claim rows omit passing mentions, reply-only items
   and attachment details. Spend unused calls on unclear candidates.
3. **Decide each candidate:** include it, include it with a qualifier, or
   exclude it. The union keeps every qualified item.
4. **Fit episodes and split constraints:** give each item with the constraint
   it meets or its date instead of silently choosing.
5. **Owner and premise:** the question's event defines the subject. The
   answer switches to another participant only when the described event
   uniquely matches that participant. When several participants have matching
   generic events, the named person is kept and answered from their own
   statements.
6. **Infer:** earlier named candidates first, then grounded suggestions or
   general-knowledge options.
7. **Write:** the end checklist now asks whether every candidate seen in
   chunk text was decided.

After the final calibration (the "chunk" terminology and the unique-match
condition in the owner step), the template is 17,818 characters. That is
shorter than candidate 2 because repeated wording was removed.

**Realistic limits.**

- The sports question depends on retrieving an utterance that no trial has
  returned so far.
- Reference-conflict items stay out of scope.
- The suggestion questions with one specific expected option, the
  interval/count disagreements and the start-date question stay out of scope.
- Between candidate 1 and candidate 2, four items were gained and three lost.
  These are observed gains and regressions between two different prompts. They
  mix the effect of the prompt change with run-to-run sampling, so they do not
  measure noise. A variance estimate needs repeated runs of the same prompt.

**Risks.**

- Scanning chunk text may surface more peripheral items and lengthen
  answers.
- Reporting split constraints may read as hedging to the judge on questions
  that have one clear answer.
- A procedural format may reduce the attention given to the older specialised
  sections (preferences, attachments, limitations).

## Candidate 4: diagnosis of trials 1–3 and changes

**Trial 3 result (measured by Codex).** Candidate 3 answered 7 of the 29
frozen misses correctly. All 29 answers and grades completed with no execution
errors, and the source bytes and ingestion counters were unchanged. Trial 1
also scored 7 and trial 2 scored 8, but each trial recovered a different set
of items. Across the three trials, 13 different items were answered correctly at
least once. One of them, the favorite-series pass in trial 2, is a probable
lenient judge pass (see the candidate 3 section), so 12 are source-backed. No
single prompt held all 12. Flips between trials mix changed behaviour with
run-to-run sampling, so they are not a noise estimate.

**Method.** Each remaining miss was checked in the trial 3 projected trace.
The check looked at the fact rows, claim rows and chunk text the reader actually
received, and compared them with the original dialogue turns in the reader
cards. Candidate 3 was also compared line by line with candidates 1 and 2 for
the items it lost.

**Finding 1: candidate 3's consolidation dropped three distinctions, and these
match its three regressions.** Every needed row was in the trace for all three.

- *Indirect brand link.* Candidate 1 said to prefer an earlier wished-for
  candidate "over ... an entity already tied to a different event"; candidate 3
  kept only the wish-to-deal link. The trace held both a fact row saying the
  person "had just signed [an earlier brand] up for a ... gear deal" and the
  earlier wish to work with another brand. The reader picked the
  already-signed brand, whose wording repeats the question's "signed up".
- *Frequency matched by paraphrase.* Candidate 1 said "often cooks" = "I make
  X a lot". Candidate 3 kept only the split-constraint rule. The fact row "he
  often makes [a dish]" was at rank 6 in the facts response, yet the reader
  answered with the dish in the photo, because only the photo matched the
  question's word "slow cooker". Candidate 1 gave both dishes and was graded
  correct.
- *A plan inside a visit statement.* The statement was "I'm stoked for my
  game there next month! It's one of my favorite cities to explore". It was the
  second-ranked fact row. The reader's own query said "excluding planned or
  future trips", and it dropped the city because of the plan. Candidate 1 had a
  worked list example. Candidate 3 had only the phrase in a list and also the
  "exclude: ... plans" bullet, which pulled the other way.

**Finding 2: wording overlap beats meaning, whether the row is a fact or
chunk text.** We tested the hypothesis that fact authority suppresses
source-based implications. It does not explain the misses on its own. In every
lost item the reader did query claims_and_sources_context, and the decisive
chunk was returned. What decided the answer was which candidate repeated the
question's words. The photographed dish won over a stated frequent dish (a
fact row). The retrospective "hurt my ankle last season" won over the current
injury, whose body part appears only in the image search text. The stated
foundation plan won over the mentoring and seminar activities that point to a
concrete occupation. Facts stay authoritative for whether a belief holds. The
fix is to decide candidates by the question's meaning and constraints.

**Finding 3: query wording does over-weight an excluded topic (verified).** For
"what sports does he like besides basketball", the trial 2 and 3 queries
contained "besides basketball". In the claims_and_sources responses, 12 to 15
of the top 15 chunks mentioned basketball. The turn where the person says they
spent "an awesome summer ... surfing" was never returned in any trial.

**Finding 4: a promising clue was cut off at a chunk boundary (verified).**
The same queries did return the chunk with "I love the ocean" and its
surfboard caption. That chunk ends right at the start of the attachment
reading. The partner's next question ("favorite beach memory?") and the
surfing answer follow in the next chunk. One adjacent_chunks read on that chunk
would very likely have reached the surfing turn. The trial 3 reader instead
spent its adjacent read on a yoga chunk. Whether candidate 4 retrieves the
turn is a prediction, not a measurement.

**Finding 5: other single-item causes.**

- *Author list.* The bookshelf reply "The Hobbit is great, but have you read
  that other ... series?" was in the trace twice. The book-list question in the
  same trial included this book, but the author question did not. That reader
  built its list from claim rows that name authors and never enumerated the
  books first.
- *Wrong-person premise.* The reader said correctly that the other
  participant made the Skype call. It then led with the named person's
  unrelated reply (a game report) as the answer.

**What candidate 4 changes.** It keeps candidate 3's ordered procedure, which
produced that trial's new wins (book list, school stages, quoted title, team
support, recovery book). It changes only the following:

- **Retrieval roles (hierarchy 1.c).** claims_and_sources_context is also
  queried for lists, episodes, implications and suggestions, because chunk text
  holds items no fact states. A compatible source detail adds information; a
  fact still decides whether an established belief holds.
- **Frame.** "New versus already done" is now a constraint. An attribute of
  list items ("which authors") first needs the full item list, decided with the
  step 3 rules.
- **Search.** An excluded topic is left out of the query, with an invented
  example. When a chunk ends at a promising clue, the reader reads its
  continuation with adjacent_chunks.
- **Decide.** Candidates are matched by the question's meaning, not its words
  ("often cooks" = "I make X a lot"). Each clue in one statement is decided
  separately: a plan does not cancel a sign of past contact (invented "City Q"
  example).
- **Episodes.** A row that repeats the question's words may describe another
  episode (deals are added to the episode kinds). The unnamed "this" may be
  identified from image search text as well as the caption. "When no single item
  meets every constraint" replaces "when different items meet different
  constraints".
- **Owner.** After a premise correction, the answer must never be the named
  person's reply or another statement of theirs.
- **Infer.** An unnamed later counterpart links to an earlier candidate that
  was still a wish or plan, not one already tied to an earlier event (invented
  Brand P/Q example). For a future role, give the stated plan and the
  concrete occupation that current activities point to. This moves the chef
  example from step 1 to here.
- **Checklist.** The episode and owner must match the question's meaning and
  constraints, not only its words.

**What was removed to stay within the guard.** The "I don't ski, but..." guard
was removed. The rule it calibrated was already removed in candidate 3, and the
sports miss turned out to be a retrieval problem, not that dialogue pattern. One
duplicate adjacent-chunks sentence in the closing paragraph was also removed,
and the reply-interpretation, preference, entity-resolution and source-fallback
wording was tightened. These rules are otherwise unchanged. The template is
18,279 characters before the calibration below, within the 18,284 guard. All five placeholders and the
contract phrases pinned by `test_locomo_protocol.py` are kept, by static check
only; Codex runs the tests.

**Faithfulness caveat.** The Brand P/Q and City Q examples are invented, but
they are close analogues of two cohort items. A recovery of those two items
on the 29-item cohort therefore overstates how well the rule generalises. The
full 178-question run is the real test.

**Recovery ceiling on the 29-item cohort.** Each item is classed by what the
fixed sources and judge allow:

| Class | Items | Count |
|---|---|---|
| Recovered in trial 3; rules kept | 0004, 0029, 0049, 0050, 0067, 0113, 0134 | 7 |
| Recovered earlier and lost in trial 3; sources support the answer | 0008, 0030, 0128 (both dishes), 0086 (explicit premise correction) | 4 |
| Never recovered; sources support the answer | 0018 (author via the bookshelf reply), 0011 (needs retrieval of the surfing turn) | 2 |
| Ambiguous: the source supports more than one reasonable answer, or the answer depends on the judge | 0034 (coaching vs the stated foundation plan), 0005 (creator's shop vs a generic themed store), 0017 (gold 6 cites 5 events), 0053 (new exercises from general knowledge), 0048 (the current injury is an ankle only per image search text; the image reading says "likely an arm") | 5 |
| Not recoverable faithfully | 0006, 0060, 0016 (relative-date arithmetic conflicts with the source); 0056 (another instrument); 0055 (signing with a new team is not the start of professional play); 0135, 0164, 0166 (the gold answer belongs to the other speaker, while the named person has their own answer); 0019, 0066, 0051 (one specific outside-world option the conversation never names) | 11 |

The faithful maximum is therefore 13 (7 + 4 + 2), plus whatever the five
ambiguous items yield. **15 is achievable only if every source-backed item
lands in the same run and at least two ambiguous items pass.** No trial has
held more than 8. Per-item outcomes changed between trials, but each trial
used a different prompt, so those flips do not measure sampling. A
realistic single-run expectation for candidate 4 is about 10–13. If it falls
short of 15, the gap should be reported as the ceiling above, not closed with
gold-shaped rules. The confirmation target follows the same arithmetic. With
11 cohort items unrecoverable, 164/178 requires at least 15 net recoveries and
no regressions among the 149 previously correct answers, which is equally
tight.

**Risks.**

- Leaving the excluded topic out of queries may drop useful context when the
  topic is part of the wanted class.
- The continuation read for cut-off clues uses one of the 8 tool calls.
- "A plan does not cancel past contact" may add weak "(likely)" items to
  visit lists.
- The rule against already-linked candidates can misfire when a later deal
  really is a renewal with the same brand.
- Giving both a stated plan and an occupation for future roles, and one item
  per constraint, lengthens answers. A strict judge may read this as hedging.
- Removing the "I don't ski" guard lets the reader credit an activity from a
  partner's negative reply again. The general rule against crediting another
  person's item still applies.

**Candidate 4 calibration, made before the first run.** The owner reviewed
the candidate and asked for four narrow corrections. Each one stops a rule
from claiming more than the evidence supports:

- **Excluded topics in queries.** The first example replaced "sports besides
  tennis" with "outdoor activities and hobbies". That added an "outdoor"
  restriction that nothing in the question supports. The rule now says to query
  the broad class without the excluded topic ("books besides novels" -> "books
  he has read"). A narrower feature is added only when clues that were already
  retrieved suggest it.
- **Earlier wish versus earlier deal.** This is a preference among
  candidates, not a ban on an old one. A stated renewal, repeat or continuation
  links to the earlier counterpart. Otherwise a still-open wish or plan is
  preferred over a counterpart already tied to an earlier event.
- **Image search text.** The dataset's image search tags are derived hints,
  weaker than the image reading (vision description and OCR), the caption or
  the dialogue. A tag may help identify a candidate, with a qualifier, but never
  overrides a conflicting observation. The injury-date item is affected: its
  tag says "ankle" while the image reading says "likely an arm", so it moves
  from source-supported to ambiguous in the ceiling table above. The
  injury-count item, already recovered, also partly rests on a tag. A later
  boot-on-foot image reading still supports it, but it is less safe than before.
- **Wrong-person premise.** Only unrelated replies or statements by the
  named person are excluded. If the question asks about a response inside the
  matching event, that response is given with its actual actor and role.

The descriptive phrase "source-passage" in the direct-primitives line now reads
"source chunk", the canonical term; no tool name or tested phrase changes. To
stay within the guard, wording was tightened in several places without changing
any rule:

- the answer-format lead sentence;
- the attachment-ownership sentence;
- the step 2 sentence about spending unused calls;
- the definition of the open valid_precision value.

Two duplicated clauses were removed. One was the "planned versus completed"
sentence in step 3, which the exclude list and the plan rule already cover. The
other was the checklist clause on preference searches, which the preference
paragraph still requires. The template is 18,244 characters. All five
placeholders and every pinned contract phrase are present, by static check
only.

## Candidate 5: diagnosis of trial 4 and changes

**Trial 4 result (measured by Codex).** Candidate 4 (as calibrated) answered
11 of the 29 frozen misses correctly. There were no execution errors or
deadlines, and the source and ingestion were unchanged. Newly or again
correct: the indirect brand link, surfing, the author list, the cities, the
injury count, the recovery book, the Skype premise correction, team support,
the slow-cooker meal, the quoted title, and the favorite-series item. The
favorite-series pass is again a probable wrong-source pass. The answer gave a
series to the named person whose evidence is film fandom and an unresolved
"that other popular fantasy series". The raw favorite-book statement belongs to
the other speaker. So 10 of the 11 passes are source-consistent. Three
supported wins from trial 3 were lost: the book list, the school stages and
the travel hobby. This is a development-cohort count only, not a full score.

**Diagnosis from the trial 4 projected traces.**

- **In all three reversions the evidence was visible and the answer omitted
  it.** Every needed row was in the trace, so retrieval does not explain them.
  - *Book list.* Both claims_and_sources responses contained the bookshelf
    reply ("X is great, but have you read ...") in chunk text. The facts
    response held the fact row "based on a book series that he loves". The
    answer omitted both. Trial 3, which kept them, wrote them as a trailing
    "Likely also ...". The trial 4 reader's last query again used literal verbs
    ("finished, completed").
  - *School stages.* The top evidence row was one statement: "through middle
    and high school before earning a college scholarship". A second row said
    "after college, I was drafted". The answer kept the clause that repeats the
    question's words and dropped the later compatible clause. Trial 3 wrote
    "then likely college".
  - *Travel hobby.* Facts about the person's writing were in both trials:
    articles for a magazine, writing a novel, "writing could create a whole new
    world". Trial 3 joined that interest to the travel goal. Trial 4 named a
    generic option (travel photography) that no evidence grounds.
  - **What this does and does not establish.** The traces show that the
    evidence was visible and omitted. They do not show why. Trials 3 and 4
    used different prompts, so the difference between them cannot be
    attributed to sampling, to the answer format or to any single wording
    change. The candidate 5 changes below are proposed remedies. They have not
    been tested.
- **Open recommendations repeat what is stored (verified).** For "what could
  he do after his career", the answer listed the stated foundation plan and
  the current mentoring and seminars. It never named a role. For "what other
  exercises can help", it repeated the person's existing strength training
  and yoga, although step 1 said "other" wants new options.
- **The count missed episodes (verified against the source turns, not the
  gold count).** The trace held these reported wins, each with a resolvable
  date:
  - a July game;
  - an August game;
  - a win over a top team, reported on Dec 8;
  - a close win "last week", reported on Dec 16, so in the week of Dec 9–15;
  - a comeback from "last year";
  - a favorite buzzer-beater after being down 10 in the 4th quarter. This may
    be the same game as the comeback.

  Trophy reports in October and December may be tournament wins rather than
  single games. The answer said "at least four; possibly five". It treated
  the two December wins as possibly one game, although their supported event
  windows are disjoint: the first was before Dec 8, the second in Dec 9–15. It
  also left out the retrospective games. A source-faithful answer is about five or six. The
  sources do not pin down six.
- **Ownership.** The Skype item is a uniquely identifiable event, so the
  labeled correction ("Tim (not John) skyped ...") is legitimate. A favorite
  series is a generic preference. It must not move to whoever's wording says
  "favorite" more literally. An unresolved "that other series" must not be
  named from another person's favorite, and film fandom is not a book
  preference.

**What candidate 5 changes.** It starts from calibrated candidate 4 and keeps
every retrieval rule that produced the trial 4 wins: broad positive queries,
continuation reads, meaning-over-words matching and the wish-versus-deal
preference.

- **Write (step 7).** A list gives the direct items, then "likely also ..."
  for the qualified ones, including later clauses of a kept statement. This is
  an untested remedy for the omitted items. It gives qualified items an
  explicit place in the answer.
- **Episodes (step 4).** Retrospective episodes are kept. Episodes are
  compared by each event's supported time window, not by when they were
  reported. Discrete events with disjoint windows are different. Stated
  continuity ("that game", "still hurts"), overlapping or uncertain windows,
  or one multi-day episode (a trip, an ongoing injury) can be one. This
  window rule replaces the earlier sentence that separated a current report
  from a clearly earlier one; the knee example still shows that case.
- **Infer (step 6).**
  - A suggestion carries the person's strongest established skill or
    interest into the goal in a new setting, format or role. The painter who
    longs for the coast still paints, but outdoors.
  - For "other", "new" or "pick up", current routines are seeds and
    constraints, not the answer. The reader proposes something the person
    does not already do in that form, using general knowledge. The old clause
    "when memory offers nothing fitting, name common real options" was
    removed, because it could be read as allowing general knowledge only when
    memory has nothing. This rule moved here from step 1, so it is no longer
    stated twice.
  - A future role is named as a role, not as a list of current activities; a
    stated plan may follow briefly.
- **Ownership (step 5 and the preference paragraph).** Matching generic
  preferences, like matching generic events, keep the named person. An unnamed
  item ("that other series") is named only from its own exchange, never from
  another person's favorite.

**What was trimmed to fit.** The following was shortened, with no rule
change:

- the open-query line;
- the duplicate current/at/overlap sentence in 1.b (the temporal block keeps
  the exact time filters);
- the "do not leave 'last week' unresolved" clause (the sentence before it
  already requires explicit dates);
- the feelings scope sentence (the answer policy already preserves the
  question's person and time);
- the adjacent-boundary sentence;
- the separate current-versus-earlier-report sentence in step 4 (now covered
  by the event-window rule and the knee example);
- the verbatim-terms and deductive examples;
- the answer-format lead sentence.

The procedure paragraphs were also reflowed; that changes whitespace only.
The template is 18,281 characters. All five placeholders and every pinned
contract phrase are present, by static check only.

**Calibrated ceiling.** These counts use the candidate 4 calibration table,
with two items reclassified from the trial 4 evidence:

- The count item moves from ambiguous to source-supported, because the dated
  source episodes support "about five or six" without using the gold count.
- The favorite-series item stays among the 11 out-of-scope items, even though
  it passed in trials 2 and 4.

Source-consistent, recoverable items:

- the 7 trial 3 wins (book list, school stages, injury count, recovery book,
  travel hobby, team support, quoted title);
- the 4 items lost in trial 3 and regained in trial 4 (brand link, cities,
  slow cooker, Skype correction);
- the author list and surfing;
- the count.

That makes 14. Four items remain ambiguous: the future role, the shop
suggestion, the new exercises and the injury date. Each passes only if the
judge accepts a grounded proposal or a qualified date. **The legitimate ceiling
is therefore 14 to 18.** A single run should be expected to land a few below
14, because this many qualified items have never all held at once. No trial so
far has kept every earlier source-consistent win.

Fixing the ownership rule will probably cost the favorite-series pass. That
loss is correct, and it should be reported as such rather than counted as a
regression. If candidate 5 scores, for example, 12 with the favorite series
wrong, that is a better source-consistent result than 11 with it right.

**Risks.**

- The "likely also" list shape may add weak items to lists that have one
  clear answer.
- Requiring new options for "other" may push answers toward general-knowledge
  proposals that a strict judge reads as unsupported. A proposal is a
  suggestion, not a claimed personal fact.
- Naming a role rather than listing activities may drop a stated plan the
  judge expected.
- The event-window rule needs the reader to resolve each event's window.
  If it resolves windows loosely, it may still merge separate games or split
  one trip.
- Reflowing the procedure gives the reader the same words with different line
  breaks. It should not change behaviour, but it is not byte-identical to
  candidate 4.

**Candidate 5 calibration, made before the first run.** The owner reviewed
the candidate and asked for three runtime corrections and one correction to
this analysis:

- Different reported dates no longer imply different episodes. Event time
  windows decide.
- A new option may carry an existing strength into a new setting, format or
  role.
- For "other" or "new" questions, ordinary general knowledge may supply a
  new option even when memory holds related routines.
- This analysis no longer claims that sampling or the answer format caused
  the omissions.

The step 4 and step 6 changes described above already include these
calibrations.

## Candidate 6: a single-variable layout experiment

**Trial 5 status (reported by Codex, not final).** Candidate 5 (commit
`a93578a06`) has generated all 29 answers. At least 21 are graded, and 5 of
those are correct. The trial cannot reach the 15-item target even before
grading finishes. Codex will record the final tally and keep the GCS outputs.
One technical interruption, a JSON-decoding failure, was resumed through the
native resume path. The 22 answers already completed stayed byte-identical.
That interruption is an execution matter and does not bear on the prompt's
semantic rules.

**Why a layout experiment instead of more rules.** Candidates 3, 4 and 5 each
changed several rules at once. Their per-item outcomes moved in both
directions, and no change can be credited or blamed on its own. Adding
another tailored rule would repeat that problem. Candidate 6 instead changes
exactly one thing, where the answer procedure sits. A difference in outcome
can then be read against a single variable. This comparison is still one run
per prompt. It does not separate the effect of the change from run-to-run
variation, and this note does not estimate that variation.

**Hypothesis (untested).** The ordered "Answer procedure (examples are
invented)" block, steps 1–7, holds the concrete rules for lists, qualified
items, episodes, ownership and proposals. In candidates 3–5 it sits in the
middle of the prompt. The dynamic tool trace comes after it and is often very
long. By the time the model writes its answer, those rules may get too little
attention compared with the trace and the short checklist that follows it.
Moving the block to just after the trace places it next to the point where
the answer is written. This is an attention and placement hypothesis. The
trial 4 traces show that evidence was visible and then omitted. They do not
show that placement caused the omissions.

**What candidate 6 changes.** Only the position of one block inside
`ANSWER_AGENT_PROMPT_TEMPLATE` changes:

- **Before:**
  - output-format paragraph, ending `{answer_word_cap_instruction}`;
  - the Answer procedure block;
  - "For hypothetical or counterfactual questions …" through
    `TOOL TRACE SO FAR: {trace}` and `{guard_feedback}`;
  - "Before finalizing, check: …".
- **After:**
  - output-format paragraph, ending `{answer_word_cap_instruction}`;
  - "For hypothetical or counterfactual questions …" through
    `TOOL TRACE SO FAR: {trace}` and `{guard_feedback}`;
  - the Answer procedure block;
  - "Before finalizing, check: …".

The block, 5,895 characters, moved with its single blank-line separator. Its
wording, examples and internal line breaks are unchanged. Everything outside
the block is unchanged and stays in its original order. Static checks show
that the template length (18,281 characters) and its multiset of characters
are identical to candidate 5. The five placeholders keep their order:

- `{answer_word_cap_instruction}`
- `{tools}`
- `{trace}`
- `{guard_feedback}`
- `{question}`

The block contains no placeholder. All pinned contract phrases are present.
Template SHA256:

- candidate 5: `5155a4ffaf083dd6059aa9ab490fb7664598ffa8c661e80ec878dacf221ada24`
- candidate 6: `a9e83a5a314d1e084e971ea7a10ca7df6ee6c21f3aee7c2451eb85bcea1b0d4e`

Tests are run by Codex. Models, judge, data, retrieval
pool, tool budgets and the eight workers are unchanged.

**How to read the result.**

- If candidate 6 beats candidate 5 on the same 29 items, the gain fits the
  placement hypothesis. A single run does not prove it, and the full
  178-question run would still be needed.
- If the two prompts score about the same or candidate 6 scores lower,
  placement is unlikely to be the main constraint for these items.

**Risks.**

- The procedure now appears after the tool catalogue and the trace. On the
  first step the trace is empty, so the model may plan its retrieval with
  less attention on step 2's search guidance than before.
- The paragraphs that stayed in place (preferences, attachments, verbatim
  terms, limitations, feelings, shared attributes, deductive questions) now
  come before the procedure. They no longer follow it. That can change how
  the model weighs them.
- The procedure now sits directly before the end checklist and the
  adjacent-chunks paragraph. Together they form a long block of instructions
  after the trace, which may dilute the checklist.
- Any score change also includes run-to-run variation, which this design
  does not measure.
