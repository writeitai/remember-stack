"""Read-only SQL prototypes for the 2026-11-12 retrieval analysis, never runtime.

The captured original SQL and each bounded-lookup variant are independently
executed and explained. All authority views and predicates remain present.
These experiments are not a production implementation or a recall benchmark.
"""

import argparse
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any
from uuid import UUID

from scripts.diagnose_retrieval_sql import _clean
from scripts.diagnose_retrieval_sql import _dump
from scripts.diagnose_retrieval_sql import _error
from scripts.diagnose_retrieval_sql import DiagnosticSettings
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


def _parameters(*, values: dict[str, Any], vector: str) -> dict[str, Any]:
    """Recreate native UUID/time bind types and the labelled diagnostic vector."""
    result: dict[str, Any] = {}
    for key, value in values.items():
        if key == "query_vector":
            result[key] = vector
        elif key == "deployment_id":
            result[key] = UUID(value)
        elif key.endswith("_ids"):
            result[key] = [UUID(item) for item in value]
        elif key in {"scope_lower", "scope_upper", "evaluated_at"}:
            result[key] = datetime.fromisoformat(value)
        else:
            result[key] = value
    return result


def _fact_fence(statement: str) -> str:
    """Keep fact authority identical but bind each lookup to its fact ID."""
    pattern = (
        r"JOIN memory_v1\.facts_visible_history AS fact\s+"
        r"ON fact\.deployment_id = indexed\.deployment_id\s+"
        r"AND fact\.fact_kind = '(relation|observation)'\s+"
        r"AND fact\.fact_id = indexed\.(relation_id|observation_id)"
    )

    def replacement(match: re.Match[str]) -> str:
        """Fence an existing complete authority view behind one candidate ID."""
        kind, column = match.groups()
        return (
            "JOIN LATERAL (SELECT 1 FROM memory_v1.facts_visible_history AS eligible "
            "WHERE eligible.deployment_id=indexed.deployment_id "
            f"AND eligible.fact_kind='{kind}' AND eligible.fact_id=indexed.{column} "
            "OFFSET 0) AS fact ON true"
        )

    rewritten, count = re.subn(pattern, replacement, statement)
    if count != 2:
        raise ValueError("expected exactly the two captured scoped fact branches")
    return rewritten


def _mention_fence(statement: str) -> str:
    """Start mention authority checks from candidate claims' base mention IDs."""
    original = "FROM memory_v1.mentions_live AS mention"
    if statement.count(original) != 1:
        raise ValueError("expected exactly one captured claim coverage lookup")
    replacement = """
        FROM mentions AS raw_mention
        CROSS JOIN LATERAL (
            SELECT live.deployment_id, live.claim_id, live.resolved_entity_id
            FROM memory_v1.mentions_live AS live
            WHERE live.deployment_id=raw_mention.deployment_id
              AND live.mention_id=raw_mention.mention_id
              AND live.claim_id=c.claim_id
            OFFSET 0
        ) AS mention
    """
    rewritten = statement.replace(original, replacement)
    return rewritten.replace(
        "WHERE mention.deployment_id = c.deployment_id",
        "WHERE raw_mention.deployment_id = c.deployment_id "
        "AND raw_mention.claim_id=c.claim_id "
        "AND mention.deployment_id = c.deployment_id",
    )


def _measure(
    *,
    engine: Engine,
    statement: str,
    parameters: dict[str, Any],
    name: str,
    output: Path,
    seconds: float,
) -> None:
    """Record completed rows and a bounded actual plan in independent snapshots."""
    (output / f"{name}.sql").write_text(statement + "\n")
    result: dict[str, Any] = {
        "name": name,
        "timeout_seconds": seconds,
        "parameters": _clean(parameters),
        "read_only": True,
        "controls": "normal planner; jit off; parallel workers 0; transaction_timeout 0",
    }
    for mode in ("execute", "plan"):
        started = time.monotonic()
        measured: dict[str, Any] = {}
        try:
            with engine.connect() as connection:
                connection.exec_driver_sql(
                    f"SET LOCAL statement_timeout={int(seconds * 1000)}"
                )
                connection.exec_driver_sql("SET LOCAL transaction_timeout=0")
                connection.exec_driver_sql("SET LOCAL jit=off")
                connection.exec_driver_sql(
                    "SET LOCAL max_parallel_workers_per_gather=0"
                )
                if mode == "execute":
                    rows = (
                        connection.exec_driver_sql(statement, parameters)
                        .mappings()
                        .all()
                    )
                    encoded = sorted(
                        json.dumps(dict(row), sort_keys=True, default=str)
                        for row in rows
                    )
                    measured.update(
                        rows=len(rows),
                        rows_sha256=hashlib.sha256(
                            "\n".join(encoded).encode()
                        ).hexdigest(),
                    )
                else:
                    measured["plan"] = connection.exec_driver_sql(
                        "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) "
                        + statement,
                        parameters,
                    ).scalar_one()
        except Exception as error:
            measured["error"] = _error(error)
        measured["elapsed_seconds"] = time.monotonic() - started
        result[mode] = measured
        _dump(path=output / f"{name}.json", value=_clean(result))
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "parameters"},
            default=str,
        )[:800],
        flush=True,
    )


def main() -> None:
    """Compare original and lookup-fenced SQL under fixed, single-reader bounds."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seconds", type=float, default=20)
    arguments = parser.parse_args()
    if not 0 < arguments.seconds <= 120:
        parser.error("seconds must be positive and at most 120")
    engine = create_engine(
        DiagnosticSettings().database_url.get_secret_value(),
        pool_size=1,
        max_overflow=0,
        hide_parameters=True,
        pool_pre_ping=True,
        connect_args={
            "options": "-c default_transaction_read_only=on -c application_name=locomo-sql-compare"
        },
    )
    arguments.output.mkdir(parents=True, exist_ok=True)
    try:
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SHOW transaction_read_only").scalar_one()
                == "on"
            )
            vector = connection.exec_driver_sql(
                "SELECT embedding::text FROM observations WHERE embedding IS NOT NULL "
                "AND vector_norm(embedding)>0 ORDER BY observation_id LIMIT 1"
            ).scalar_one()
        for directory in sorted(arguments.capture.glob("scoped-*")):
            rows = json.loads((directory / "statements.json").read_text())
            slow = max(rows, key=lambda row: row.get("execute_seconds", 0))
            parameters = _parameters(values=slow["parameters"], vector=vector)
            fact = directory.name == "scoped-facts_context"
            variants = [
                (
                    "facts-native" if fact else "claims-native",
                    slow["statement"],
                    parameters,
                )
            ]
            if fact:
                variants.append(
                    ("facts-fenced", _fact_fence(slow["statement"]), parameters)
                )
            else:
                for count in (1, 16):
                    variants.append(
                        (
                            f"claims-native-{count}",
                            slow["statement"],
                            {
                                **parameters,
                                "claim_ids": parameters["claim_ids"][:count],
                            },
                        )
                    )
                variants.append(
                    ("claims-fenced", _mention_fence(slow["statement"]), parameters)
                )
            for name, statement, bindings in variants:
                _measure(
                    engine=engine,
                    statement=statement,
                    parameters=bindings,
                    name=name,
                    output=arguments.output,
                    seconds=arguments.seconds,
                )
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
