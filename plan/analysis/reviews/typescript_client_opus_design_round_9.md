## Verdict: CHANGES REQUESTED (merge only) at `12a43e7ada7089e00aa34e1f1488f394a140578d`

The implementation approval from round 8 at `f1befcb4` still stands, and round 7's complete approval at `707ede2e` hasn't regressed. All five items round 8 asked for are fixed. One new sentence added in this commit is wrong about current Python, and it sits in the shared authority, D136 §8.2. That needs a one-line fix before merge.

### Blocking: config-directory variable names are not read exactly in Python

`plan/designs/one_key_client_surfaces_design.md:684-685` says "config directory names are read exactly as shown." Python actually matches them case-insensitively. `src/remember/credentials.py:53-59` reads `REMEMBER_CONFIG_DIR` and `XDG_CONFIG_HOME` through a pydantic-settings class with the default `case_sensitive=False`, the same mechanism as the connection variables.

I checked this in child processes on macOS, with `PYTHONPATH=src` and the sibling worktree's virtualenv:
- `remember_config_dir=/X` gives `/X/credentials.json`.
- `Remember_Config_Dir=/X` gives the same.
- `xdg_config_home=/X` gives `/X/remember/credentials.json`.

**What goes wrong:** the TypeScript design says only connection names are case-insensitive (`typescript_client_design.md:119`), and §8.2 says config names are exact. A TypeScript client built to that text would read `~/.config/remember/credentials.json` while the Python CLI reads and writes `/X/remember/credentials.json`. That breaks the very rule round 8 used to justify the empty-directory paths: TypeScript must find the file the Python CLI writes. It also makes the `configDir` support export in the inventory differ from Python's `config_dir`.

**Fix:** change §8.2 to say all of these environment names are matched case-insensitively, as in Python, and extend the sentence at `typescript_client_design.md:119` to include `REMEMBER_CONFIG_DIR` and `XDG_CONFIG_HOME`. The other option is to keep exact names, add a §7 adaptation row and change Python too, but that is more work for no gain.

### Round 8 items: all fixed

| Item | Where | Result |
|---|---|---|
| 1. "Explicit or environment" signed keys | §3 `:129-131`; Windows row `:348` | Fixed. The row now covers argument and environment keys, file present and absent, and signed and unsigned keys. The unsigned behavior matches `connection.py:148-150`: it refuses when a file exists and falls back to localhost when none does. |
| 2. Python change recorded in the corpus | D140 `decisions.md:6705`; D136 note `:6448-6450`; §8.2 `:677-685` | Fixed. "D136 amended only as stated above" now covers the new D140 bullet. Delivery plan step 2 already requires the Python behavior, tests and docs change in the same PR. |
| 3. Upper limit of 2,147,483,647 ms | §7 `:344` | Fixed. Values must be finite, above zero and at most that limit. Invalid values raise `InputValidationError` before any HTTP call. Tests cover the upper-limit refusal and the accepted maximum. |
| 4. Queued-request test on the 421 case | Test gate `:300-302` | Fixed. It now matches round 7's request: 421 retirement with a request already queued, checked by server-side connection ID rather than `reusedSocket`, plus idle retirement including an active response longer than 4 s, on HTTP and HTTPS. |
| 5. Plain-language nits | `:120-122`, `:344` | Fixed. The design says why zero is refused and names the error class. It spells out both empty-directory paths and the reason for them. |
| Case-insensitive connection variables | `:119`; §8.2 | Correct for the connection variables (a lowercase `remember_api_url` and `remember_project` resolve as environment values). The config-directory part is wrong, as described above. |

The inventory JSON parses and points to §7 for adaptations, so it needs no duplicate entries.

### Optional nits

- **"Explicit" key plus URL:** `:126`, `:348` and §8.2 `:678` still say "explicit key and URL". Python skips the file when the key and URL come from arguments *or* the environment (`connection.py:148`). §3's phrase "read only when required by precedence" already implies this, but matching item 1's "argument or environment" wording would remove the ambiguity.
- **Two spellings of one variable:** if both `REMEMBER_API_URL` and `remember_api_url` are set, Python uses whichever comes last in the process environment. If that value is empty, it counts as unset and resolution falls through to the file. Node's `Object.keys(process.env)` keeps the same order, so stating this rule, or adding a fixture for it, would pin down parity.
- **Negative timeouts:** the §7 test column could list them alongside zero and non-finite values.
- **Stored issuer:** when a Windows signed key skips the file, §8.2 could say "stored URL/project/issuer" are unavailable, not just URL and project.

### Scope and limits

- I read every change between `707ede2e` and HEAD, the round 7 and round 8 reviews, and the Python files `connection.py` and `credentials.py`.
- Nothing was run on Windows.
- I edited no repo files or git refs, and made no GitHub, legal, registry, production or real-credential access.
- The inherited `REMEMBER_CONFIG_DIR` (`/tmp/remember-design-review9-config-aqRMhf`) is unchanged. The probes overrode it only inside `env -i` / `env -u` child processes and only computed paths; no credential files were read or written.
- The one scratch directory I created was empty and has been removed. The working tree is clean.
