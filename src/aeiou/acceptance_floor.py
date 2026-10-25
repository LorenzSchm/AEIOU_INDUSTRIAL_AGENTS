"""Explicit, case-scoped acceptance limits supplied with an evaluation strategy."""

import json
import re
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from arena_kit.cost import total_cost
from arena_kit.docs import standard_terms


@dataclass(frozen=True)
class AcceptanceFloor:
    case_id: str
    c_open: Decimal | None
    c_best: Decimal | None
    minimum_score: Decimal | None
    maximum_unit_price: Decimal | None = None
    maximum_total_cost: Decimal | None = None

    @classmethod
    def from_dict(cls, value):
        if isinstance(value, dict) and "maximum_total_cost" in value:
            return cls.from_limits(value)
        required = {"case_id", "c_open", "c_best", "minimum_score"}
        if not isinstance(value, dict) or not required <= set(value) or set(value) - required - {"maximum_unit_price"}:
            raise ValueError("Acceptance floor needs case_id, c_open, c_best and minimum_score")
        if not isinstance(value["case_id"], str) or not value["case_id"].startswith("case:"):
            raise ValueError("Acceptance floor needs an exact case ID")
        numbers = []
        for key in ("c_open", "c_best", "minimum_score"):
            if isinstance(value[key], bool):
                raise ValueError("Acceptance floor values must be finite numbers")
            try:
                number = Decimal(str(value[key]))
            except InvalidOperation as exc:
                raise ValueError("Acceptance floor values must be finite numbers") from exc
            if not number.is_finite():
                raise ValueError("Acceptance floor values must be finite numbers")
            numbers.append(number)
        opening, best, minimum = numbers
        if not opening > best >= 0 or not 0 <= minimum <= 100:
            raise ValueError("Acceptance floor requires c_open > c_best >= 0 and a score in [0, 100]")
        price_cap = None
        if "maximum_unit_price" in value:
            try:
                price_cap = Decimal(str(value["maximum_unit_price"]))
            except InvalidOperation as exc:
                raise ValueError("maximum_unit_price must be a finite positive number") from exc
            if not price_cap.is_finite() or price_cap <= 0:
                raise ValueError("maximum_unit_price must be a finite positive number")
        if minimum == 0 and price_cap is None:
            raise ValueError("A zero score minimum requires an explicit maximum_unit_price")
        return cls(value["case_id"], opening, best, minimum, price_cap)

    @classmethod
    def from_limits(cls, value):
        """Use explicit saved caps without inventing benchmark costs or a score."""
        if not isinstance(value, dict) or set(value) != {"case_id", "maximum_total_cost", "maximum_unit_price"}:
            raise ValueError("Acceptance limits need case_id, maximum_total_cost and maximum_unit_price")
        if not isinstance(value["case_id"], str) or not value["case_id"].startswith("case:"):
            raise ValueError("Acceptance limits need an exact case ID")
        limits = []
        for key in ("maximum_total_cost", "maximum_unit_price"):
            if isinstance(value[key], bool):
                raise ValueError("Acceptance limits must be finite positive numbers")
            try:
                number = Decimal(str(value[key]))
            except InvalidOperation as exc:
                raise ValueError("Acceptance limits must be finite positive numbers") from exc
            if not number.is_finite() or number <= 0:
                raise ValueError("Acceptance limits must be finite positive numbers")
            limits.append(number)
        return cls(value["case_id"], None, None, None, limits[1], limits[0])

    @property
    def has_benchmark(self):
        return self.c_open is not None and self.c_best is not None and self.minimum_score is not None

    @classmethod
    def from_prompt(cls, prompt):
        if "<acceptance_floor>" not in prompt and "</acceptance_floor>" not in prompt:
            return None
        blocks = re.findall(r"<acceptance_floor>\s*(.*?)\s*</acceptance_floor>", prompt, re.S)
        if len(blocks) != 1 or prompt.count("<acceptance_floor>") != 1 or prompt.count("</acceptance_floor>") != 1:
            raise ValueError("Use exactly one complete acceptance_floor block")
        return cls.from_dict(json.loads(blocks[0]))

    @property
    def max_cost(self):
        if self.maximum_total_cost is not None:
            return self.maximum_total_cost
        limit = self.c_open - self.minimum_score / 100 * (self.c_open - self.c_best)
        return limit.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)

    def allows_cost(self, cost):
        try:
            value = Decimal(str(cost))
            return value.is_finite() and value >= 0 and value <= self.max_cost
        except InvalidOperation:
            return False

    def allows_unit_price(self, terms):
        if self.maximum_unit_price is None:
            return True
        if not isinstance(terms, dict):
            return False
        try:
            value = Decimal(str(terms.get("unit_price")))
            return value.is_finite() and 0 < value <= self.maximum_unit_price
        except InvalidOperation:
            return False

    def allows(self, cost, terms=None):
        return self.allows_cost(cost) and self.allows_unit_price(terms)

    def metadata(self):
        if not self.has_benchmark:
            return {
                "case_id": self.case_id,
                "maximum_total_cost": float(self.max_cost),
                "maximum_unit_price": float(self.maximum_unit_price),
            }
        return {
            "case_id": self.case_id,
            "c_open": float(self.c_open),
            "c_best": float(self.c_best),
            "minimum_score": float(self.minimum_score),
            **({"maximum_unit_price": float(self.maximum_unit_price)} if self.maximum_unit_price is not None else {}),
        }

    def check_case(self, case, threads=None):
        if case["case_id"] != self.case_id:
            raise ValueError("ACCEPTANCE_BENCHMARK_CASE_MISMATCH")
        if threads is None or not self.has_benchmark:
            return
        opening_costs = []
        for events in threads.values():
            for i, message in enumerate(events):
                if message.get("from") != "buyer" and message.get("term_sheets"):
                    std = standard_terms(events[: i + 1])
                    opening_costs.extend(total_cost(case, s["terms"], std) for s in message["term_sheets"])
                    break
        if not opening_costs or Decimal(str(min(opening_costs))) != self.c_open:
            raise ValueError("ACCEPTANCE_BENCHMARK_OPENING_MISMATCH")


FLOOR_TRANSPORT = """

<acceptance_floor_contract>
Acceptance limits come from an explicit acceptance_floor block or the saved execution controls
for this case. They are enforced by code. acceptance_policy in the input contains the exact
maximum total cost. Offers failing it cannot be accepted, even on
Final, agreement, timeout or exhausted rounds. Model-estimated scores do not override the check.
If maximum_unit_price is configured, the selected offer must also meet that literal unit-price
cap. Better payment or index terms cannot authorize a higher unit price. Both limits are checked.
A minimum_score of 0, together with an explicit maximum_unit_price, disables a positive
score target; the opening-cost ceiling and the literal unit-price cap still apply.
Saved absolute cost and price limits without benchmark inputs do not imply a known score.
This policy also permits {"supplier_id":"sup:...", "action":"stop"} with the usual assessment
and numeric_claims. It stops locally without accepting or sending a buyer email. Use it when
no eligible offer satisfies the floor and no useful legal negotiation move remains. This is
not a successful deal and does not earn the minimum score. Do not output walk_away.
The floor takes priority over any strategy sentence requiring unconditional acceptance.
</acceptance_floor_contract>
"""
