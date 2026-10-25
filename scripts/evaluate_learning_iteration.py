"""Score a new public exchange before using it to update response-model weights."""

import argparse
import json
from pathlib import Path
from statistics import mean

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms

from aeiou.conditional_cost import forecast_conditional_cost
from aeiou.custom_model import CustomResponseModel
from aeiou.custom_model_data import extract
from aeiou.general_context import snapshot_context
from aeiou.research import package, research
from aeiou.terminal_learning import terminal_label


def evaluate(run_path, model_paths):
    run = json.loads(Path(run_path).read_text())
    rows, excluded = extract(run)
    pkg = package()
    case = run["case"]
    intelligence = research(case, pkg.for_case(case))
    reports = []
    for path in model_paths:
        model = CustomResponseModel(path)
        dataset = json.loads((model.path.parent / "tactical-dataset.json").read_text())
        if run["session_id"] in {r["session_id"] for r in dataset}:
            raise ValueError("Cannot evaluate predictive benefit on a session already used in training")
        conditional_head = model.artifact.get("conditional_final_cost", {})
        if run["session_id"] in conditional_head.get("training_sessions", []):
            raise ValueError("Cannot evaluate conditional cost on a session already used in training")
        predictions = []
        errors = []
        conditional_predictions = []
        conditional_errors = []
        for row in rows:
            sid, turn = row["supplier_id"], row["turn"]
            events = run["threads"][sid]
            forecast = model.forecast(
                case, sid, events[:turn], events[turn], context=snapshot_context(case, sid, intelligence)
            )
            actual = row["y"]["quoted_cost_gain"]
            predicted = forecast.get("predictions", {}).get("quoted_cost_gain")
            if predicted is not None and actual is not None:
                errors.append(predicted - actual)
            predictions.append(
                {
                    "turn": turn,
                    "actual_gain": actual,
                    "predicted_gain": predicted,
                    "status": forecast["status"],
                    "domain": forecast.get("domain"),
                }
            )
            if conditional_head:
                conditional = forecast_conditional_cost(model.artifact, case, sid, events[:turn], events[turn])
                label, reason = terminal_label(case, events)
                actual_cost = None
                if label and label["terminal_deal"] == 1:
                    final = next(e for e in reversed(events) if e.get("status") == "agreed")
                    actual_cost = total_cost(case, final["term_sheets"][0]["terms"], standard_terms(events))
                expected = conditional.get("expected_final_cost_given_agreement")
                if expected is not None and actual_cost is not None:
                    conditional_errors.append(expected - actual_cost)
                conditional_predictions.append(
                    {
                        "turn": turn,
                        "forecast": conditional,
                        "actual_accepted_cost": actual_cost,
                        "terminal_label": reason,
                    }
                )
        reports.append(
            {
                "path": str(path),
                "sha256": model.sha256,
                "supported_predictions": len(errors),
                "mae": mean(abs(e) for e in errors) if errors else None,
                "mse": mean(e * e for e in errors) if errors else None,
                "predictions": predictions,
                "conditional_cost": {
                    "supported_predictions": len(conditional_errors),
                    "mae_eur": mean(abs(e) for e in conditional_errors) if conditional_errors else None,
                    "predictions": conditional_predictions,
                },
            }
        )
    return {
        "session_id": run["session_id"],
        "case_id": case["case_id"],
        "excluded_steps": dict(excluded),
        "models": reports,
        "method": "Actual sent email, public prefix only; session excluded from each model's corpus.",
        "limitation": "One session is noisy evidence. Abstentions are reported, not assigned zero error.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = evaluate(args.run, args.models)
    Path(args.output).write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
