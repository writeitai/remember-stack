# Baseline answer policy with explicit calendar values

Non-binding analysis, 2026-11-09. The owner selected the original answer
policy and instruction placement, retaining only a general calendar-output
clarification. Previous broader candidates are not selected.

## Problem and evidence

The baseline can resolve a relative date internally yet answer with another
relative phrase. One complete development trial expressed the adoption answer
as a calendar range and received CORRECT. The exact anchored relative wording
also received different grades in two other complete trials, so neither the
judge nor a single trial proves a prompt effect. All four broader trials
scored 17/20; later instruction relocation coincided with a game regression.
The source store and all original answers, traces and grades remain preserved.
Evidence: cloud PR #850, `experiments/locomo-conv47-best-answer-20261109/`.

## Selected change and alternatives

Restore the baseline verbatim and insert only the four-line instruction in
its existing dates section: relative-time answers name calendar dates, ranges
or periods using the source timestamp, at the precision supported by the
source wording. It changes neither inference weighting nor plural scope and
does not relocate instructions. It supplies no dates, people, answer values,
reference IDs, week convention or category-specific execution. Template
SHA-256: `0b8945c8ac47c9d1558335cedaabe43a06e66a50e1fa0f69b444b3ca191bd7ed`.

Keeping the baseline unchanged preserves behavior but leaves final calendar
wording underspecified. The broader variants and instruction relocation had
no aggregate gain and exposed regressions. Changing the judge, reference
answers, model, retrieval ranking or engine would confound this focused test.
The suspicious tournament relation label needs a separate engine investigation;
this prompt change does not claim to repair it.

## Identity, cost, failure and validation

Only prompt/adapter bytes roll evaluation identity. Full-v40 remains the sole
current runtime profile; old runs and fingerprints are immutable. No ingestion
code, generation, D56 paid-work identity, model/provider, retrieval depth,
tool/call budget or judge changes. The instruction adds 44 whitespace-separated
words to model input; no new model calls or retrieval operations are required.
The existing no-fabrication, event-time and supported-precision rules apply.
A source timestamp anchors relative text; it is never an invented event date.

Run every selected question with the same source/model/tool settings, save
all traces, grades and usage independently, and verify immutable GCS archives.
The owner changed the campaign gate to conv-47 >=18/20, followed by conv-42
20/20 under this same prompt, before full publication. Inspect per-question
regressions as well as aggregate score. No score improvement is claimed before
those complete runs; this is development selection, not a held-out result.
