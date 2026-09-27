# Canonical `remember` release identity and PyPI retirement

**Status:** non-binding analysis. **Date:** 2026-09-22.

## Question

How should this repository present releases after D108 made `remember` the sole
current Python distribution, without renaming `writeitai/remember-stack` or
discarding its existing GitHub release history?

The failure to avoid is not merely cosmetic. The latest public
`rememberstack` release is the old engine-bearing `0.16.0`; leaving that as the
apparently current Python choice allows a mistaken human or coding agent to
install obsolete server code. Conversely, deleting the PyPI project would break
old pins and release the name for another publisher to claim.

## Repository evidence

- D108 already selects `remember` as the exclusive active PyPI distribution,
  retains `writeitai/remember-stack` as the repository, and distributes the
  engine through `ghcr.io/writeitai/remember-stack` (`decisions.md` D108 and
  `plan/designs/unified_remember_distribution_design.md`).
- The root `pyproject.toml` builds only `src/remember`, but it also registers a
  legacy `rememberstack` command. That command makes a normal `pip install
  remember` advertise both names even though only one is current.
- `packages/rememberstack/` is a six-file transition distribution depending on
  `remember>=0.17.0`; it is not the engine under `src/rememberstack/`.
- `.github/workflows/release.yml` publishes the transition distribution only at
  `v0.17.0`, but its `dist/*` GitHub-release glob also advertises that package
  beside the canonical `remember` wheel and source archive.
- `scripts/check_github_release.py` currently makes both distributions required
  public attachments at `v0.17.0`, so hiding the retired name requires changing
  the recovery contract as well as the upload glob.

## Observed incomplete v0.17 attempt

GitHub Actions run
[`35090761998`](https://github.com/writeitai/remember-stack/actions/runs/35090761998)
started from commit `0c882ac793f31a90e544fbf0babffc75ed9f5b8a` on
2026-09-16. Verification and the application-image publication succeeded, but
the PostgreSQL image job failed before execution because two pinned Docker
action commit identifiers did not exist. The PyPI job remained waiting, and no
Git tag or GitHub release was created. The run was cancelled on 2026-09-22
before any PyPI publication.

The partial run left only
`ghcr.io/writeitai/remember-stack:0.17.0` (digest
`sha256:80d1580f5c25d90e97515e01de9d4f5a5936b6be0069e3cd37c444b80c1db477`)
from the obsolete source revision. Recovery deletes that exact orphaned
container version after verifying the absence of PyPI `0.17.0`, the Git tag,
the GitHub release, and the PostgreSQL image, then publishes the coordinated
release from current `main`. The release contract pins the valid upstream
commits for `docker/setup-qemu-action` v3.2.0 and
`docker/setup-buildx-action` v3.10.0 so the failure cannot recur silently.

## External lifecycle facts

PyPI project archival is designed for projects that expect no further updates.
Archival keeps releases installable, prevents new uploads, visibly marks the
project, and can be reversed by an owner. PyPI recommends making a final release
with updated retirement context before archiving. Source: [PyPI Now Supports
Project Archival](https://blog.pypi.org/posts/2025-01-30-archival/), retrieved
2026-09-22.

Deleting a PyPI project is permanent, makes it uninstallable, and releases the
project name for use by another PyPI user. Source: [PyPI Help — restoring a
deleted project, release, or file](https://pypi.org/help/#deletion),
retrieved 2026-09-22. That name-release behavior creates an avoidable
supply-chain risk for old documentation and lock files.

PyPI describes yanking as a non-destructive response for broken,
compatibility-violating, or vulnerable releases, and exact pins may still
select a yanked release. It is therefore not a project-retirement mechanism.
Source: [PyPI yanking documentation](https://docs.pypi.org/project-management/yanking/),
retrieved 2026-09-22.

## Alternatives

### A. Delete `rememberstack` from PyPI

This would make unpinned installation fail, but it would permanently break old
environments and make the trusted name claimable by another publisher. It is
rejected as both disruptive and unsafe.

### B. Archive `rememberstack==0.16.0` without a final forwarder

This would stop updates but leave an unpinned `pip install rememberstack`
resolving to the obsolete engine-bearing wheel. It preserves exactly the
confusion D108 is intended to remove and is rejected.

### C. Publish one terminal forwarder, then archive the project

Publish `rememberstack==0.17.0` once, depending on `remember>=0.17.0`, with
inactive metadata and a visible migration warning. Do not attach or feature it
on the GitHub release. After publication, archive the PyPI project. A mistaken
install then resolves to the safe client package, while all current release
surfaces present only `remember`.

This is the recommended alternative.

### D. Rename the repository or internal engine namespace

Renaming the repository would discard the deliberate `remember-stack`
distinction and risk confusion with similarly named projects. Renaming the
internal engine namespace would touch hundreds of source and test files without
changing what PyPI presents. Neither is required for the package-retirement
goal, so both are rejected here.

## Recommended release contract

1. Preserve the repository name, every existing tag, and every historical
   GitHub release.
2. Name new GitHub releases `remember <version>` while retaining the existing
   `v<version>` tags.
3. Attach only the canonical `remember` wheel/source archive and engine
   deployment inputs to GitHub releases.
4. Make `pip install remember` install only the `remember` and
   `remember-status` commands. The `rememberstack` command exists only when the
   terminal transition distribution is explicitly installed.
5. Publish the terminal distribution once at `0.17.0`, mark its package
   metadata inactive, and emit a warning visible under Python's default warning
   filters.
6. Archive the `rememberstack` PyPI project after the terminal upload succeeds;
   never delete it, reuse it, or publish another version.
7. Present `remember` as the sole Python package in the README, project-status
   documentation, generated GitHub release title, notes, and attachments.

## Failure and recovery

The release workflow publishes both PyPI projects before creating the GitHub
release. If either upload fails, no GitHub release is created. If GitHub release
creation fails after the terminal upload, a rerun verifies the already-published
artifacts and resumes. The public release completeness check therefore needs
only the canonical `remember` artifacts plus immutable engine evidence; the
terminal distribution remains a prerequisite job outcome, not a public release
attachment.

Archival is a post-publication PyPI owner action. It must occur only after the
terminal `0.17.0` files and metadata are visible. If archival is missed, the
package remains safely forwarded but incorrectly marked active; archive it
without changing or deleting any release.
