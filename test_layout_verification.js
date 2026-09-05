const fs = require('fs');
const html = fs.readFileSync('static/index.html', 'utf8');

console.log('=== 1. Checking Default View Structure ===');
const checks = [
  { name: 'Company Switcher selector exists', ok: html.includes('id="companySelector"') },
  { name: 'Architecture & Overview collapsed by default', ok: html.includes('id="systemOverviewContent"') && html.includes('display: none;') },
  { name: 'Main Navigation Bar exists', ok: html.includes('class="main-nav-bar"') },
  { name: 'Live Pipeline tab button exists', ok: html.includes('id="tabBtn-pipeline"') },
  { name: 'Live Pipeline tab content active by default', ok: /id="pipelineTab"[^>]*class="[^"]*active/.test(html) },
  { name: 'Compact Control Strip exists', ok: html.includes('class="pipeline-control-strip"') },
  { name: 'Simulation Status Dot exists', ok: html.includes('id="simStatusDot"') },
  { name: 'Start Simulation button exists', ok: html.includes('id="simToggleBtn"') },
  { name: 'AI Customer Order button exists', ok: html.includes('id="simSingleBtn"') },
  { name: 'Pipeline Counter badge exists', ok: html.includes('id="pipelineCounter"') },
  { name: 'Pinned Auditor Card exists', ok: html.includes('id="pinnedAuditorVerdict"') },
  { name: 'Pinned Auditor Badge exists', ok: html.includes('id="pinnedAuditorBadge"') },
  { name: 'Pinned Auditor Reason exists', ok: html.includes('id="pinnedAuditorReason"') },
  { name: 'Pipeline Timeline exists', ok: html.includes('id="pipelineTimeline"') },
  { name: 'Inventory tab hidden by default', ok: html.includes('id="inventoryTab"') && !/id="inventoryTab"[^>]*class="[^"]*active/.test(html) },
  { name: 'Command tab hidden by default', ok: html.includes('id="commandTab"') && !/id="commandTab"[^>]*class="[^"]*active/.test(html) },
  { name: 'Buyer tab hidden by default', ok: html.includes('id="buyerTab"') && !/id="buyerTab"[^>]*class="[^"]*active/.test(html) },
  { name: 'Customer tab hidden by default', ok: html.includes('id="customerTab"') && !/id="customerTab"[^>]*class="[^"]*active/.test(html) }
];

let allPassed = true;
checks.forEach(c => {
  console.log((c.ok ? '✓ ' : '✗ ') + c.name);
  if (!c.ok) allPassed = false;
});

console.log('\n=== 2. Checking Tab Switcher Coverage ===');
['pipeline', 'inventory', 'command', 'buyer', 'customer', 'restock'].forEach(t => {
  const hasTab = html.includes(`'${t}'`);
  console.log((hasTab ? '✓ ' : '✗ ') + 'Tab handled: ' + t);
  if (!hasTab) allPassed = false;
});

console.log('\n=== 3. Checking Pinned Auditor Updates ===');
const hasDef = html.includes('function updatePinnedAuditorVerdict');
const hasPipelineCall = html.includes('updatePinnedAuditorVerdict(auditRes, decision)');
const hasRenderCall = html.includes('updatePinnedAuditorVerdict(data.audit_result, data)');
const hasAuditCall = html.includes('updatePinnedAuditorVerdict({');

console.log((hasDef ? '✓ ' : '✗ ') + 'updatePinnedAuditorVerdict defined');
console.log((hasPipelineCall ? '✓ ' : '✗ ') + 'updatePinnedAuditorVerdict called in appendPipelineSteps');
console.log((hasRenderCall ? '✓ ' : '✗ ') + 'updatePinnedAuditorVerdict called in renderResult');
console.log((hasAuditCall ? '✓ ' : '✗ ') + 'updatePinnedAuditorVerdict called in loadAudit');

if (!hasDef || !hasPipelineCall || !hasRenderCall || !hasAuditCall) allPassed = false;

console.log('\n=== 4. Checking DOMContentLoaded Tab Default ===');
const domDefaultOk = html.includes("switchTab('pipeline')");
console.log((domDefaultOk ? '✓ ' : '✗ ') + "DOMContentLoaded calls switchTab('pipeline')");
if (!domDefaultOk) allPassed = false;

console.log('\nFinal Status: ' + (allPassed ? 'ALL VERIFICATIONS PASSED [OK]' : 'FAILURES DETECTED'));
process.exit(allPassed ? 0 : 1);
