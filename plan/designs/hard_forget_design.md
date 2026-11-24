# Hard-forget design — superseded

> **Status:** superseded by [D159](../../decisions.md#d159-archive-and-forget-replace-delete-forget-hides-at-once-and-sweeps-until-clean).
> The binding contract for erasing a document is the
> [archive and forget design](archive_and_forget_design.md) (§3, Forget). This path is kept
> only so older links resolve; the D74 workflow it described (portable forget manifest,
> deployment-wide admission barrier and drain, startup readiness replay, stop-and-remount of
> mounts, permanent re-ingest guard) is removed. Its history is in
> [D74](../../decisions.md#d74-hard-forget-is-an-append-first-fail-closed-lineage-purge-with-one-portable-manifest) and git.
