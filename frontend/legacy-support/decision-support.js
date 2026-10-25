/* Recorded observations and explicit cost scenarios. This view never starts a run. */
function decisionSupport(run, supplier) {
  const history = (run.decisions ?? []).filter(d => d.decision_support?.[supplier]);
  if (!history.length) return '';
  const observation = history.at(-1).decision_support[supplier];
  // A terminal close has fresh observations but no new offer plan. Keep the most recent plan.
  const planned = [...history].reverse().find(d => d.decision_support[supplier].plan);
  const plan = planned?.decision_support[supplier].plan;
  const learned = observation.state;
  const rejections = history.flatMap(d => d.guard_rejections ?? []);
  const belief = observation.belief;
  const controller = history.at(-1).controller;
  const strategy = observation.strategy;
  const policy = belief ? `<details class="scenario-assumptions" open><summary>Controller and preference hypotheses</summary>
    <p>${esc(strategy?.name ?? 'balanced')} · ${esc(strategy?.reason ?? '')}</p>
    <p>Action: ${esc(controller?.action ?? 'recorded')} · ${esc(controller?.reason ?? '')}</p>
    <p>${number(belief.observations)} distinct observed package choices. Assumption weights: ${(belief.hypotheses ?? []).map(h => `${esc(h.name)} ${number(h.weight*100,1)}%`).join(' · ')}</p>
    <p>${esc(belief.limitation)}</p>
    ${(controller?.traces ?? []).map(t => `<p>${esc(t.candidate_id)}: modeled continuation ${euros(t.modeled_continuation_cost)} · assumed loss risk ${number(t.assumed_loss_risk*100,1)}%</p>`).join('')}
    <p class="section-note">Hypothetical continuation costs use uncalibrated supplier assumptions. Economic guards preserve better legal counters and terminal offers.</p></details>` : '';
  const preferences = (learned.preferences ?? []).map(p =>
    `<div class="audit-item"><strong>${esc(human(p.field))}: ${esc(typeof p.value === 'object' ? JSON.stringify(p.value) : p.value)}</strong>
      <br>${esc(p.confidence)} hypothesis from ${number(p.observations)} package responses
      <details><summary>Response evidence</summary>${p.evidence.map(e =>
        `<p>Message ${number(e.reply_message_index + 1)} · ${esc(e.offer_id ?? 'unnamed offer')} · ${esc(human(e.kind))}${e.confounded ? ' · several terms varied' : ''}</p>`
      ).join('')}</details></div>`).join('');
  const scenarios = plan?.packages?.length ? `<div class="table-scroll"><table>
    <thead><tr><th>PACKAGE</th><th>BASE COST</th><th>WORST SCENARIO</th><th>WORST REGRET</th></tr></thead>
    <tbody>${plan.packages.map(p => `<tr><td>${esc(p.id)} · ${esc(p.label)}${p.id === plan.recommendation ? ' · robust cost choice' : ''}</td>
      <td>${euros(p.cost)}</td><td>${euros(p.worst_cost)}</td><td>${euros(p.worst_regret)}</td></tr>`).join('')}</tbody></table></div>
    <details class="scenario-assumptions"><summary>Scenario assumptions and exact costs</summary>
      ${(plan.scenarios ?? []).map(s => `<p><strong>${esc(s.label)}</strong><br>${plan.packages.map(p => `${esc(p.id)}: ${euros(p.scenario_costs[s.id])}`).join(' · ')}</p>`).join('')}
      <p>Worst regret compares a package with the cheapest offered package in each scenario. It does not model whether the supplier will accept.</p>
    </details>` : '<p class="section-note">No executable package plan at this decision.</p>';
  return `<section class="card decision-support"><div class="card-head"><div><h3>Supplier learning and package planning</h3>
    <p class="muted">Observed replies, tentative preferences and explicit buyer-cost scenarios.</p></div></div>
    <div class="evidence-grid"><div><h4>What the supplier has shown</h4>${preferences || '<p class="section-note">No distinct package choice observed yet. Preferences remain unknown.</p>'}
      <p class="section-note">${number(learned.identical_price_replies)} identical price replies · ${number(learned.moves_left)} moves remaining at this decision</p>
      <p class="section-note">Package choices can reflect price or several terms together. These are not revealed supplier utilities.</p></div>
    <div><h4>Package exposure</h4><p class="section-note">Cost sensitivities, not predictions or acceptance probabilities. Last offer plan${planned ? ` from decision ${number((run.decisions ?? []).indexOf(planned) + 1)}` : ''}.</p>${scenarios}</div></div>
    ${policy}${rejections.length ? `<details class="scenario-assumptions"><summary>${number(rejections.length)} guarded model proposals</summary>${rejections.map(reason => `<p>${esc(reason)}</p>`).join('')}</details>` : ''}</section>`;
}

function handoffLink(run) {
  if (run.status !== 'completed') return '';
  return `<section class="card handoff-review"><h3>Procurement review</h3>
    <p class="section-note">${run.mode === 'local' ? 'Simulation output. ' : ''}Draft terms, cost comparison and source messages. Review and approval are required before any purchase order.</p>
    <a class="button" href="/api/runs/${encodeURIComponent(run.id)}/handoff" download="handoff-${esc(run.id)}.json">Download review draft</a></section>`;
}
