"""Result directories and their manifests: every published number traces back to one of these."""

from __future__ import annotations

import datetime as dt
import json
import platform
import subprocess
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
RESULTS_DIR = REPO_ROOT / "analysis" / "results"


def agent_run_manifests(
    root: Path, labels: Collection[str], *, recorded_only: bool = False
) -> Iterator[tuple[Path, dict[str, Any]]]:
    """Selected agent runs in chronological filename order; row files are read separately."""
    for directory in sorted(root.glob("*-agent-run")):
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        config = manifest["config"]
        if recorded_only and not config.get("record"):
            continue
        if config.get("label") in labels:
            yield directory, manifest


def agent_run_rows(directory: Path) -> Iterator[dict[str, Any]]:
    for line in (directory / "agent_runs.jsonl").read_text(encoding="utf-8").splitlines():
        yield json.loads(line)


def git_state() -> dict[str, object]:
    """Commit and dirtiness, falling back to reading .git directly when the git binary is absent."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],  # noqa: S607
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(
                ["git", "status", "--porcelain", "--", ".", ":(exclude)analysis/results"],  # noqa: S607
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
        )
        return {"sha": sha, "dirty": dirty}
    except OSError, subprocess.CalledProcessError:
        head = (REPO_ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
        if head.startswith("ref: "):
            ref = REPO_ROOT / ".git" / head[5:]
            sha = ref.read_text(encoding="utf-8").strip() if ref.exists() else "unknown"
        else:
            sha = head
        return {"sha": sha, "dirty": None}


@dataclass
class Run:
    kind: str
    config: dict[str, object]
    started: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.UTC))

    @property
    def run_id(self) -> str:
        return f"{self.started:%Y%m%dT%H%M%SZ}-{self.kind}"

    @property
    def directory(self) -> Path:
        path = RESULTS_DIR / self.run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write(self, name: str, payload: object) -> Path:
        path = self.directory / name
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        return path

    def finish(self, models: dict[str, str]) -> Path:
        manifest = {
            "run_id": self.run_id,
            "kind": self.kind,
            "git": git_state(),
            "started": self.started.isoformat(),
            "finished": dt.datetime.now(dt.UTC).isoformat(),
            "python": platform.python_version(),
            "machine": platform.machine(),
            "models": models,
            "config": self.config,
        }
        return self.write("manifest.json", manifest)
