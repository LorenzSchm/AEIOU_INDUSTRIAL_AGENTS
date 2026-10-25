"""Audited P10/P18 corpus from local hosted logs and cached public server traces."""

import copy
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from arena_kit.cost import closing_terms
from arena_kit.docs import standard_terms

from .response_learning import FEATURES, _sheets, features
from .response_refinement import HISTORY_FEATURES, enrich_history
from .response_value import TARGETS as VALUE_TARGETS
from .response_value import add_target_history
from .response_value_features import EXTRA_FEATURES, complete_features, outcome

CASES = ("case:P10", "case:P18")
TARGETS = (*VALUE_TARGETS, "package_matched")
STAGE_FEATURES = ("previous_final_offer", "previous_agreed")
SCHEMA = (*FEATURES, *HISTORY_FEATURES, *EXTRA_FEATURES, *STAGE_FEATURES)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def public_message(message):
    return {
        k: copy.deepcopy(message[k])
        for k in (
            "from",
            "body",
            "status",
            "term_sheets",
            "attachments",
            "refs",
            "action",
            "message_id",
            "claim_results",
        )
        if k in message
    }


def trace_threads(snapshot, cached):
    """Decode recorded visible moves only; never ingest server reasons/private state."""
    threads = defaultdict(list)
    trace = (snapshot.get("result") or {}).get("trace", [])
    for item in trace:
        move, actor = item.get("move", {}), item.get("actor")
        if actor == "buyer":
            sid = move.get("supplier_id")
            if not sid:
                continue
            message = {
                "from": "buyer",
                "body": move.get("body", ""),
                "refs": move.get("refs", []),
                "term_sheets": move.get("term_sheets", []),
            }
            if move.get("type") != "message":
                message["action"] = {"type": move.get("type"), "term_sheet_id": move.get("term_sheet_id")}
        elif actor and actor.startswith("sup:"):
            sid = actor
            sheets = [{"term_sheet_id": move.get("offer_id"), "terms": move["terms"]}] if move.get("terms") else []
            sheets += [
                {"term_sheet_id": s.get("offer_id"), "terms": s["terms"]}
                for s in move.get("alternatives", [])
                if s.get("terms")
            ]
            message = {
                "from": sid,
                "body": move.get("text", ""),
                "status": move.get("status"),
                "term_sheets": sheets,
                "claim_results": move.get("claim_results", []),
            }
        else:
            continue
        # Reuse attachments only from the exact same recorded message. In particular
        # never attach the final PDF to an earlier supplier response.
        for previous in cached.get(sid, []):
            same_role = (previous.get("from") == "buyer") == (actor == "buyer")
            if same_role and previous.get("body", "") == message["body"] and _sheets(previous) == _sheets(message):
                if previous.get("attachments"):
                    message["attachments"] = copy.deepcopy(previous["attachments"])
                break
        threads[sid].append(public_message(message))
    return dict(threads)


def gather(roots, public_cases, case_ids=CASES):
    """Recursive file discovery, session deduplication and explicit source provenance."""
    records, snapshots, sources, excluded = defaultdict(list), defaultdict(list), [], Counter()
    seen_paths = set()
    for root in roots:
        for path in sorted(Path(root).rglob("*.json")):
            if path.resolve() in seen_paths or any(p.startswith("custom-p10-p18-") for p in path.parts):
                continue
            seen_paths.add(path.resolve())
            try:
                raw = path.read_bytes()
                value = json.loads(raw)
            except (OSError, ValueError):
                excluded["unreadable_file"] += 1
                continue
            if not isinstance(value, dict):
                continue
            cid = value.get("case_id") or (value.get("case") or {}).get("case_id")
            if not cid or (case_ids is not None and cid not in case_ids):
                continue
            source = {
                "path": str(path),
                "sha256": hashlib.sha256(raw).hexdigest(),
                "case_id": cid,
                "session_id": value.get("session_id"),
            }
            sources.append(source)
            sid = value.get("session_id")
            if value.get("mode") == "hosted" and sid and value.get("case"):
                records[sid].append((value, source))
            elif (
                sid
                and cid in public_cases
                and value.get("set") == "practice"
                and (value.get("result") or {}).get("trace")
            ):
                snapshots[sid].append((value, source))
            else:
                excluded["simulation_or_diagnostic_summary"] += 1
    runs, recovered = {}, []
    for sid in sorted(records.keys() | snapshots.keys()):
        logs = records.get(sid, [])
        selected = max(logs, key=lambda pair: sum(len(t) for t in pair[0].get("threads", {}).values()), default=None)
        if selected:
            run = copy.deepcopy(selected[0])
            case = run["case"]
        else:
            sample = snapshots[sid][0][0]
            case = public_cases[sample["case_id"]]
            run = {"case": copy.deepcopy(case), "session_id": sid, "mode": "hosted", "threads": {}}
        run["threads"] = {s: [public_message(m) for m in events] for s, events in run.get("threads", {}).items()}
        original_count = sum(map(len, run["threads"].values()))
        for snapshot, source in snapshots.get(sid, []):
            decoded = trace_threads(snapshot, run["threads"])
            if sum(map(len, decoded.values())) > sum(map(len, run["threads"].values())):
                run["threads"] = decoded
                recovered.append(
                    {
                        "session_id": sid,
                        "source": source["path"],
                        "previous_messages": original_count,
                        "recovered_messages": sum(map(len, decoded.values())),
                    }
                )
        if case_ids is not None and case["case_id"] not in case_ids:
            raise ValueError("Unexpected case")
        dates = sorted(r.get("created_at") for r, _ in logs if r.get("created_at"))
        run["created_at"] = dates[0] if dates else None
        run["corpus_sources"] = [s for _, s in [*logs, *snapshots.get(sid, [])]]
        # Do not carry result traces, prompts, reasoning, diagnostics or hidden scores
        # into the model-ready raw archive.
        runs[sid] = {k: run[k] for k in ("case", "session_id", "mode", "threads", "created_at", "corpus_sources")}
    return runs, {
        "sources": sources,
        "excluded_files": dict(excluded),
        "recovered": recovered,
        "unique_sessions": len(runs),
        "duplicate_log_copies": sum(max(0, len(v) - 1) for v in records.values()),
    }


def extract(run):
    """Learn responses, including valid observed final counters, without imitating them."""
    rows, skipped = [], Counter()
    case = run["case"]
    for sid, events in run["threads"].items():
        for i, outgoing in enumerate(events):
            if outgoing.get("from") != "buyer":
                continue
            if outgoing.get("action"):
                skipped["action_only_close"] += 1
                continue
            if not _sheets(outgoing):
                skipped["message_without_package"] += 1
                continue
            prefix = events[:i]
            previous = next((m for m in reversed(prefix) if m.get("from") != "buyer"), {})
            if previous.get("status") in {"walked_away", "no_bid"}:
                skipped["already_closed"] += 1
                continue
            if i + 1 >= len(events):
                skipped["missing_response"] += 1
                continue
            response = events[i + 1]
            if response.get("from") in {"buyer", "arena"} or previous.get("from") == "arena":
                skipped["transport_or_unpaired_response"] += 1
                continue
            if "timeout" in response.get("body", "").lower():
                skipped["timeout_response"] += 1
                continue
            try:
                vector = features(case, prefix, outgoing)
                extra, evidence = complete_features(case, prefix, outgoing)
                label = outcome(case, prefix, response)
                std = standard_terms(prefix)
                replies = [] if label["supplier_exit"] else [closing_terms(case, t, std) for t in _sheets(response)]
                matched = any(all(r.get(k) == v for k, v in p.items()) for p in _sheets(outgoing) for r in replies)
                # Primary quote gain agrees with the value target; alternatives
                # are advertised choices, not assumed executable offers.
                label.update(
                    package_matched=int(matched),
                    direct_cost_improved=int(
                        label["quoted_cost_gain"] is not None and label["quoted_cost_gain"] > 1e-9
                    ),
                )
                if label["quoted_cost_gain"] is None and not label["supplier_exit"]:
                    skipped["response_without_quote"] += 1
                    continue
            except (KeyError, TypeError, ValueError, ZeroDivisionError):
                skipped["invalid_or_incomplete_terms"] += 1
                continue
            stage = [
                float(previous.get("status") in {"final", "final_offer"}),
                float(previous.get("status") == "agreed"),
            ]
            rows.append(
                {
                    "session_id": run["session_id"],
                    "case_id": case["case_id"],
                    "supplier_id": sid,
                    "turn": i,
                    "created_at": run["created_at"],
                    "group": case["case_id"],
                    "x": vector,
                    "extra": extra + stage,
                    "signal_evidence": evidence,
                    "y": label,
                    "previous_status": previous.get("status"),
                    "response_status": response.get("status"),
                }
            )
    enriched = enrich_history(rows)
    for row in enriched:
        row["v"] = row["z"] + row.pop("extra")
    return add_target_history(enriched), dict(skipped)


def live_features(case, supplier_id, prefix, proposal):
    if case["case_id"] not in CASES:
        raise ValueError("This specialist supports P10 and P18 only")
    prior, _ = extract(
        {"mode": "hosted", "case": case, "session_id": "live", "created_at": None, "threads": {supplier_id: prefix}}
    )
    current = {
        "session_id": "live",
        "supplier_id": supplier_id,
        "case_id": case["case_id"],
        "group": case["case_id"],
        "turn": len(prefix),
        "x": features(case, prefix, proposal),
        "y": {t: None for t in TARGETS},
    }
    current = enrich_history(prior + [current])[-1]
    extra, evidence = complete_features(case, prefix, proposal)
    previous = next(m for m in reversed(prefix) if m.get("from") != "buyer")
    current["v"] = (
        current["z"]
        + extra
        + [float(previous.get("status") in {"final", "final_offer"}), float(previous.get("status") == "agreed")]
    )
    current["signal_evidence"] = evidence
    return add_target_history(prior + [current])[-1]


def build(roots, public_cases, case_ids=CASES):
    runs, audit = gather(roots, public_cases, case_ids)
    rows, excluded = [], Counter()
    for run in runs.values():
        examples, reasons = extract(run)
        rows.extend(examples)
        excluded.update(reasons)
    rows.sort(key=lambda r: (r["created_at"] or "", r["session_id"], r["supplier_id"], r["turn"]))
    audit.update(
        excluded_steps=dict(excluded),
        examples=len(rows),
        by_case=dict(Counter(r["case_id"] for r in rows)),
        usable_sessions=len({r["session_id"] for r in rows}),
        sha256=digest(rows),
        observed_post_final_examples=sum(r["previous_status"] in {"final", "final_offer", "agreed"} for r in rows),
        data_policy="Visible messages and observed responses only; "
        "no server reasons, private scores or future evidence. "
        "Final-offer counters are observed risk examples, never instructions to over-negotiate.",
    )
    return runs, rows, audit
