import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const context = vm.createContext({
  document: { addEventListener() {} },
  esc: value => String(value ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c])),
  euros: value => `EUR ${value}`,
  number: value => String(value),
  human: value => String(value).replaceAll('_', ' '),
});
vm.runInContext(fs.readFileSync(new URL('../frontend/legacy-support/decision-support.js', import.meta.url), 'utf8'), context);
vm.runInContext(fs.readFileSync(new URL('../frontend/legacy-support/research-tools.js', import.meta.url), 'utf8'), context);

test('sandbox reports show Arena use without claiming practice consumption', () => {
  const report = {created_at:'today', conditions:{planned_sessions:2,supplier_mode:'hosted sandbox'},
    excluded_pairs:0,summary:{},arena_sessions_used:2,arena_scope:'sandbox',practice_sessions_used:0,
    limitation:'One run per version.'};
  const html = context.controlledReportCards([report]);
  assert.match(html, /2 hosted sandbox sessions/);
  assert.match(html, /0 Practice sessions used/);
  assert.doesNotMatch(html, /No Arena sessions/);
});

test('repeat comparisons show individual scores, mean and spread', () => {
  const html = context.controlledReportCards([{title:'D01 repeats',conditions:{planned_sessions:4,supplier_mode:'sandbox'},
    summary:{current:{scores:[0.5,0.7],mean_score:0.6,spread:0.2,failures:0,decision_p95_s:1}}}]);
  assert.match(html, /D01 repeats/);
  assert.match(html, /50 \/ 70/);
  assert.match(html, /<td>60<\/td><td>20<\/td>/);
});

function fixture() {
  return {
    id: 'run123', status: 'completed', mode: 'local',
    decisions: [{ decision_support: { 'sup:1': {
      state: {
        identical_price_replies: 1, moves_left: 3,
        preferences: [{field: 'payment_days', value: 14, confidence: 'tentative', observations: 1,
          evidence: [{reply_message_index: 2, offer_id: 'offer:1', kind: 'counter_on_package', confounded: false}]}],
      },
      plan: {
        recommendation: 'A', scenarios: [{id: 'base', label: 'Published rates'}],
        packages: [{id: 'A', label: 'Buyer value', cost: 100, worst_cost: 110, worst_regret: 0, scenario_costs: {base: 100}}],
      },
    }}, guard_rejections: ['Invalid selection'] }],
  };
}

test('replay shows source message, scenarios and guarded actions', () => {
  const html = context.decisionSupport(fixture(), 'sup:1');
  assert.match(html, /Message 3/);
  assert.match(html, /offer:1/);
  assert.match(html, /EUR 110/);
  assert.match(html, /Published rates/);
  assert.match(html, /Invalid selection/);
  assert.match(html, /not predictions or acceptance probabilities/);
});

test('terminal observation retains the earlier scenario plan', () => {
  const run = fixture();
  run.decisions.push({decision_support: {'sup:1': {state: {preferences: [], moves_left: 0, identical_price_replies: 2}}}});
  const html = context.decisionSupport(run, 'sup:1');
  assert.match(html, /No distinct package choice/);
  assert.match(html, /from decision 1/);
  assert.match(html, /EUR 110/);
});

test('untrusted replay labels remain text', () => {
  const run = fixture();
  run.decisions[0].decision_support['sup:1'].plan.packages[0].label = '<img src=x onerror=alert(1)>';
  run.decisions[0].guard_rejections.push('<script>bad()</script>');
  const html = context.decisionSupport(run, 'sup:1');
  assert.doesNotMatch(html, /<img|<script/);
  assert.match(html, /&lt;img/);
});

test('legacy runs remain readable and only completed runs get a draft link', () => {
  assert.equal(context.decisionSupport({decisions: []}, 'sup:1'), '');
  assert.equal(context.handoffLink({status: 'running'}), '');
  assert.match(context.handoffLink(fixture()), /Simulation output/);
  assert.match(context.handoffLink(fixture()), /\/api\/runs\/run123\/handoff/);
});
