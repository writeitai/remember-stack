# Explicit permission for unresolved state overlap

Status: unchosen alternative to D111, 2026-09-07.

An explicit per-fact identity-uncertainty field could permit overlap independently
of endpoint knowledge. It would require a recorded authority for setting and
clearing permission, a partial exclusion predicate, all-writer participation,
replay/forget coverage, and disclosure in retrieval, counts and generated text.

D111 chooses missing verdict start as the existing discriminator for ordinary
undated coexistence. Adding permission for exactly that class duplicates state
and creates more transitions that can disagree. See
`../analysis/temporal_undated_state_coexistence.md` for the comparison.

Adoption trigger: an accepted requirement calls for materializing distinct
known-start overlapping state identities while their identity remains unresolved,
and neither evidence attachment nor retaining unresolved testimony meets that
requirement. That decision must specify whether counts refer to fact identities
or distinct world episodes and include the complete lifecycle cost; a database
exclusion error alone is not evidence of contradiction or permission.
