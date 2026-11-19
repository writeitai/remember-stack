# Pipeline checkpointing — current contract

The accepted contract is [PostgreSQL-native P1 §7](postgres_p1_search_projection_design.md#7-writes-and-readiness),
including bounded fact-scoped coordination in §7.1. Label checkpoints and
atomic vector/input attestation remain separate; unrelated facts may be
projected concurrently. The former project-wide embedding lock is removed.

The unaccepted 2026-08-06 proposal is retained as
[historical analysis](../analysis/pipeline_checkpointing_proposal_20260806.md).
This pointer preserves old inbound links, not an alternative implementation.
