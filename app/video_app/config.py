from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    runtime: Path
    poc_root: Path
    semantic_source_root: Path | None = None

    @classmethod
    def from_environment(cls) -> "Settings":
        root = Path(
            os.environ.get("VIDEO_EDITING_ROOT", "/home/saveas/Documents/video-editing")
        ).resolve()
        runtime = Path(
            os.environ.get("VIDEO_EDITING_RUNTIME", str(root / "runtime" / "projects"))
        ).resolve()
        poc_root = Path(
            os.environ.get("VIDEO_EDITING_POC_ROOT", str(root / "poc-morning-routine"))
        ).resolve()
        semantic_source_root = Path(
            os.environ.get(
                "VIDEO_EDITING_SEMANTIC_SOURCE_ROOT",
                str(root / "runtime" / "openstoryline"),
            )
        ).resolve()
        return cls(
            root=root,
            runtime=runtime,
            poc_root=poc_root,
            semantic_source_root=semantic_source_root,
        )
