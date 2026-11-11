# Reader tool-catalog projection and answer attention

Non-binding analysis, 2026-11-11. The owner authorized small, general reader
changes, tested on two development questions before a fresh complete conv42
regression. Every candidate must be published before paid testing. The source
store, judge wording and ingestion/reuse generations stay fixed.

The first four prompt-only candidates scored0/2 with Pro HIGH reader and judge.
Their raw source traces include the missing restriction and image subject, but
the final output drops details, adds an unsupported category contrast, or mixes
work media. Trial05 is still running; no score improvement is established.
All prior outcomes and six-archive GCS readbacks are in cloud PR959,
`experiments/locomo-targeted-development-20261111/`. This is development tuning,
not held-out validation or proof that the engine lacks the source evidence.

The ordinary full reader renders all22 ToolDescriptor records on every step.
Each record includes its complete result JSON Schema, often repeating the same
large response-envelope definition. An empty trace rendered165157 characters;
the two saved final traces rendered375700 and392997 characters. The existing
MCP tool shape omits output schemas and reduces these to25476,236019 and253316
characters. These are measured character counts, not tokenizer measurements or
an attribution of the observed answer failures.

Chosen candidate: omit only `result_schema` from the ordinary reader's displayed
catalog. Keep name, description, full input schema, result contract, grain,
answer intent and other descriptor metadata. The durable tool catalog and hash,
public API/MCP schema, runtime validation and every retrieved response stay
unchanged. This avoids a separate tool catalog, hand-written response summaries,
lossy evidence selection, new ranking model or additional model call. The
reader still sees actual typed response fields in its trace and the existing
instruction to respect freshness, negatives, truncation and attribution.

The alternative of retaining the full schemas preserves maximum advance type
information but repeats a large description the reader does not need to call a
tool. Switching wholesale to the MCP shape also discards useful contract/grain
metadata. Filtering retrieved evidence could save more input but would change
what the reader is allowed to consider and introduce a ranking dependency; it
is outside this small experiment. Re-ingestion is expressly excluded.

Risk: omitting an output schema may make a field unfamiliar before its first
use. Tool descriptions, result contracts and actual trace fields remain the
source of those semantics. This is a benchmark reader projection, not removal
of public schemas or engine validation. Offline checks must prove that all tool
inputs and contracts survive, that output schemas leave only the rendered
catalog, and that retrieved evidence survives unchanged. Scores, latency and
actual usage must be measured separately; a smaller prompt alone proves none
of them improved.

The changed rendering is recorded by the single current Full-v45 adapter
identity; Full-v44 is retired from executable configuration, while recorded
artifacts remain readable. No ingestion key, provider/model default, retrieval
pool, permission, judge prompt, or tool budget changes. Explicit prompt-override
experiments also record the immutable runner revision and template hash. Keep
all failed trials and use a fresh complete20-question arm only after both
targeted questions pass. Do not merge this candidate on a size reduction alone.

Offline projection check for candidate23ab8c81c: the ordinary empty-trace prompt
is29324 characters, and the same two final traces are239867 and257164
characters. This removes135833 repeated catalog characters while retaining
more descriptor metadata than the MCP shape. All394 benchmark tests, Ruff,
docs truth/generated-doc checks and targeted Pyright pass. These checks do not
establish an accuracy gain; paid comparison is still pending.

## Source-qualified answer template in the same draft

Prompt-only trial05 (SHA256
`065f61b33211537404a5fbc6bb0da3d25f0d8ba2bc397816f5d35ebc53e274d0`)
scored1/2 with Pro HIGH for both roles. Its concrete restriction answer includes
the related item under its precise source label and is graded CORRECT. The book
answer includes the pictured subject and a competing recommendation but remains
WRONG. This does not establish that the second answer lacks evidence or that
the judge is necessarily incorrect. Cloud PR959 retains the unmodified result.

The general template interprets short personal-memory questions by everyday
intent, keeping neighboring source labels parenthetically instead of upgrading
them or adding unsupported negative classifications. It checks object kind and
medium before transferring a preference, treats an unnamed object's owning
image as part of its description, and searches the object class without a
resolved-person filter when that filter can hide attachment-derived evidence.
Non-exclusive recommendations are not unique favorites; supported alternatives
may be qualified, while casual mentions are excluded. Compact output must retain
the requested concrete values, not explanations of search sufficiency.

These replace overlapping earlier wording; they contain no named answer,
person, condition or source label. The alternative of injecting a missing answer
or suppressing a retrieved competing candidate would tune to the reference
rather than improve general recall and is rejected. A broader answer can still
include irrelevant material or lead with uncertainty; the complete regression
must expose that. There is no postprocessing, manual grade override or new
retrieval/extraction model. Trial06 holds this template fixed and changes only
the catalog projection. Its outcome is pending, so the combined current-protocol
candidate remains a draft. A fresh full20 arm is allowed only after both selected
questions pass.
