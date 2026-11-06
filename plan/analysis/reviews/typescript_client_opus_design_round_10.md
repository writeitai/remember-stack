## Verdict: APPROVED at `8c048dc40174939426c0673dd8751d8b4f4a22fb`

The round 9 blocker is fixed, and the four optional wording nits are handled correctly. Only three files changed between `12a43e7a` and HEAD: the round 9 report, which is newly recorded, and two short edits in the design docs. Nothing else changed, so the round 8 implementation approval (`f1befcb4`) and the round 7 full design approval (`707ede2e`) still stand.

### The blocker: config-directory variable names
- **D136 §8.2** (`one_key_client_surfaces_design.md:684-686`) now says connection and config-directory variable names are case-insensitive, as in Python. That replaces "read exactly as shown".
- **TypeScript design** (`typescript_client_design.md:119-120`) now names `REMEMBER_CONFIG_DIR` and `XDG_CONFIG_HOME` as case-insensitive.
- **Python check:** I ran child processes under `env -i` with pydantic-settings 2.14.2. `remember_config_dir=/X` and `Remember_Config_Dir=/X` both give `/X`, and `xdg_config_home=/X` gives `/X/remember`. The cause is `credentials.py:53-59`: `_ConfigDirSettings` uses the default `case_sensitive=False`. The docs now match the code, so TypeScript will find the file the Python CLI writes. The `configDir` support export in the inventory also matches Python's `config_dir`, and the inventory JSON still parses.

### The optional nits
| Nit | Result |
|---|---|
| Argument or environment key plus URL | Fixed at TS `:127`, the §7 Windows row and §8.2 `:678`. This matches `connection.py:148`. |
| Stored issuer skipped | §8.2 now says "Stored URL/project/issuer". |
| Last spelling wins | Correct. With `REMEMBER_API_URL=http://a` set before `remember_api_url=http://b`, Python returns `b`; reversing the order returns `a`. If the last spelling is empty, Python returns `''`, which counts as unset, so the setting falls through to the file. For config directories the last spelling also wins, but an empty value means the current directory instead of falling through (`config_dir` gives `.`), which agrees with the TS empty-directory sentence. Node 24's `Object.keys(process.env)` lists variables in the same order, so TypeScript can match this. |
| Negative timeouts | Added to the §7 test column. |

### Remaining nits (not blocking)
- `typescript_client_design.md:130` still says "explicit key plus URL bypasses the store". The line just above it and the §7 row already say "argument or environment", so this is only cosmetic.
- "including REMEMBER_CONFIG_DIR and XDG_CONFIG_HOME" could be misread as covering `HOME` too. It doesn't: `~` is looked up exactly by both Python's `Path.home()` and Node's `os.homedir()`, so the two clients still agree. "namely" would be clearer.
- The last-spelling-wins rule is written in §8.2 but has no named test case in the TypeScript test gate. One test with two spellings, including an empty last spelling, would lock it in.

### Scope and limits
- This was a targeted follow-up, not a fresh end-to-end review. I read the round 9 report, the HEAD commit, the edited sections, `credentials.py` and `connection.py`.
- Nothing was run on Windows.
- I made no changes to source files, git, GitHub, legal records, the registry or production, and accessed no real credentials. The probes only read the environment and computed paths; they never read or wrote credential files.
- The inherited `REMEMBER_CONFIG_DIR` (`/tmp/remember-design-review10-config-hLLnXJ`) was not changed. My one scratch file, `/tmp/r10probe.py`, has been removed, and the working tree is clean.
