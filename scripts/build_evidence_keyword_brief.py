"""Build source-linked commercial keyword cues without hosted/model calls."""

import argparse
import json
from pathlib import Path

from aeiou.evidence_cues import keyword_brief
from aeiou.research import package, research


def build(case_id):
    pkg = package()
    case = pkg.cases[case_id]
    scoped = pkg.for_case(case)
    intelligence = research(case, scoped)
    return keyword_brief(case, intelligence, case["suppliers"][0])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--case", required=True, choices=["case:P10", "case:P18"])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = build(args.case)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps({"case": args.case, "themes": len(result["themes"]), "output": str(args.output)}))
