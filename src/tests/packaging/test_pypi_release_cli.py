"""Actual release-check CLI coverage for per-project interrupted publication."""

from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
import sys

import pytest
from scripts import check_pypi_release


@pytest.mark.parametrize("project", ["remember", "rememberstack"])
@pytest.mark.parametrize("remote_count", [0, 1, 2])
def test_cli_selects_only_missing_files_for_exact_project(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    project: str,
    remote_count: int,
) -> None:
    """Run the real argv/file selection path with both distributions co-located."""
    distribution = tmp_path / "dist"
    distribution.mkdir()
    files: dict[str, bytes] = {}
    for package in ("remember", "rememberstack"):
        for extension in ("py3-none-any.whl", "tar.gz"):
            name = (
                f"{package}-0.17.0-{extension}"
                if extension.endswith("whl")
                else (f"{package}-0.17.0.{extension}")
            )
            files[name] = name.encode()
            (distribution / name).write_bytes(files[name])
    wanted = sorted(name for name in files if name.startswith(project + "-"))
    uploaded = wanted[:remote_count]
    payload = {
        "urls": [
            {"filename": name, "digests": {"sha256": sha256(files[name]).hexdigest()}}
            for name in uploaded
        ]
    }

    def response(url: str, *, timeout: int) -> BytesIO:
        """Return metadata only for the exact requested package/version."""
        assert url == f"https://pypi.org/pypi/{project}/0.17.0/json"
        assert timeout == 30
        return BytesIO(json.dumps(payload).encode())

    output = tmp_path / "publish"
    monkeypatch.setattr(check_pypi_release, "urlopen", response)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_pypi_release.py",
            "--project",
            project,
            "--version",
            "0.17.0",
            "--dist",
            str(distribution),
            "--missing-dir",
            str(output),
        ],
    )
    check_pypi_release.main()
    assert capsys.readouterr().out.strip() == (
        "identical" if remote_count == 2 else "partial"
    )
    assert sorted(path.name for path in output.iterdir()) == wanted[remote_count:]
    for path in output.iterdir():
        assert path.read_bytes() == files[path.name]
