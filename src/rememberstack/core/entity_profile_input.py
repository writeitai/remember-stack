"""Exact text contracts for evidence-backed entity profile embeddings."""


def entity_profile_embedding_input(
    *, canonical_name: str, profile_summary: str, salient_facts: tuple[str, ...]
) -> str:
    """Build the candidate profile text stamped by entity-profile-v2."""
    facts = "\n".join(f"- {fact}" for fact in salient_facts)
    return (
        f"ENTITY: {canonical_name}\nPROFILE: {profile_summary}\nSALIENT FACTS:\n{facts}"
    )


def mention_profile_embedding_input(*, name: str, claim_context: str) -> str:
    """Build the mention-side T3 input aligned to candidate profile text."""
    return f"ENTITY: {name}\nCLAIM CONTEXT: {claim_context.strip()}"
