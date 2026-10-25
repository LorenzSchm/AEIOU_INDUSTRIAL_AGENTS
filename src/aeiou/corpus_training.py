"""Train hosted response predictors with an explicitly measured local-data ablation."""

import argparse
import json
from collections import Counter
from pathlib import Path

from .response_learning import assign_groups
from .response_training import fit, metrics, predict
from .training_corpus import dump, write_jsonl

TARGETS = ("package_matched", "direct_cost_improved")


def fold_data(rows, held_out):
    """Connected case/supplier groups include local rows before making folds."""
    train = [r for r in rows if r["group"] != held_out]
    test = [r for r in rows if r["group"] == held_out and r["source_kind"] == "hosted"]
    assert not {r["case_id"] for r in train} & {r["case_id"] for r in test}
    assert not {r["supplier_id"] for r in train} & {r["supplier_id"] for r in test}
    assert not {r["session_id"] for r in train} & {r["session_id"] for r in test}
    return train, test


def train_corpus(corpus, output):
    corpus, output = Path(corpus), Path(output)
    output.mkdir(parents=True, exist_ok=False)
    rows = [json.loads(line) for line in (corpus / "responses.jsonl").read_text().splitlines()]
    # Terminal counters are useful risk examples in the archive, but are outside these two predictors' scope.
    rows = [r for r in rows if r["previous_status"] not in {"final", "final_offer", "agreed"}]
    rows = [r for r in rows if all(r["y"].get(t) is not None for t in TARGETS)]
    assign_groups(rows)
    groups = sorted({r["group"] for r in rows if r["source_kind"] == "hosted"})
    if len(groups) < 3:
        raise ValueError("Need at least three independent hosted case/supplier groups")
    folds, predictions = [], []
    for group in groups:
        training, test = fold_data(rows, group)
        hosted = [r for r in training if r["source_kind"] == "hosted"]
        candidates = {"hosted_only": hosted, "hosted_plus_local": training}
        models = {name: {t: fit(part, t) for t in TARGETS} for name, part in candidates.items()}
        folds.append(
            {"group": group, "hosted_training": len(hosted), "mixed_training": len(training), "test": len(test)}
        )
        for row in test:
            predictions.append(
                {
                    **row,
                    "predictions": {
                        t: {
                            **{name: predict(m[t], row["x"]) for name, m in models.items()},
                            "base_rate": models["hosted_only"][t]["prior"],
                        }
                        for t in TARGETS
                    },
                }
            )
        print(json.dumps({"held_out": group, "test_rows": len(test)}), flush=True)
    comparisons = {
        t: {
            name: metrics(predictions, [r["predictions"][t][name] for r in predictions], t)
            for name in ("hosted_only", "hosted_plus_local", "base_rate")
        }
        for t in TARGETS
    }
    models = {
        name: {t: fit(part, t) for t in TARGETS}
        for name, part in {
            "hosted_only": [r for r in rows if r["source_kind"] == "hosted"],
            "hosted_plus_local": rows,
        }.items()
    }
    report = {
        "rows": len(rows),
        "by_source": dict(Counter(r["source_kind"] for r in rows)),
        "groups": len(groups),
        "folds": folds,
        "comparisons": comparisons,
        "deployment": "offline_only",
        "model_fits": (len(groups) + 1) * len(TARGETS) * 2,
        "limitation": "Historical development; local synthetic scores never replace official scores. "
        "Group exclusion prevents same case/supplier leakage. No causal policy improvement claimed.",
    }
    dump(output / "report.json", report)
    dump(output / "models.json", models)
    write_jsonl(output / "predictions.jsonl", predictions)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(train_corpus(args.corpus, args.output), indent=2))
