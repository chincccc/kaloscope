import re
from pathlib import Path

from app.core.exceptions import ErrorCode, KaloscopeException

TAG_PATTERN = re.compile(r"#([^#\s_.]+)")
INVALID_TAG_PATTERN = re.compile(r"[#\s_.<>:\"/\\|?*\x00-\x1f]")


def filename_tags(name: str) -> list[str]:
    """Extract unique hash-prefixed tags from a file or directory name."""
    tags: list[str] = []
    seen: set[str] = set()
    for match in TAG_PATTERN.finditer(name):
        tag = match.group(1)
        folded = tag.casefold()
        if folded not in seen:
            tags.append(tag)
            seen.add(folded)
    return tags


def normalize_filename_tags(tags: list[str]) -> list[str]:
    """Validate and de-duplicate tags before writing them to a resource name."""
    normalized: list[str] = []
    seen: set[str] = set()
    for value in tags:
        tag = value.strip().lstrip("#")
        if not tag or INVALID_TAG_PATTERN.search(tag):
            raise KaloscopeException(ErrorCode.BAD_REQUEST)
        folded = tag.casefold()
        if folded not in seen:
            normalized.append(tag)
            seen.add(folded)
    return normalized


def tagged_resource_name(name: str, tags: list[str], *, directory: bool) -> str:
    """Replace filename tags while preserving the base name and file suffix."""
    suffix = "" if directory else Path(name).suffix
    stem = name if directory else name[: -len(suffix)] if suffix else name
    normalized = normalize_filename_tags(tags)
    base = TAG_PATTERN.sub("", stem).rstrip(" _.")
    if not base and not normalized:
        raise KaloscopeException(ErrorCode.BAD_REQUEST)
    tag_text = " ".join(f"#{tag}" for tag in normalized)
    separator = " " if base and tag_text else ""
    return f"{base}{separator}{tag_text}{suffix}"
