"""Collect owned negotiations and build provenance-preserving, prefix-only datasets.

Remote collection uses GET only. Local simulation IDs are scoped by run ID.
Outcomes are separate from decision inputs; recorded actions are not endorsements.
"""

import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

from arena_kit.docs import read_document

from .custom_model_data import digest, extract, public_message, trace_threads
from .research import package
from .response_learning import _sheets


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def stamp(value):
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, UTC).isoformat()
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC).isoformat()
        except ValueError:
            pass
    return None


def collect_remote(output, client):
    """Download all visible owned sets; never open, close or advance a session."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    audit = {"sets": {}, "errors": [], "new_sessions_started": 0, "method": "GET only"}
    audit["practice_before"] = client.me().get("practice")
    jobs = []
    for name in ("sandbox", "practice", "final"):
        try:
            sessions = client.sessions(name)
            cases = {c["case_id"]: c for c in client.cases(name)} if sessions else {}
            dump(output / f"{name}-index.json", sessions)
            dump(output / f"{name}-cases.json", cases)
            audit["sets"][name] = {"listed": len(sessions), "fetched": 0, "cached": 0}
            jobs.extend((name, row, cases.get(row["case_id"])) for row in sessions)
        except Exception as exc:
            audit["errors"].append({"set": name, "error": type(exc).__name__})

    def fetch(name, row, case):
        sid = row["session_id"]
        destination = output / "runs" / f"{sid}.json"
        if destination.exists() and row.get("status") == "ended":
            previous = json.loads(destination.read_text())
            if previous.get("remote_status") == "ended" and not previous.get("attachment_errors"):
                return "cached"
        state = client.session(sid)
        threads, attachment_errors = {}, []
        for supplier in state.get("threads", {}):
            messages = client.thread(sid, supplier)["messages"]
            for message in messages:
                for att in message.get("attachments", []):
                    if att.get("attachment_id") and not att.get("text"):
                        try:
                            att["text"] = read_document(att["name"], client.attachment(sid, att["attachment_id"]))
                        except Exception as exc:
                            attachment_errors.append(
                                {"attachment_id": att["attachment_id"], "error": type(exc).__name__}
                            )
            threads[supplier] = messages
        if not case:
            raise ValueError("No accessible public case for session")
        dump(
            destination,
            {
                "mode": "hosted",
                "case_set": name,
                "case_id": row["case_id"],
                "case": case,
                "session_id": sid,
                "created_at": stamp(row.get("created_at")),
                "threads": threads,
                "remote_status": state.get("status"),
                "status": "completed" if state.get("status") == "ended" else "running",
                "result": state.get("result"),
                "remote_snapshot": state,
                "attachment_errors": attachment_errors,
                "collected_at": datetime.now(UTC).isoformat(),
                "provenance": "owned Arena session, GET only",
            },
        )
        return "fetched"

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = {pool.submit(fetch, *job): job for job in jobs}
        for index, future in enumerate(as_completed(futures), 1):
            name, row, _ = futures[future]
            try:
                audit["sets"][name][future.result()] += 1
            except Exception as exc:
                audit["errors"].append({"set": name, "session_id": row["session_id"], "error": type(exc).__name__})
            if index % 25 == 0:
                dump(output / "collection-audit.json", audit)
                print(json.dumps({"collected": index, "total": len(jobs), "errors": len(audit["errors"])}), flush=True)
    audit["practice_after"] = client.me().get("practice")
    dump(output / "collection-audit.json", audit)
    return audit


def records(value, pointer=""):
    """Find actual runs inside report lists as well as standalone run files."""
    if isinstance(value, dict):
        if value.get("mode") in {"local", "hosted"} and isinstance(value.get("threads"), dict) and value.get("case"):
            yield pointer, value
            return
        if value.get("session_id") and value.get("set") and (value.get("result") or {}).get("trace"):
            yield pointer, value
            return
        for key, item in value.items():
            if isinstance(item, (dict, list)) and key not in {"decisions", "audit", "result", "metrics"}:
                yield from records(item, f"{pointer}/{key}")
    elif isinstance(value, list):
        for i, item in enumerate(value):
            yield from records(item, f"{pointer}/{i}")


def identity(run):
    if run.get("mode") == "local":
        # Every simulator starts at sim1; never use that as the corpus primary key.
        return "local:" + (run.get("id") or digest({k: run.get(k) for k in ("case", "threads", "created_at", "seed")}))
    return "hosted:" + str(run["session_id"])


def canonical_threads(threads):
    return {
        sid: [
            {
                "from": m.get("from") if m.get("from") in {"buyer", "arena"} else "supplier",
                "body": m.get("body"),
                "status": m.get("status"),
                "terms": [s.get("terms") for s in m.get("term_sheets", [])],
                "action": (m.get("action") or {}).get("type"),
                "refs": m.get("refs", []),
            }
            for m in events
        ]
        for sid, events in threads.items()
    }


def public_timeline(snapshot, cached):
    """Recover visible chronological moves, excluding server reasoning/private state."""
    timeline = []
    positions = defaultdict(int)
    for item in (snapshot.get("result") or {}).get("trace", []):
        decoded = trace_threads({"result": {"trace": [item]}}, cached)
        for supplier, messages in decoded.items():
            for message in messages:
                action = message.get("action") or {}
                if action.get("type") == "timeout":
                    message["from"] = "arena"  # no buyer-authored decision existed
                if action.get("type") == "accept":
                    action["term_sheet_id"] = action.get("term_sheet_id") or item["move"].get("offer_id")
                events = cached.get(supplier, [])
                for i in range(positions[supplier], len(events)):
                    event = events[i]
                    same_role = (event.get("from") == "buyer") == (message.get("from") == "buyer")
                    same_action = (event.get("action") or {}).get("type") == action.get("type")
                    if (
                        same_role
                        and same_action
                        and event.get("body", "") == message.get("body", "")
                        and _sheets(event) == _sheets(message)
                    ):
                        message = public_message(event)
                        positions[supplier] = i + 1
                        break
                timeline.append({"supplier_id": supplier, "message": message})
    return timeline


def normalize(copies, public_cases):
    def quality(pair):
        value = pair[0]
        authoritative = (
            value.get("provenance") == "owned Arena session, GET only" and value.get("remote_status") == "ended"
        )
        return (
            authoritative,
            sum(len(e) for e in value.get("threads", {}).values() if isinstance(e, list)),
            bool(value.get("id")),
        )

    best = max(copies, key=quality)[0]
    case = best.get("case") or public_cases.get(best["case_id"])
    if not case:
        raise ValueError("Missing public case")
    result = {
        k: copy.deepcopy(best[k])
        for k in (
            "id",
            "mode",
            "session_id",
            "case_set",
            "status",
            "agent",
            "model",
            "supplier_kind",
            "opponent",
            "prompt",
        )
        if k in best
    }
    result.update(mode=best.get("mode", "hosted"), case=copy.deepcopy(case), case_id=case["case_id"])
    # Server exports restore missing turns but do not carry local model/prompt metadata.
    for field in ("id", "agent", "model", "supplier_kind", "opponent", "prompt"):
        if not result.get(field):
            result[field] = next((copy.deepcopy(v[field]) for v, _ in copies if v.get(field)), None)
    result["case_set"] = result.get("case_set") or best.get("set") or case.get("set")
    result["source_type"] = (
        "hosted_" + str(result["case_set"])
        if result["mode"] == "hosted"
        else "local_language"
        if result.get("supplier_kind") == "language" or str(result.get("opponent", "")).startswith("language:")
        else "local_simulation"
    )
    dates = [stamp(v.get("created_at")) for v, _ in copies]
    result["created_at"] = min((d for d in dates if d), default=None)
    threads = {}
    authoritative = quality((best, {}))[0]
    conflicts = []
    for value, _ in sorted(copies, key=quality, reverse=True):
        decoded = {
            s: [public_message(m) for m in events]
            for s, events in value.get("threads", {}).items()
            if isinstance(events, list)
        }
        if not decoded and (value.get("result") or {}).get("trace"):
            decoded = trace_threads(value, threads)
        for supplier, events in decoded.items():
            old = threads.get(supplier, [])
            a = canonical_threads({supplier: old})[supplier]
            b = canonical_threads({supplier: events})[supplier]
            if a[: min(len(a), len(b))] != b[: min(len(a), len(b))]:
                conflicts.append(supplier)
            # Local logs can contain a request written before a failed send. The
            # completed server transcript establishes which messages actually existed.
            if supplier not in threads or (not authoritative and len(events) > len(old)):
                threads[supplier] = events
    # Some ended sessions expose only the opening through /threads, while their
    # visible result trace retains all subsequent moves. Preserve those too.
    timeline = public_timeline(best, threads)
    if timeline:
        result["timeline"] = timeline
    if len(timeline) >= sum(map(len, threads.values())) and timeline:
        threads = defaultdict(list)
        for event in timeline:
            threads[event["supplier_id"]].append(event["message"])
    result["threads"] = dict(threads)
    result["corpus_sources"] = [source for _, source in copies]
    result["version_conflicts"] = sorted(set(conflicts))
    result["trajectory_sha256"] = digest({"case_id": case["case_id"], "threads": canonical_threads(threads)})
    # Post-run labels are kept separately, not in the transcript/feature archive.
    outcomes = []
    for value, source in copies:
        for label, outcome in (
            ("result", value.get("result")),
            ("cleanup", value.get("remote_cleanup", {}).get("result")),
        ):
            if outcome:
                outcomes.append(
                    {
                        "source": source,
                        "kind": label,
                        "value": {
                            k: outcome[k]
                            for k in ("score", "deal", "must_haves_ok", "terms", "supplier_id", "end_reason", "ended")
                            if k in outcome
                        },
                    }
                )
    return result, outcomes


def decision_examples(run, key, split):
    contexts = {}
    if run.get("timeline"):
        visible = defaultdict(list)
        for event in run["timeline"]:
            supplier, message = event["supplier_id"], event["message"]
            if message.get("from") == "buyer":
                position = len(visible[supplier])
                target = run["threads"].get(supplier, [])
                prefixes_match = all(
                    canonical_threads({s: events}) == canonical_threads({s: run["threads"].get(s, [])[: len(events)]})
                    for s, events in visible.items()
                )
                action_matches = position < len(target) and (
                    canonical_threads({supplier: [message]}) == canonical_threads({supplier: [target[position]]})
                )
                if prefixes_match and action_matches:
                    contexts[(supplier, position)] = copy.deepcopy(dict(visible))
            visible[supplier].append(message)
    for supplier, events in run["threads"].items():
        for i, message in enumerate(events):
            if message.get("from") != "buyer":
                continue
            context = contexts.get((supplier, i))
            yield {
                "id": f"{key}:{supplier}:{i}",
                "session_id": key,
                "case_id": run["case_id"],
                "source_kind": run["mode"],
                "case_set": run.get("case_set"),
                "split": split,
                "input": {
                    "case": run["case"],
                    "supplier_id": supplier,
                    **({"threads": context} if context is not None else {"thread_prefix": events[:i]}),
                },
                "recorded_action": message,
                "context_scope": "chronological conversation prefixes across all supplier threads"
                if context is not None
                else "single thread only; global ordering unavailable; incomplete for portfolio imitation",
                "use": "behavior analysis; quality is not assumed; not automatically approved imitation data",
            }


def write_jsonl(path, rows):
    with Path(path).open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")


def build_corpus(roots, output, public_cases=None, exclude_evaluations=(), fixture_run_ids=()):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    public_cases = public_cases if public_cases is not None else package().cases
    copies, excluded, seen = defaultdict(list), Counter(), set()
    reserved = set()
    for root in roots:
        for path in sorted(Path(root).rglob("*.json")):
            resolved = path.resolve()
            if resolved in seen or resolved.is_relative_to(output):
                continue
            seen.add(resolved)
            # Prior training exports are derived copies; source negotiations remain available.
            if any(p.startswith(("custom-p10-p18-", "training-corpus-")) for p in path.parts):
                excluded["derived_training_file"] += 1
                continue
            try:
                raw = path.read_bytes()
                value = json.loads(raw)
            except (OSError, ValueError):
                excluded["unreadable_json"] += 1
                continue
            for pointer, run in records(value):
                if not run.get("session_id"):
                    excluded["no_session_id"] += 1
                    continue
                source = {"path": str(resolved), "pointer": pointer, "sha256": hashlib.sha256(raw).hexdigest()}
                if run.get("evaluation_id") in exclude_evaluations:
                    reserved.add(identity(run))
                copies[identity(run)].append((run, source))
    runs, labels, response_rows, decisions = {}, [], [], []
    source_counts, split_counts = Counter(), Counter()
    for key, versions in sorted(copies.items()):
        if key in reserved:
            excluded["reserved_prospective_session"] += 1
            continue
        try:
            run, outcomes = normalize(versions, public_cases)
        except (ValueError, KeyError):
            excluded["missing_public_case"] += 1
            continue
        fixture = run.get("id") in fixture_run_ids
        if fixture:
            run["source_type"] = "verification_fixture"
        # Group identical trajectories together even if they have different session IDs.
        bucket = int(run["trajectory_sha256"][:8], 16) % 10
        split = "train" if bucket < 8 else "validation" if bucket == 8 else "test"
        run["split"] = split
        run["original_session_id"] = run["session_id"]
        if run["mode"] == "local":
            run["session_id"] = key
        run["corpus_id"] = key
        runs[key] = run
        source_counts[run["source_type"]] += 1
        split_counts[split] += 1
        destination = output / f"{run['mode']}-runs" / (hashlib.sha256(key.encode()).hexdigest() + ".json")
        dump(destination, run)
        labels.append({"session_id": key, "source_kind": run["mode"], "observed_outcomes": outcomes})
        if fixture:
            excluded["verification_fixture_session"] += 1
            continue
        decisions.extend(decision_examples(run, key, split))
        rows, skipped = extract(run)
        excluded.update(skipped)
        for row in rows:
            row.update(corpus_id=key, source_kind=run["mode"], split=split, trajectory_sha256=run["trajectory_sha256"])
        response_rows.extend(rows)
    write_jsonl(output / "conversations.jsonl", runs.values())
    write_jsonl(output / "decisions.jsonl", decisions)
    write_jsonl(output / "outcomes.jsonl", labels)
    write_jsonl(output / "responses.jsonl", response_rows)
    for split in ("train", "validation", "test"):
        write_jsonl(output / f"decisions-{split}.jsonl", (d for d in decisions if d["split"] == split))
    manifest = {
        "schema": "aeiou-all-negotiations-v2",
        "created_at": datetime.now(UTC).isoformat(),
        "roots": [str(Path(r).resolve()) for r in roots],
        "files_scanned": len(seen),
        "sessions": len(runs),
        "by_source": dict(source_counts),
        "split_sessions": dict(split_counts),
        "messages": sum(len(t) for r in runs.values() for t in r["threads"].values()),
        "decision_examples": len(decisions),
        "decision_context_scopes": dict(Counter(d["context_scope"] for d in decisions)),
        "response_examples": len(response_rows),
        "response_examples_by_source": dict(Counter(r["source_kind"] for r in response_rows)),
        "duplicate_copies": sum(len(v) - 1 for v in copies.values()),
        "excluded": dict(excluded),
        "conflicting_sessions": [k for k, r in runs.items() if r["version_conflicts"]],
        "reserved_prospective_sessions": sorted(reserved),
        "split_policy": "80/10/10 whole-trajectory hash; same-supplier development, not unseen-case proof",
        "training_policy": "All observed actions retained, including failures. Outcomes separate. Simulations labeled. "
        "No preference pairs fabricated. No provider fine-tuning upload. No private reasoning included.",
    }
    dump(output / "manifest.json", manifest)
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--sources", nargs="+", default=["data/local", "reports", "tmp"])
    parser.add_argument("--collect-remote", action="store_true")
    parser.add_argument("--exclude-evaluations", nargs="*", default=[])
    parser.add_argument(
        "--fixture-manifest", help="JSON with run_ids generated by verification, archived but not trained"
    )
    args = parser.parse_args()
    if args.collect_remote:
        from .eval_suite import sandbox_client

        client = sandbox_client()
        try:
            print(json.dumps(collect_remote(args.output, client), indent=2))
        finally:
            client.http.close()
    else:
        fixtures = json.loads(Path(args.fixture_manifest).read_text())["run_ids"] if args.fixture_manifest else []
        print(
            json.dumps(
                build_corpus(
                    args.sources, args.output, exclude_evaluations=args.exclude_evaluations, fixture_run_ids=fixtures
                ),
                indent=2,
            )
        )
