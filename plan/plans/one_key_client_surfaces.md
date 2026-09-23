# One key, one tool catalogue — build order (D136)

Design: [one_key_client_surfaces_design.md](../designs/one_key_client_surfaces_design.md).
Nobody uses the product yet, so each step replaces the old path in the same
change and deletes it: no compatibility layer, no legacy variables, no
credential-file conversion, no acceptance of the old `scope` claim.

1. **Catalogue.** Create `remember.mcp_tools` (definitions, annotations,
   validation, error envelopes, `render_tools_list`); move the parsers out of
   `mcp_memory_tools.py` and `query_sandbox/mcp_tools.py`; delete those modules
   and the `rememberstack.surfaces` re-export shims; generate the registry's
   agent-facing fields from the catalogue; add `tools` to `GET /deployment`.
2. **Perimeter.** Replace the signed-token claim contract (issuer, audience
   set, `permissions`, `kind`, scope set) and the plain revocation list with
   the signed, sequenced revocation document and `active_kids`; add the
   persisted perimeter-state row.
3. **Client resolver and login.** One `resolve_connection()` for SDK and CLI;
   delete the old variable names, `CloudClient` and credential file version 1;
   `client.account`; issuer metadata, RFC 8628 login, mint→persist→revoke,
   host resolution with TTL and re-resolution.
4. **`remember mcp`.** Rebuild engine mode on the catalogue; add Streamable
   HTTP, multi-target routing and bridge mode.
5. **`remember setup`.** Entry selection per harness.
6. **Docs.** Update the `website/` pages listed in PR #458 in the same PRs as
   the behaviour they describe.
