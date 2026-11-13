"""Portable relative object paths and directory-boundary prefix matching."""

from rememberstack.model import ObjectKeyEscapesRootError


def validate_object_path(value: str, *, prefix: bool = False) -> str:
    """Refuse ambiguous paths; only a listing prefix may be empty or end in '/'."""
    normalized = value.rstrip("/") if prefix else value
    if prefix and value == "":
        return ""
    if (
        not normalized
        or "\\" in normalized
        or "\x00" in normalized
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise ObjectKeyEscapesRootError(
            f"object path {value!r} is not a safe relative path"
        )
    return normalized


def matches_object_prefix(key: str, *, prefix: str) -> bool:
    """Match an already validated prefix at a complete path boundary."""
    return not prefix or key == prefix or key.startswith(f"{prefix}/")
