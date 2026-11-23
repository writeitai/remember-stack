# Hard-forget design — superseded

> **Superseded by [D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-erases-one-document-without-stopping-the-deployment)
> (2026-11-23).** Erasing a document is now the **forget** verb in the
> [archive and forget design](archive_and_forget_design.md) §3. This path stays only so
> existing links keep working.

The workflow this file used to describe (D74) has been removed: there is no separate forget
manifest store, no deployment-wide admission barrier or drain, no `forget_in_progress` answer
for ordinary traffic, no replay of every forget at startup, no refusal while authored pages
cite the document, no permanent re-ingest guard, and no stop-and-remount of mounts. What
survives of D74 (the erasure inventory, the purge steps, the knowledge-repository history
rule and the S55 "forgotten means never existed" contract) now lives in the archive and
forget design. Why it changed is in the
[archive and forget analysis](../analysis/archive_and_forget.md); the old text is in git
history.
