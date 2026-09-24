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
2. **Direct-path admission.** Per-key and per-deployment rate and in-flight
   limits in the perimeter (design §7.6), in place before any key is issued.
3. **Perimeter.** Replace the signed-token claim contract with the per-kind
   claim sets (`aud`, `org`, `projects`, `permissions`, `kind`), URL-fetched
   key set, and the signed, sequenced revocation document with `active_kids`;
   `src` required by the issuer on derived `session` credentials only (the
   engine accepts it as optional); delete the inline key set, the plain revocation list and the `umc_dp_`
   special case (any letters-only prefix is stripped); add the persisted perimeter-state row.
4. **Client resolver and login.** One `resolve_connection()` for SDK and CLI;
   delete the old variable names, `CloudClient` and credential file version 1;
   `client.account`; issuer metadata, RFC 8628 login, journal→mint→replace→revoke re-login,
   host resolution with TTL and re-resolution.
5. **`remember mcp`.** Rebuild engine mode on the catalogue; add Streamable
   HTTP and bridge mode.
6. **`remember setup`.** Entry selection per harness.
7. **Docs.** Update the `website/` pages listed in PR #458 in the same PRs as
   the behaviour they describe.
