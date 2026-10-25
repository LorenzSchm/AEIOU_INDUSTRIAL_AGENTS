"""Compatibility entry point for the current-prompt local evaluation."""

from .local_evaluation import evaluate_local, evaluation_plan


def benchmark(agent="original", seeds=1, label="local", seed_start=1101, parallel=2):
    if agent != "original" or seeds != 1:
        raise ValueError("The local suite uses the current original prompt and three repeats per hosted case")
    plan = evaluation_plan(seed_start=seed_start)
    return evaluate_local(label=label, seed_start=seed_start, parallel=parallel, plan=plan, attempt_limit=len(plan))
