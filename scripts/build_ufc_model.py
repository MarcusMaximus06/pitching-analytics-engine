from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ufc_model import DEFAULT_DATASET_URL, build_model_artifact


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the leakage-aware HagLabs UFC winner model.")
    parser.add_argument("--source", default=DEFAULT_DATASET_URL, help="Pinned CSV URL or local CSV path.")
    parser.add_argument("--output", default="data/ufc/model.json", help="Artifact output path.")
    parser.add_argument("--validation-start", default="2024-01-01")
    args = parser.parse_args()

    frame = pd.read_csv(args.source)
    artifact = build_model_artifact(frame, validation_start=args.validation_start)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, sort_keys=True), encoding="utf-8")
    summary = {
        "model_version": artifact["model_version"],
        "training_games": artifact["training_games"],
        "trained_through": artifact["trained_through"],
        "validated": artifact["validated"],
        "market_validated": artifact["market_validated"],
        "market_weight": artifact["market_weight"],
        "validation": artifact["validation"],
        "output": str(output.resolve()),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
