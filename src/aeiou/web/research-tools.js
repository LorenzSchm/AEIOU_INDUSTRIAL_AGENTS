/* Read-only views: searching and viewing reports never starts a model or Arena run. */
function researchSearchPanel(caseId) {
  return `<section class="card"><h3>Decision-focused evidence search</h3>
    <p class="section-note">Local BM25 and business-concept matching, scoped to this case and its data cutoff. Internal notes stay private.</p>
    <form id="research-search-form" data-case="${esc(caseId)}"><label>Business question
    <input name="query" required maxlength="500" placeholder="What supports a cash-for-price exchange?"></label>
    <button class="button" type="submit">Search evidence</button></form>
    <div id="research-search-results" aria-live="polite"></div></section>`;
}

function controlledReportCards(reports) {
  if (!reports.length) return '<p class="section-note">No controlled experiments saved yet. Run the local evaluate command to create a report.</p>';
  return reports.map(r => `<section class="card"><h3>${esc(r.created_at)}</h3>
    <p class="section-note">${number(r.conditions.planned_sessions)} planned sessions · ${esc(r.conditions.supplier_mode)} suppliers · ${number(r.excluded_pairs)} excluded pairs</p>
    <div class="table-scroll"><table><thead><tr><th>AGENT</th><th>MEAN SCORE</th><th>FAILURES</th><th>P95 DECISION</th></tr></thead><tbody>
    ${Object.entries(r.summary).map(([name,s]) => `<tr><td>${esc(name)}</td><td>${s.mean_score == null ? '—' : number(s.mean_score*100,2)}</td><td>${number(s.failures)}</td><td>${number(s.decision_p95_s,3)} s</td></tr>`).join('')}</tbody></table></div>
    ${Object.entries(r.paired_comparisons ?? {}).map(([name,c]) => c ? `<p>${esc(name)} vs reference: ${number(c.mean_case_delta*100,2)} points; 95% case-cluster interval ${number(c.low_95*100,2)} to ${number(c.high_95*100,2)}; ${number(c.case_clusters)} base cases.</p>` : '').join('')}
    <p class="section-note">${esc(r.limitation)} ${r.arena_sessions_used ? `${number(r.arena_sessions_used)} hosted ${esc(r.arena_scope ?? 'Arena')} sessions.` : 'No Arena sessions.'}${r.practice_sessions_used != null ? ` ${number(r.practice_sessions_used)} Practice sessions used.` : ''}</p></section>`).join('');
}

function controlledExperimentsPanel() {
  return '<section class="card"><h3>Controlled experiments and ablations</h3><p class="section-note">Paired conditions, all attempts, case-cluster intervals and explicit resource limits.</p><button class="button" id="load-controlled-reports">Load saved experiments</button><div id="controlled-reports" aria-live="polite"></div></section>';
}

function controlledEvaluations() {
  const newest = state.controlledReports?.[0];
  const names = Object.keys(newest?.summary ?? {});
  const reference = newest?.summary[names[0]], challenger = newest?.summary[names[1]];
  const headline = reference?.mean_score != null && challenger?.mean_score != null ? `<div class="metrics">
    ${metric('Reference score',number(reference.mean_score*100,2),esc(names[0]),'◉')}
    ${metric('Upgrade score',number(challenger.mean_score*100,2),esc(names[1]),'↗',true)}
    ${metric('Paired gain',number((challenger.mean_score-reference.mean_score)*100,2)+' pts',newest.arena_scope==='sandbox'?'Hosted sandbox; one run per version':'Local simulated conditions','⇄')}
    ${metric('Paired conditions',number(reference.sessions),`${number(challenger.failures)} failures · ${number(challenger.must_have_violations)} must-have violations`,'✓')}
    </div>` : '';
  return heading(newest?.arena_scope==='sandbox'?'SANDBOX BEFORE / AFTER':'PAIRED LOCAL EXPERIMENTS', 'Does the upgrade <em>perform better?</em>',
    newest?.arena_scope==='sandbox'?'One sandbox session before and one after the change. Supplier responses can vary.':'Both agents receive the same cases, seeds and supplier conditions. All outcomes count.') +
    evaluationTabs() + headline + controlledExperimentsPanel().replace(
      '<div id="controlled-reports" aria-live="polite"></div>',
      `<div id="controlled-reports" aria-live="polite">${controlledReportCards(state.controlledReports ?? [])}</div>`);
}

document.addEventListener('submit', async event => {
  if (event.target.id !== 'research-search-form') return;
  event.preventDefault();
  const form = event.target, result = document.getElementById('research-search-results');
  result.textContent = 'Searching local evidence…';
  try {
    const q = new FormData(form).get('query');
    const r = await api('/api/research/' + encodeURIComponent(form.dataset.case) + '/search?q=' + encodeURIComponent(q));
    result.innerHTML = r.evidence_cards.map(c => `<details class="document"><summary>${esc(c.source_id ?? c.id)} · ${c.shareable ? 'approved excerpt' : 'private planning only'}</summary><pre>${esc(c.text)}</pre></details>`).join('') || '<p>No matching evidence.</p>';
  } catch (error) { result.textContent = error.message; }
});

document.addEventListener('click', async event => {
  if (!event.target.closest('#load-controlled-reports')) return;
  const result = document.getElementById('controlled-reports');
  try { result.innerHTML = controlledReportCards(await api('/api/evaluations')); }
  catch (error) { result.textContent = error.message; }
});
