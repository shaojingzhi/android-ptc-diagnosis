from __future__ import annotations

from pathlib import Path
import yaml

from .models import TriageRequest


def load_case(case_dir: str | Path) -> tuple[TriageRequest, dict]:
    case_dir = Path(case_dir)
    meta = yaml.safe_load((case_dir / "case.yaml").read_text(encoding="utf-8"))
    request = TriageRequest(**meta["request"])
    return request, meta["expected"]


def load_runtime_state(case_dir: str | Path) -> dict:
    path = Path(case_dir) / "runtime_state.yaml"
    if not path.exists():
        return {}
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
