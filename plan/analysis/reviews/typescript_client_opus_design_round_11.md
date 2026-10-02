**CHANGES REQUESTED at `8713135641295bb7b0585b0ce6f43e1f89ce1c60`**

The adaptation itself is sound. The blocker is that it rests on a wrong premise: the Python server does refuse naive inputs. It also refuses non-UTC offsets such as `+02:00`. Both documents imply the server accepts them, so they need to state what the server actually does.

### What changed since round 10 (`8c048dc4`)
Three files changed, and nothing else: the recorded round 10 report, the new final section of `typescript_client_parity.md`, and one new §7 row in `typescript_client_design.md`. The round 10 report is recorded faithfully.

### What I checked, and what holds
- **Python client:** confirmed. `lookup_relations`, `graph_neighborhood` and `graph_path` accept naive datetimes and send `.isoformat()` unchanged (`client.py:480, 626-628, 659-661`). Those five fields are the only general datetime inputs. `source_modified_at` stays UTC-only in the Python client (`client.py:796-800`).
- **Published schema:** confirmed. `openapi.json` gives exactly those five fields as `format: date-time`, with no UTC constraint.
- **References:** correct. RFC 3339 §5.6 requires an offset in `full-time`. JSON Schema 2020-12 validation §7 does separate format annotation from optional format assertion.
- **Consistency with earlier approvals:** fine. The §7 header (`:331`) already says top-level Date options become UTC ISO.

### Blocker: the server's actual behavior is wrong in both documents
`http_api.py:139-146` defines `_require_utc` / `UTCInstant`: "Refuse naive and non-UTC instants at the boundary (422, never 500)". All three routes use it: `/lookup/relations` `valid_at` (`:529`) and both clocks on the two graph request models (`:336-337, :359-360`). It shipped in v0.17.1 and v0.17.2 (commit `78660f99`, G27). v0.16.0 still used plain `datetime`.

Existing tests require the refusal. `test_http_api_robustness.py:181-190` sends `"2026-01-01T00:00:00"` and `"…+02:00"` and expects 422. `:252-266` does the same with naive and `-05:00` on `/graph/path`.

I ran the real code in a scratch environment:
- Both graph models refuse naive and `+02:00` with "must be timezone-aware UTC (end it with Z)".
- `GET /lookup/relations` returns 422 for naive and `+02:00`, and 200 for `Z`.

What this means for the documents:
1. **Analysis section.** The heading "Python/runtime versus the published format", "Executed Python wire cases pass" and "this does not claim FastAPI refuses naive values at runtime" all lead a reader to think the server is permissive. The UTC requirement is invisible in OpenAPI only because the `AfterValidator` isn't reflected in the schema.
2. **§7 row.** "Preserve nonzero offsets" and "unchanged +02:00 wire fixtures" match the Python client's wire bytes. But a `+02:00` value sent to the current server gets 422, the same as from Python. "Does not claim the Python server refuses naive values" hides that, so an implementer could write live or compatibility-matrix tests expecting `+02:00` to succeed against the candidate server.

### Recommended fix (keep the selected behavior, correct the rationale)
- **Analysis:**
  - Rename the heading to "Python client versus the published format".
  - State the facts: v0.17.1+ servers refuse naive and non-UTC instants with 422 on these fields; v0.16.0 accepted plain `datetime`; OpenAPI omits the UTC constraint.
  - The TypeScript adaptation refuses naive values before any HTTP call. The current server would refuse them anyway, and older servers would have to guess a timezone.
  - Offsets are forwarded unchanged, so the server stays the authority. A `+02:00` value gets the same 422 `MemoryApiError` as Python.
- **§7 row:** replace the disclaimer with "the current server refuses naive and non-UTC instants (422); refusing naive values moves that failure before HTTP, and offsets are forwarded unchanged so non-UTC values receive the server's 422 as in Python".
- **§7 test column:** add "+02:00 → 422 `MemoryApiError` against the candidate server; Z succeeds". Matrix expectations must differ by server version, since v0.16.0 accepted these values.

I'm not recommending a UTC-only client check. Python's client doesn't have one, so it would be a further divergence that needs its own §7 entry.

### Scope and limits
- This was a targeted follow-up on `8c048dc4..HEAD` only. The approvals from rounds 7, 8 and 10 stand for everything else.
- Nothing was run on Windows.
- I made no changes to source, git, GitHub, the CLA, npm or production, and used no credentials. The probes only checked model and route validation.
- `REMEMBER_CONFIG_DIR` (`/tmp/remember-design-review11-config-Q9Xlsf`) was not changed. Scratch dependencies went into the uv cache. The scratch directory `/tmp/r11probe` has been removed, and the working tree is clean.
