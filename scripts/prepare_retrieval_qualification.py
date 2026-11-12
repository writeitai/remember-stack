"""Copy a stopped benchmark PostgreSQL volume into two isolated SQL test stores.

The original is mounted read-only by the copy process and is never started or
migrated. This operator creates only task-labelled volumes/containers, refuses
name reuse, and records private connection settings separately from evidence.
No ingestion, model calls, source reset or original answer edits occur.
"""

import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any


def _docker(*, arguments: list[str]) -> str:
    """Run a bounded Docker command without echoing configuration or credentials."""
    return subprocess.check_output(["docker", *arguments], text=True).strip()


def prepare(*, source: str, prefix: str, output: Path) -> None:
    """Copy one stopped native volume and launch independent reference/target DBs."""
    if not prefix.startswith("d153-") or any(
        c not in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in prefix
    ):
        raise ValueError("qualification prefix must be an owned d153- identifier")
    if output.exists():
        raise ValueError(
            "private output already exists; inspect the prior qualification"
        )
    original: dict[str, Any] = json.loads(_docker(arguments=["inspect", source]))[0]
    if original["State"]["Status"] != "exited":
        raise ValueError("source must be cleanly stopped before its physical copy")
    if original["State"]["ExitCode"] != 0:
        raise ValueError("source did not stop cleanly; use its verified native backup")
    mounts = [
        m
        for m in original["Mounts"]
        if m["Destination"] == "/var/lib/postgresql" and m["Type"] == "volume"
    ]
    if len(mounts) != 1:
        raise ValueError("source must have exactly the native PostgreSQL volume")
    source_volume = str(mounts[0]["Name"])
    image = str(original["Image"])
    environment = dict(
        item.split("=", 1) for item in original["Config"]["Env"] if "=" in item
    )
    settings: dict[str, Any] = {
        "source_container": source,
        "source_volume": source_volume,
        "image_id": image,
        "source_mounted_read_only": True,
        "provider_calls": 0,
        "databases": {},
    }
    for variant in ("reference", "target"):
        name = f"{prefix}-{variant}"
        volume = name + "-pg"
        for kind, identifier in (("container", name), ("volume", volume)):
            found = subprocess.run(
                ["docker", kind, "inspect", identifier],
                capture_output=True,
                check=False,
            )
            if found.returncode == 0:
                raise ValueError(
                    "qualification name already exists; never overwrite a prior store"
                )
    output.parent.mkdir(parents=True, exist_ok=True)
    for variant in ("reference", "target"):
        name = f"{prefix}-{variant}"
        volume = name + "-pg"
        _docker(
            arguments=[
                "volume",
                "create",
                "--label",
                f"rememberstack.task={prefix}",
                volume,
            ]
        )
        _docker(
            arguments=[
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "0",
                "--entrypoint",
                "/bin/sh",
                "--mount",
                f"type=volume,source={source_volume},target=/source,readonly",
                "--mount",
                f"type=volume,source={volume},target=/target",
                image,
                "-c",
                "cd /source && tar cf - . | tar xf - -C /target",
            ]
        )
        with tempfile.NamedTemporaryFile(
            mode="w", prefix="d153-postgres-", delete=False
        ) as handle:
            os.chmod(handle.name, 0o600)
            for key, value in environment.items():
                if "\n" in value:
                    raise ValueError("multiline container environment is not supported")
                handle.write(f"{key}={value}\n")
            private_env = handle.name
        try:
            _docker(
                arguments=[
                    "run",
                    "-d",
                    "--name",
                    name,
                    "--label",
                    f"rememberstack.task={prefix}",
                    "--shm-size",
                    "512m",
                    "--publish",
                    "127.0.0.1::5432",
                    "--env-file",
                    private_env,
                    "--mount",
                    f"type=volume,source={volume},target=/var/lib/postgresql",
                    image,
                    *original["Config"]["Cmd"],
                ]
            )
        finally:
            Path(private_env).unlink(missing_ok=True)
        inspect = json.loads(_docker(arguments=["inspect", name]))[0]
        port = int(inspect["NetworkSettings"]["Ports"]["5432/tcp"][0]["HostPort"])
        settings["databases"][variant] = {
            "container": name,
            "volume": volume,
            "host": "127.0.0.1",
            "port": port,
            "username": environment["POSTGRES_USER"],
            "password": environment["POSTGRES_PASSWORD"],
            "database": environment["POSTGRES_DB"],
        }
        # Preserve access metadata at every successful launch, even if the
        # second copy fails. No automatic cleanup ever risks an original.
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as handle:
            json.dump(settings, handle, indent=2)
        print(
            json.dumps(
                {
                    "variant": variant,
                    "container": name,
                    "port": port,
                    "source_read_only": True,
                }
            ),
            flush=True,
        )


def main() -> None:
    """Require an explicit source container, task name and private output path."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    args = parser.parse_args()
    prepare(source=args.source, prefix=args.prefix, output=args.private_output)


if __name__ == "__main__":
    main()
