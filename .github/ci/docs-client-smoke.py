"""Exercise the published client's documented workflows against a fresh local engine."""

import asyncio
from datetime import datetime
from datetime import timedelta
from datetime import UTC
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
from uuid import UUID

from mcp import ClientSession
from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

import remember
from remember.credentials import credentials_path

QUERY = "SELECT doc_id, current_version_id FROM documents_live WHERE source_ref = $1"
SOURCE_DATE = datetime(2026, 9, 17, 9, 30, tzinfo=UTC)
CLI_SOURCE = "notes/docs-cli.md"
SDK_SOURCE = "notes/docs-sdk.md"


def cli(*args: str) -> str:
    """Run the installed entry point and return its standard output."""
    return subprocess.check_output(["remember", *args], text=True, timeout=30)


def check_query(*, result: dict[str, object], doc_id: str, version_id: str) -> None:
    """Require the query to complete and return the exact ingested IDs."""
    assert result["termination_reason"] == "completed", result
    assert result["rows"] == [[doc_id, version_id]], result


async def check_mcp(*, doc_id: str, version_id: str) -> None:
    """Discover tools and retrieve the CLI's document through the stdio server."""
    api_url = remember.resolve_connection().api_url
    assert api_url is not None, "Set REMEMBER_API_URL to the Compose engine"
    server = StdioServerParameters(
        command="remember",
        args=["mcp"],
        env={
            "REMEMBER_API_URL": api_url,
            "REMEMBER_CONFIG_DIR": str(credentials_path().parent),
        },
    )
    async with (
        stdio_client(server=server) as (read, write),
        ClientSession(
            read_stream=read,
            write_stream=write,
            read_timeout_seconds=timedelta(seconds=30),
        ) as session,
    ):
        initialized = await session.initialize()
        assert initialized.serverInfo.name == "remember", initialized
        assert initialized.serverInfo.version == remember.__version__, initialized
        tools = await session.list_tools()
        assert "query_sql" in {tool.name for tool in tools.tools}
        response = await session.call_tool(
            name="query_sql",
            arguments={"sql": QUERY, "parameters": [CLI_SOURCE], "max_rows": 1},
        )
        assert not response.isError, response
        (content,) = response.content
        assert isinstance(content, TextContent), response
        check_query(
            result=json.loads(content.text), doc_id=doc_id, version_id=version_id
        )
        print(
            f"MCP mcp=={version('mcp')}: remember mcp {initialized.serverInfo.version} "
            f"({initialized.protocolVersion}), tools/list and tools/call query_sql passed."
        )


def main() -> None:
    """Ingest, wait, query and check validation through the three public entry points."""
    print(f"Python {sys.version.split()[0]}, {cli('--version').strip()} (PyPI).")
    assert "ingest" in cli("--help")

    # The Compose job has no model provider credentials. These whitespace-only
    # sources can reach readiness without paid extraction or embedding calls.
    cli_input = Path("docs-cli.md")
    cli_input.write_text(" ", encoding="utf-8")
    receipt = json.loads(
        cli(
            "ingest",
            str(cli_input),
            "--source-kind",
            "file",
            "--source-ref",
            CLI_SOURCE,
            "--source-modified-at",
            SOURCE_DATE.isoformat(),
        )
    )
    assert receipt["created"] is True, receipt

    sdk_input = Path("docs-sdk.md")
    sdk_input.write_text("\n", encoding="utf-8")
    client = remember.Client.from_env()
    stored = client.ingest(
        sdk_input,
        source_kind="file",
        source_ref=SDK_SOURCE,
        source_modified_at=SOURCE_DATE,
    )
    assert stored.created, stored
    ready = client.wait_for_readiness(
        [UUID(receipt["version_id"]), stored.version_id], timeout=90, poll_interval=1
    )
    assert ready.ready, ready

    cli_result = json.loads(
        cli(
            "query",
            "sql",
            QUERY,
            "--parameters",
            json.dumps([CLI_SOURCE]),
            "--max-rows",
            "1",
        )
    )
    check_query(
        result=cli_result, doc_id=receipt["doc_id"], version_id=receipt["version_id"]
    )
    print("CLI: remember --help and ingest → remember query sql passed.")

    sdk_result = client.query_sql(sql=QUERY, parameters=[SDK_SOURCE], max_rows=1)
    check_query(
        result=sdk_result, doc_id=str(stored.doc_id), version_id=str(stored.version_id)
    )
    try:
        client.query_sql(sql=QUERY, parameters=[SDK_SOURCE], max_rows=-1)
    except remember.MemoryApiError as error:
        assert error.status_code == 422, error
    else:
        raise AssertionError("The engine accepted a negative max_rows")
    print(
        "Python SDK: Client.from_env → ingest → wait_for_readiness → query_sql and HTTP 422 passed."
    )

    asyncio.run(check_mcp(doc_id=receipt["doc_id"], version_id=receipt["version_id"]))


if __name__ == "__main__":
    main()
