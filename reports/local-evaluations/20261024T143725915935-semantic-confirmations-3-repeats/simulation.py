"""Local Claude suppliers using public arena contracts and isolated synthetic economics."""

import copy
import itertools
import logging
import random
import time

from arena_kit.cost import must_haves_ok, score, total_cost
from arena_kit.play import play
from arena_kit.sim import Sim, load_cases

from .action_policy import NegotiationBlocked, validate_buyer_message
from .runner import finish_run, make_agent
from .store import new_run, save_run
from .supplier_profiles import PROFILES

OPPONENTS = {
    **PROFILES,
    "hardliner": {
        "name": "The hardliner",
        "description": "Protects margin and decides when further discussion is useful.",
    },
    "cashflow": {"name": "Cash constrained", "description": "Values early payment more than the buyer does."},
    "relationship": {"name": "Family business", "description": "Trades continuity and a respectful tone for price."},
    "index_skeptic": {"name": "Evidence skeptic", "description": "Opens flexibility only for verifiable evidence."},
    "adversarial": {
        "name": "Adversarial inbox",
        "description": "Tough commercial seller that protects confidential economics.",
    },
}


def local_cases():
    cases = load_cases()
    quality = copy.deepcopy(cases["case:S01"])
    quality.update(
        case_id="case:T01",
        title="Quality remediation: bearing bracket",
        type="quality",
        negotiable=["unit_price", "ppm_commitment", "chargeback_eur_per_complaint"],
        term_options={"ppm_commitment": [500, 1000, 1500, 3000], "chargeback_eur_per_complaint": [0, 50, 100]},
    )
    quality["fixed_terms"]["payment_days"] = 30
    quality["buyer"].update(
        category_goal="quality_first",
        cost_per_defect=100,
        must_haves=[{"field": "ppm_commitment", "op": "<=", "value": 1500}],
    )
    cases[quality["case_id"]] = quality
    coverage = copy.deepcopy(cases["case:S03"])
    coverage.update(
        case_id="case:T02",
        title="Secure full supply across the horizon",
        type="capacity",
        negotiable=["unit_price", "payment_days", "volume_per_year", "contract_months"],
        term_options={
            "payment_days": [14, 30, 60, 90],
            "volume_per_year": [6000, 8490, 10000],
            "contract_months": [12, 24],
        },
    )
    coverage["fixed_terms"].pop("volume_per_year")
    coverage["fixed_terms"]["incoterm"] = "DAP"
    coverage["buyer"]["must_haves"] = [{"field": "volume_per_year", "op": ">=", "value": 8490}]
    cases[coverage["case_id"]] = coverage
    from .public_setups import public_setups

    cases.update({cid: setup["case"] for cid, setup in public_setups().items()})
    return cases


class SupplierEconomics(Sim):
    def __init__(self, cases, opponent, seed, facts):
        super().__init__(cases, facts, no_clock=True)
        self.opponent = opponent
        self.rng = random.Random(seed)
        # These private values stay in the simulator. The agent sees only case files and email.
        self.floor_ratio = {
            "hardliner": 0.96,
            "cashflow": 0.90,
            "relationship": 0.90,
            "index_skeptic": 0.94,
            "adversarial": 0.91,
            **dict.fromkeys(PROFILES, 0.93),
        }[opponent] + self.rng.uniform(-0.015, 0.015)

    def cash_factor(self, terms):
        if self.opponent == "cashflow":
            return 1 - 0.40 * (terms.get("payment_days", 30) - 14) / 360
        return 1

    def required_price(self, opening, terms):
        ratio = self.floor_ratio
        if self.opponent == "relationship" and terms.get("contract_months", 12) >= 24:
            ratio -= 0.015
        return round(opening * ratio / self.cash_factor(terms), 2)

    def _answer(self, s, th, msg, sheets):
        raise NotImplementedError("A model-driven supplier must decide every reply")

    def _end(self, s, reason, sup):
        super()._end(s, reason, sup)
        case, best = s["case"], float("inf")
        fields = [f for f in case["negotiable"] if f != "unit_price"]
        for th in s["threads"].values():
            choices = [case["term_options"].get(f) or [th["standard"].get(f)] for f in fields]
            for combo in itertools.product(*choices):
                t = dict(zip(fields, combo, strict=True))
                t["unit_price"] = self.required_price(th["opening"], t)
                if must_haves_ok(case, t, th["standard"]):
                    best = min(best, total_cost(case, t, th["standard"]))
        alt = s["result"].get("c_alt")
        if alt is not None:
            best = min(best, alt)
        s["result"]["c_best"] = best
        cost = s["result"].get("c_deal") or alt
        s["result"]["score"] = score(s["result"]["c_open"], cost, best)
        if s["result"]["deal"] and not s["result"]["must_haves_ok"]:
            s["result"]["score"] = 0


class LocalTable:
    def __init__(self, sim, run, pace=0.0):
        self.sim, self.run, self.team, self.session_id, self.pace = sim, run, "local-team", None, pace

    def open(self, case_id):
        r = self.sim.open(self.team, case_id)
        self.session_id = r["session_id"]
        self.run.update(case=r["case"], session_id=self.session_id)
        return r["case"], copy.deepcopy(r["threads"])

    def observe_decision(self, agent):
        self.run["decisions"] = getattr(agent, "decisions", [])
        if hasattr(agent, "audit"):
            self.run["audit"] = agent.audit
        save_run(self.run)

    def send(self, supplier_id, message):
        validate_buyer_message(message)
        reply = copy.deepcopy(self.sim.message(self.team, self.session_id, supplier_id, message))
        # Persist each exchange so the dashboard pipeline can follow the negotiation live.
        threads = self.sim.sessions[self.session_id]["threads"]
        self.run["threads"] = {s: copy.deepcopy(t["messages"]) for s, t in threads.items()}
        save_run(self.run)
        if self.pace:
            time.sleep(self.pace)
        return reply

    def close(self, supplier_id, message):
        return self.send(supplier_id, message)["result"]

    def attachment(self, attachment_id):
        return self.sim.attachment(self.team, self.session_id, attachment_id)[0]

    def result(self):
        return self.sim.sessions[self.session_id]["result"]


def local_play(
    case_id="case:S01",
    name="original",
    opponent="relationship",
    seed=0,
    run=None,
    pkg=None,
    pace=0.0,
    backend_factory=None,
):
    from .research import package

    pkg = pkg or package()
    run = run or new_run(case_id, "local", name)
    run.update(opponent=opponent, seed=seed)
    save_run(run)
    started = time.monotonic()
    cases = local_cases()
    if opponent not in OPPONENTS or case_id not in cases:
        raise ValueError("unknown local opponent or case")
    from .agent import ClaudeBackend
    from .config import model_name
    from .language_sim import SUPPLIER_VERSION, LanguageSim, SupplierSimulationError
    from .public_setups import public_setups

    backend = (backend_factory or ClaudeBackend)(model=model_name(), max_tokens=2200, timeout=90)
    sim = LanguageSim(cases, opponent, seed, pkg.facts, backend)
    run.update(
        opponent=f"language:{opponent}",
        supplier_kind="language",
        supplier_version=SUPPLIER_VERSION,
        supplier_model=getattr(backend, "model", model_name()),
    )
    setup = public_setups().get(case_id)
    if setup:
        run["public_setup_sha256"] = setup["sha256"]
    table, agent = LocalTable(sim, run, pace), make_agent(name, pkg)
    try:
        result = play(table, case_id, agent)
        state = sim.sessions[table.session_id]
        run["threads"] = {s: t["messages"] for s, t in state["threads"].items()}
        finish_run(run, agent, result, started)
    except NegotiationBlocked as exc:
        if table.session_id:
            state = sim.sessions[table.session_id]
            sim._end(state, "protocol_limit", None)
            run["threads"] = {sid: t["messages"] for sid, t in state["threads"].items()}
            run["termination"] = {"origin": "protocol", "reason": exc.reason, "buyer_action_sent": False}
            finish_run(run, agent, state["result"], started)
    except Exception as e:
        logging.getLogger("aeiou").exception("Local negotiation failed for %s / %s", case_id, opponent)
        if table.session_id:
            run["threads"] = {s: t["messages"] for s, t in sim.sessions[table.session_id]["threads"].items()}
        finish_run(run, agent, {}, started)
        run.update(
            status="failed",
            error=f"{type(e).__name__}: {e}",
            failure_origin="supplier" if isinstance(e, SupplierSimulationError) else "buyer_or_transport",
        )
        save_run(run)
    run["supplier_metrics"] = {k: getattr(backend, k, 0) for k in ("calls", "errors", "input_tokens", "output_tokens")}
    run["supplier_metrics"].update(version=SUPPLIER_VERSION, format_repairs=sim.repairs)
    save_run(run)
    return run
