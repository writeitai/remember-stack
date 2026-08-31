You can use Codex and Antigravity as separate subagents like this:
- when the agent should use CODEX, it means you will execute the agent as `codex exec --yolo --model gpt-5.5 PROMPT="<your prompt>"`. If you want to include a pre-prepared file with prompt, do it like this: `cat /tmp/some-file.txt | codex exec --yolo --model gpt-5.5`.
- when the agent should use ANTIGRAVITY, it means you will execute the agent as `agy --dangerously-skip-permissions --print-timeout 180m0s -p "<your prompt>"`. Be sure to include the --print-timeout set to multi-hour value.
