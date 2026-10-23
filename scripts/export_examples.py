"""Regenerate editable JSON examples after an intentional seed-definition change."""

import json
from pathlib import Path

from korsik.seed import definition

root = Path(__file__).resolve().parents[1]
(root / "examples").mkdir(exist_ok=True)
(root / "examples" / "onboarding.json").write_text(
    json.dumps(definition(), ensure_ascii=False, indent=2), encoding="utf-8"
)
