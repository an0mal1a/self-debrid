"""Local shared-filesystem boundary. No path is serialized by the engine."""

from pathlib import Path

from self_debrid.domain.torrents import TorrentError, relative_parts


def safe_local_path(root: Path, directory: str, relative: str) -> Path:
    try:
        expected = root.resolve(strict=True)
        folder = Path(directory)
        if not folder.is_absolute() or folder.is_symlink():
            raise TorrentError("unsafe_path")
        resolved_folder = folder.resolve(strict=True)
        if resolved_folder == expected or not resolved_folder.is_relative_to(expected):
            raise TorrentError("unsafe_path")
        target = folder
        for component in relative_parts(relative):
            target = target / component
            if target.is_symlink():
                raise TorrentError("unsafe_path")
        resolved = target.resolve()
        if not resolved.is_relative_to(resolved_folder):
            raise TorrentError("unsafe_path")
        return resolved
    except (OSError, ValueError, RuntimeError):
        raise TorrentError("unsafe_path") from None
