I'm approving this design for implementation at HEAD **`f1befcb424885447030be87ac2b9dc03b8d580af`** on `design/typescript-client-parity`. Nothing blocks starting work. Four items should be fixed before merge: two are wording gaps in the new clarifications, one is a missing note in the decision log, and one is a test requirement that drifted from what round 7 asked for.

## Scope: what changed since round 7

Round 7 approved `707ede2e`. Two commits have landed since:

| Commit | Content | Status |
|---|---|---|
| `8ce3d112` | Fixes for round 7's five non-blocking items, plus D140 cleanup | Checked here. All addressed except a wording slip (item 4 below). |
| `f1befcb4` | The three clarifications you listed | New in this review |

The change is limited to the design document, `decisions.md`, a one-line `HttpTransport` type change in the inventory, and the round-7 review file. The working tree is clean and the inventory JSON parses.

## The three clarifications

**1. Empty config directory vs empty connection variables.** Correct, and it matches Python exactly. This is parity, not a new adaptation. I checked it in Python:
- `REMEMBER_CONFIG_DIR=""` resolves the store to `credentials.json` in the current working directory.
- `XDG_CONFIG_HOME=""` resolves it to `remember/credentials.json` in the current working directory.
- Empty `REMEMBER_API_URL`, `REMEMBER_PROJECT` and `REMEMBER_MCP_URL` are treated as unset.

**2. Signed keys on Windows.** The behavior is sound and safe:
- The key goes to its own issuer.
- The stored values it skips can't be read on Windows anyway.
- Linux and macOS keep the ordinary precedence.

It closes a real gap. I ran a synthetic signed key with no URL against an existing credentials file. With the file at mode 0644, current Python raises `CredentialError` for both a passed-in key and `REMEMBER_API_KEY`. Mode 0644 stands in for Windows, where Python reports every file as readable by others; I took that from CPython source, not from a Windows run. With the file at 0600 (Linux/macOS), Python uses the stored URL and project.

This is a behavior change for **both** clients, not a TypeScript-only adaptation, so it needs recording (items 1–2 below).

**3. Positive finite deadlines.** This is a genuine TypeScript adaptation, and it is listed in the adaptations table with tests.
- Python's zero wait timeout does one poll and stops.
- Python's zero poll interval polls back to back.
- Python's negative poll interval fails inside `time.sleep`.

Refusing these is acceptable because `pipelineReadiness` still covers a single check.

## Fix before merge

1. **Low–Medium: say whether "explicit" includes `REMEMBER_API_KEY`.**
   - §3 uses "explicit" narrowly at `:150` ("Explicit/environment keys") and broadly at `:124`.
   - Both key sources fail today, and environment keys are the common setup on Windows.
   - Suggest "an explicit or environment signed key".
   - Update the Windows row at `:347`, which still says "Same refusal", with fixtures for: argument vs environment key, credentials file present vs absent, and an unsigned key with no URL (refused if a file exists, localhost if not, as Python does today).
2. **Low (corpus rule): the Python change isn't recorded.**
   - D140 still says "D136 amended only as stated above".
   - The D136 amendment note and the D136 design §8.2 precedence table don't mention the Windows exception, yet the TypeScript design calls §8.2 the shared authority.
   - The adaptations table also says anything it doesn't list must match current Python. Add a clause to D140, extend the D136 note, and add a Windows line to §8.2.
3. **Low: "positive finite" needs an upper limit.**
   - Node fires any timer above 2,147,483,647 ms (about 24.8 days) after 1 ms.
   - I confirmed this on Node 22.0.0 and 24.0.2, for both `setTimeout` and `AbortSignal.timeout`.
   - As written, a very long wait would time out immediately. Refuse values above that limit (or chain timers) and add a boundary test.
4. **Low (drift from round 7's ask): the queued-request test is attached to the wrong case.**
   - At `:298-301`, "a request already queued on the same agent" now qualifies idle retirement. Round 7 asked for it on retirement after a 421, which is the trap it actually measured.
   - Suggested wording: "421 retirement with a request already queued… verified by server connection identity; idle retirement, including an active response over 4 s".
5. **Nits (plain-language rule):**
   - Explain why zero is refused: the TypeScript deadline also bounds the request in flight, so a zero deadline can never finish Python's single poll.
   - Name the refusal class (`InputValidationError`, raised before any HTTP call).
   - Spell out the two empty-directory paths, and why: TypeScript must find the file the Python CLI writes in the same environment.

**Older observation, not from this change:** Python reads these environment variable names case-insensitively. For example, a lowercase `remember_api_url` is honored; I verified this. A TypeScript client reading exact names won't do the same, and the adaptations table doesn't record it.

## How I checked, and limits

- I read D140, D136 (entry and §8.2–8.3), the design, the inventory, the plan, the analysis and round 7, plus the Python sources (`credentials.py`, `connection.py`, `issuer.py`, and `client.py` for the wait and constructor code).
- The Python checks used a sibling worktree's virtualenv (`/private/tmp/remember-ts-client/.venv`) with this HEAD's `src` on the import path.
- **Limits:**
  - Nothing was run on Windows; that part is from CPython source.
  - There is no implementation to test.
  - I didn't re-review the round-7-approved content end to end, only checked it for consistency with the new text.
- I edited no repo files, git refs, GitHub or CLA, and used no real credentials. The only things I wrote are scratch scripts in `/tmp/opus-r8-scratch.oRjPpT`.
- The synthetic `REMEMBER_CONFIG_DIR` (`/tmp/remember-design-review-config-OFhOxX`) was inherited and left unchanged. The empty-value tests set it only in child processes, and the store tests redirected the file path instead.
