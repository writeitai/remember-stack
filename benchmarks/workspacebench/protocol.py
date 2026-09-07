"""Pinned coordinates for the experimental Workspace-Bench smoke protocol.

Numbers such as timeouts are starting points to measure, not claimed optima.
The first live CLI defaults to task 300; models do not hard-code its filenames.
"""

from __future__ import annotations

from typing import Final
from typing import Literal

PROTOCOL_NAME: Final = "RS-WorkspaceBench-CodexSubscription-CloudLocal/v1"
PROTOCOL_EXPERIMENTAL_LABEL: Final = (
    "experimental Workspace-Bench protocol; not an upstream leaderboard result"
)
ADAPTER_VERSION: Final = "1.0.0"
DEFAULT_TASK_ID: Final = "300"
UPSTREAM_COMMIT: Final = "3fbd0f1a136720fece86786545983e26642c3db2"
CODEX_EXECUTION_BACKEND: Final[Literal["sdk"]] = "sdk"
CODEX_SDK_VERSION: Final = "0.147.0"
CODEX_CLI_VERSION: Final = "0.147.0"
# Observed SHA-256 of the bundled binary is recorded on ProtocolCoordinates
# and in the protocol fingerprint. That digest is platform-specific.
CODEX_ISOLATION: Final[Literal["disposable_codex_home_keyring"]] = (
    "disposable_codex_home_keyring"
)
CODEX_CREDENTIALS_STORE: Final[Literal["keyring"]] = "keyring"
CODEX_MODEL: Final = "gpt-5.6-luna"
CODEX_REASONING_EFFORT: Final = "high"
CODEX_CLIENT_NAME: Final = "rememberstack_workspacebench"
CODEX_CLIENT_TITLE: Final = "RememberStack Workspace-Bench"
CODEX_SERVICE_NAME: Final = "rememberstack-workspacebench"
CONSUMPTION_INSTRUCTION_VERSION: Final = "1.1.0"
MCP_SERVER_NAME: Final = "remember"
MCP_READ_ONLY_ARGS: Final[tuple[str, ...]] = ("mcp", "--read-only")
# Starting points to measure, not committed performance constants.
MCP_STARTUP_TIMEOUT_SEC: Final = 30
MCP_TOOL_TIMEOUT_SEC: Final = 60
DEFAULT_TIMEOUT_SEC: Final = 300.0
DEFAULT_GRACE_SEC: Final = 30.0
TARGET_OUTPUT_DIR: Final = "model_output"
OFFICE_SKILL_SOURCE_RELPATH: Final = "evaluation/skills/office"
OFFICE_SKILL_WORKSPACE_RELPATH: Final = ".agents/skills"
OWNER_ONLY_FILE_MODE: Final = 0o600
REQUIRED_OFFICE_EXECUTABLES: Final[tuple[str, ...]] = ("soffice", "pdftoppm")
LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"127.0.0.1", "localhost", "::1", "[::1]"}
)
SDK_TIMEOUT_PARTIAL_EVENTS: Final = (
    "The openai-codex 0.147.0 synchronous thread.run call has no benchmark "
    "wall-clock deadline. An external process-group supervisor terminates the "
    "child. Partial runtime events are retained only if the child flushed a "
    "turn receipt before SIGTERM; the SDK itself does not return partial items "
    "after an external kill."
)
DEFAULT_ARM_ORDER: Final[tuple[Literal["native"], Literal["memory"]]] = (
    "native",
    "memory",
)

# Closed D87 catalog, in canonical registry order. Preflight/catalog binding
# compares this pin to CANONICAL_OPERATIONS so a later rename cannot keep
# Workspace-Bench on stale names. Do not derive this tuple at runtime.
REQUIRED_ASSURED_TOOLS: Final[tuple[str, ...]] = (
    "resolve_entity",
    "claims_and_sources_context",
    "facts_context",
    "combined_context",
)

EVALUATION_ONLY_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {
        "rubrics",
        "rubric_types",
        "judge_metadata",
        "ground_truth",
        "reference_output",
        "file_dep_graph",
        "dependency_graph",
        "gold",
        "gold_fields",
    }
)
EVALUATION_ONLY_METADATA_PREFIXES: Final[tuple[str, ...]] = (
    "rubric_",
    "judge_",
    "ground_truth_",
    "gold_",
)
EVALUATION_ONLY_METADATA_FILENAME: Final = "metadata.json"

REQUIRED_UPSTREAM_FILES: Final[tuple[str, ...]] = (
    "evaluation/scripts/run_isolated_benchmark.py",
    "evaluation/src/task_container_entry.py",
    "evaluation/src/agent_as_a_judge.py",
    "evaluation/src/agent_runner.py",
    "evaluation/src/agents/codex.py",
    "evaluation/src/filesys_utils.py",
    "evaluation/src/task_patches.py",
    "LICENSE",
)

# Byte hashes of REQUIRED_UPSTREAM_FILES at UPSTREAM_COMMIT. Preflight rejects
# a clean-looking checkout whose runner files drifted.
EXPECTED_UPSTREAM_FILE_SHA256: Final[dict[str, str]] = {
    "evaluation/scripts/run_isolated_benchmark.py": (
        "e5a6a986696f19fbc28e00d3b5a10f6acf7d9bc48e42ef50bb132eb94fa07da2"
    ),
    "evaluation/src/task_container_entry.py": (
        "a14fae95a4d7ac076cb89ad49863ca45b65e1aff4ea4dbecbd628f9bf0d28ce0"
    ),
    "evaluation/src/agent_as_a_judge.py": (
        "7fe885d4d1f958eaaf1788c940cabe435581acc264c80d9075ca73b0ba538bf6"
    ),
    "evaluation/src/agent_runner.py": (
        "badb4cc4e97b6b7ac86c4b73f39fbd16fdeaadacde3d49a6ee75a0ce961ce16d"
    ),
    "evaluation/src/agents/codex.py": (
        "03486d9ffa671a51ed77520a798fcca8d6d4f39eaf5e02a80584de034dcadb9d"
    ),
    "evaluation/src/filesys_utils.py": (
        "879b3f672beb9b3c77fd81fc94aa86e7f52ef190cfd56ad53655da04e278f2a9"
    ),
    "evaluation/src/task_patches.py": (
        "3829dafdbca71fd727598b97e96e45855ff4053d475b456b9010def4baeae9b9"
    ),
    "LICENSE": "00264dea01f101505cb70ec2396b5967a8fdcb09603e182c9c8d7563bf6ea1ba",
}

ALLOWED_RUNTIME_ITEM_TYPES: Final[frozenset[str]] = frozenset(
    {
        "AgentMessageThreadItem",
        "PlanThreadItem",
        "ReasoningThreadItem",
        "UserMessageThreadItem",
        "CommandExecutionThreadItem",
        "FileChangeThreadItem",
        "McpToolCallThreadItem",
        "HookPromptThreadItem",
        "ContextCompactionThreadItem",
        "ImageViewThreadItem",
    }
)
DISALLOWED_RUNTIME_ITEM_TYPES: Final[frozenset[str]] = frozenset(
    {
        "WebSearchThreadItem",
        "SubAgentActivityThreadItem",
        "CollabAgentToolCallThreadItem",
        "DynamicToolCallThreadItem",
        "ImageGenerationThreadItem",
        "EnteredReviewModeThreadItem",
        "ExitedReviewModeThreadItem",
        "SleepThreadItem",
    }
)

CREDENTIAL_REDACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "api_key",
        "apiKey",
        "openaiApiKey",
        "arkApiKey",
        "auth",
    }
)
BODY_REDACT_KEYS: Final[frozenset[str]] = frozenset(
    {
        "aggregatedOutput",
        "aggregated_output",
        "contentItems",
        "content_items",
        "output",
        "result",
        "results",
    }
)
SECRET_REDACT_KEYS: Final[frozenset[str]] = CREDENTIAL_REDACT_KEYS | BODY_REDACT_KEYS
RECEIPT_CREDENTIAL_FIELD_NAMES: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "api_key",
        "apiKey",
        "bearer",
    }
)

AUTH_CACHE_BASENAME: Final = "auth.json"
FAKE_CANARY_SECRET: Final = "WB_FAKE_SECRET_DO_NOT_LIVE_USE_9f3c2a1b"
CANARY_SECRET_FILENAME: Final = "fake-remember-credential"
LIVE_GATES: Final[tuple[str, ...]] = (
    "cloud_role_workspace_ingest_and_signed_receipt",
    "external_workspace_and_lite_task_corpus",
    "codex_chatgpt_subscription_task_execution",
    "live_credential_isolation_canary_turn",
    "official_anthropic_compatible_judge",
    "docker_container_equivalence",
)
AUTOMATIC_LIVE_GATES: Final[tuple[str, ...]] = (
    "codex_chatgpt_account_attestation",
    "codex_keyring_credentials_and_disposable_home",
    "remember_mcp_stdio_discovery",
    "live_credential_isolation_canary_turn",
)
STRUCTURAL_CANARY_DETAIL: Final = (
    "structural credential-isolation canary: scanned generated configs and "
    "artifacts only; no Codex subscription turn was run"
)
