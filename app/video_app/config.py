from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    root: Path
    runtime: Path

    @classmethod
    def from_environment(cls) -> "Settings":
        root = Path(
            os.environ.get("VIDEO_EDITING_ROOT", "/home/saveas/Documents/video-editing")
        ).resolve()
        runtime = Path(
            os.environ.get("VIDEO_EDITING_RUNTIME", str(root / "runtime" / "projects"))
        ).resolve()
        return cls(root=root, runtime=runtime)
