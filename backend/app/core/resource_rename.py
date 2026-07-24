import asyncio
import re
from pathlib import Path

from app.core.exceptions import ErrorCode, KaloscopeException

INVALID_RESOURCE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def normalize_resource_name(value: str, suffix: str = "") -> str:
    """Validate a portable file name and remove an optional existing suffix."""
    name = value.strip()
    if suffix and name.casefold().endswith(suffix.casefold()):
        name = name[: -len(suffix)].rstrip()
    if (
        not name
        or name in {".", ".."}
        or name.endswith((" ", "."))
        or value.endswith(" ")
        or INVALID_RESOURCE_NAME.search(value)
    ):
        raise KaloscopeException(ErrorCode.BAD_REQUEST)
    return name


def rename_destination(source: Path, name: str, *, directory: bool) -> Path:
    suffix = "" if directory else source.suffix
    normalized = normalize_resource_name(name, suffix)
    return source.with_name(f"{normalized}{suffix}")


def replace_path_prefix(
    value: str | None, source: Path, destination: Path
) -> str | None:
    if not value:
        return value
    path = Path(value)
    try:
        relative = path.relative_to(source)
    except ValueError:
        return value
    return str(destination / relative)


def sidecar_destination(sidecar: Path, source: Path, destination: Path) -> Path:
    """Keep the portion after the media stem, such as .nfo or .zh-CN.ass."""
    if sidecar.parent != source.parent or not sidecar.name.startswith(source.stem):
        return sidecar
    remainder = sidecar.name[len(source.stem) :]
    return sidecar.with_name(f"{destination.stem}{remainder}")


async def rename_paths(moves: list[tuple[Path, Path]]):
    """Rename an ordered set of paths and roll completed moves back on failure."""
    effective = [(source, target) for source, target in moves if source != target]
    for source, target in effective:
        if not source.exists():
            raise KaloscopeException(ErrorCode.FILE_NOT_EXISTS)
        if target.exists():
            raise KaloscopeException(ErrorCode.NAME_ALREADY_EXISTS)

    completed: list[tuple[Path, Path]] = []
    try:
        for source, target in effective:
            await asyncio.to_thread(source.rename, target)
            completed.append((source, target))
    except Exception:
        for source, target in reversed(completed):
            if target.exists() and not source.exists():
                await asyncio.to_thread(target.rename, source)
        raise


async def rollback_paths(moves: list[tuple[Path, Path]]):
    for source, target in reversed(moves):
        if target.exists() and not source.exists():
            await asyncio.to_thread(target.rename, source)
