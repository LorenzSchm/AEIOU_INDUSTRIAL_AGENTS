# Negotiation assumptions and evidence

Primary reference: Vaccaro, Caosun, Ju, Aral & Curhan, **Advancing AI Negotiations: A Large-Scale
Autonomous Negotiation Competition**, arXiv:2503.06416v3 (13 January 2026).
[Full paper](https://arxiv.org/html/2503.06416v3) ·
[Requested page 87](https://arxiv.org/pdf/2503.06416v3#page=87).

PDF page 87 is supplementary page S57, Figure S38, NegoMate part 1. The local reference prompt
combines this preparation scaffold with the later Inject+Voss instructions (Figure S41).

| Finding or tactic | Implementation hypothesis | Validation |
| --- | --- | --- |
| Warmth was associated with better outcomes across metrics | Gratitude, specific questions, legitimate constraints | Hosted transcripts and case scores |
| Dominance helped claim value conditional on a deal | Firm anchors and conditional concessions | Guarded versus adaptive iterations |
| Longer conversations were associated with impasse | Bounded rounds and deadline closing | Cap and final-wave tests |
| Preparation and mutual-gain framing appear in NegoMate | Research context and price complete packages | Retrieval, index arithmetic and cost tests |
| AI-specific components had incremental effects in targeted ablations | Keep a tactical-question variant available | `--tactic voss` as an experiment |
| Results came from a specific model and setup | Test Claude directly rather than assume transfer | Claude reports and hosted comparison |

These are testable design choices, not causal guarantees for industrial procurement. Ablations
do not prove every component of the combined prompt optimal. The arena's structured term sheets,
finite rounds and total-cost objective differ from the paper's tasks. The original prompt stays
intact while production instructions adapt it to those constraints. The agent does not send
private preparation or purported hidden messages to suppliers.

The official arena brief and C function govern scoring. Market changes and package values come
from code. Cash pressure, approval ladders, capacity and quality notes guide questions and trades;
should-cost remains an uncertain estimate rather than a disclosed supplier floor.

## Lessons from actual tests

- A PDF's standard-terms section can omit terms already stated in its offered section. Supplier
  financing references combine master data, PDF defaults and fixed terms.
- A cheaper unit price can lose overall through overbuying or insufficient coverage. Validate
  proposed packages as well as accepts.
- Partial final offers can revert non-price fields to costly defaults. Use observed concessions
  and resolve the complete package before the last round where possible.
- Shared read-only SQLite access still needs serialization in this runtime. The research package
  locks queries and lazy ID initialization; the parallel two-wave rehearsal covers this.
- Keep hosted practice separate from synthetic tests, retaining failures, attempt counts, usage
  and fallback counts instead of presenting only favorable runs.
