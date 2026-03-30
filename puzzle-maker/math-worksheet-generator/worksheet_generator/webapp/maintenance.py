from __future__ import annotations

from pathlib import Path
import shutil

from .repository import AppRepository


def _run_directory_name(run_id: int) -> str:
    return f"run-{run_id:05d}"


def orphan_run_directories(*, artifact_root: Path, known_run_ids: list[int]) -> list[Path]:
    known = {_run_directory_name(run_id) for run_id in known_run_ids}
    if not artifact_root.exists():
        return []
    return sorted(
        path
        for path in artifact_root.iterdir()
        if path.is_dir() and path.name.startswith("run-") and path.name not in known
    )


def maintenance_snapshot(*, repository: AppRepository, artifact_root: Path) -> dict[str, object]:
    counts = repository.counts()
    known_run_ids = repository.list_worksheet_run_ids()
    orphan_dirs = orphan_run_directories(artifact_root=artifact_root, known_run_ids=known_run_ids)
    run_dirs = sorted(path for path in artifact_root.iterdir() if path.is_dir() and path.name.startswith("run-")) if artifact_root.exists() else []
    return {
        "counts": counts,
        "artifact_root_exists": artifact_root.exists(),
        "run_directory_count": len(run_dirs),
        "orphan_run_directory_count": len(orphan_dirs),
        "orphan_run_directories": [path.name for path in orphan_dirs],
    }


def prune_orphan_artifacts(*, repository: AppRepository, artifact_root: Path) -> dict[str, object]:
    orphan_dirs = orphan_run_directories(artifact_root=artifact_root, known_run_ids=repository.list_worksheet_run_ids())
    removed: list[str] = []
    removed_file_count = 0
    for path in orphan_dirs:
        removed_file_count += sum(1 for child in path.rglob("*") if child.is_file())
        shutil.rmtree(path, ignore_errors=False)
        removed.append(path.name)
    return {
        "removed_run_directories": removed,
        "removed_run_directory_count": len(removed),
        "removed_file_count": removed_file_count,
        "post_prune_snapshot": maintenance_snapshot(repository=repository, artifact_root=artifact_root),
    }
