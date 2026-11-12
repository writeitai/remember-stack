# D153 design review record

The owner required Claude Opus 5.5 HIGH approval before runtime implementation.
All three reviews used the exact requested Claude Code model/effort and ran
read-only. Invocation identities, prompt/output hashes and verdicts are in the
adjacent JSON; complete prompts and reviews are retained.

| Round | Reviewed revision | Verdict | Result |
| --- | --- | --- | --- |
| 1 | `32b2a5d2d` | Changes required | Concrete probes/indexes, fact layout, independent oracle, adverse cases and corpus adoption needed |
| 2 | `085b06869` | Changes required | Pin actual D140-widened head authority, entity-driven scope, opaque-ID partition cost and schema/capture pins |
| 3 | `431900b1d` | **Approved** | Zero remaining blockers; explicit linear partition-probe tradeoff accepted |

The accepted contract is [D153](../../designs/shared_retrieval_eligibility_design.md).
Promotion and reconciliation add the same reviewed rules to the current P1,
entity, query-space and schema designs and mark the D149/D23 amendments.
The schema DDL explicitly records the reviewed mention-index final shape and
migration session lock. No runtime implementation preceded approval.

The first review's hand-picked old-migration oracle was corrected in the
second review: only a real database migrated to `p9_46_0067` supplies the
pre-change authority, including carried periodised claims. The evidence here
does not claim a runtime speedup, completed implementation or new LoCoMo score.
Implementation qualification and its own review remain separate from design
approval.
