export const meta = {
  name: 'factory-sprint',
  description: 'Run one FactoryConnect sprint: parallel implementation by area, QA on the live stack, adversarial review, fix loop',
  whenToUse: 'Each roadmap sprint of the FactoryConnect project; pass {sprint, goal, tasks:[{role, area, prompt}], qaFocus}',
  phases: [
    { title: 'Implement', detail: 'one engineer per area, disjoint directories' },
    { title: 'QA', detail: 'all suites + e2e on the running stack' },
    { title: 'Review', detail: 'adversarial review of the working-tree diff' },
    { title: 'Fix', detail: 'fix failures and blocking findings, re-verify' },
  ],
}

const ENV = `
Shared environment (cloud container, repo at /home/user/factory-monitor, branch claude/fervent-bardeen-tigs3b):
- Docker daemon running; containers fm_postgres (5432, user fm_user / fm_dev_password, db factory_monitor), fm_influxdb (8086, token dev-token-change-me, org factory-monitor, bucket sensors), fm_mosquitto (1883) are UP. Do not stop them.
- Python venvs: .venv (server deps) and .venv-edge (edge deps). Node 22 + web/node_modules installed. pip and npm registries work. apt inside docker builds is BLOCKED by the network, so 'docker build' of server/web cannot be verified here — say so instead of pretending.
- A dev backend may be running on :8000 and vite on :5173. Other engineers work IN PARALLEL in this same checkout: stay inside your assigned area, use your own ports (pick from 8100-8199 / 5200-5299) and your own test database name (e.g. fm_test_<area>) for any servers/tests you start, and stop what you started.
- DO NOT run git commit, git stash, git checkout, git reset or anything that changes git state — the orchestrator commits. Never touch .env or secrets.
- Playwright: import from '/opt/node-tools/node_modules/playwright/index.mjs', chromium executablePath '/opt/pw-browsers/chromium'.
- The outside internet (factoryconnect.tech, VPS) is unreachable from here.
`

const IMPL_SCHEMA = {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    files_changed: { type: 'array', items: { type: 'string' } },
    tests_added: { type: 'array', items: { type: 'string' } },
    how_verified: { type: 'string', description: 'commands actually run and their results' },
    contract_changes: { type: 'string', description: 'API/payload/schema changes other areas must know; empty if none' },
    open_issues: { type: 'array', items: { type: 'string' } },
  },
  required: ['summary', 'files_changed', 'how_verified', 'open_issues'],
}
const QA_SCHEMA = {
  type: 'object',
  properties: {
    all_green: { type: 'boolean' },
    suites: { type: 'array', items: { type: 'object', properties: { name: { type: 'string' }, command: { type: 'string' }, passed: { type: 'boolean' }, detail: { type: 'string' } }, required: ['name', 'passed'] } },
    failures: { type: 'array', items: { type: 'object', properties: { what: { type: 'string' }, repro: { type: 'string' }, suspect: { type: 'string' } }, required: ['what'] } },
    acceptance: { type: 'array', items: { type: 'object', properties: { criterion: { type: 'string' }, met: { type: 'boolean' }, evidence: { type: 'string' } }, required: ['criterion', 'met'] } },
  },
  required: ['all_green', 'suites', 'failures'],
}
const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    findings: { type: 'array', items: { type: 'object', properties: {
      severity: { type: 'string', enum: ['blocking', 'important', 'suggestion'] },
      file: { type: 'string' }, line: { type: 'integer' }, problem: { type: 'string' }, scenario: { type: 'string' }, fix: { type: 'string' } },
      required: ['severity', 'file', 'problem', 'fix'] } },
  },
  required: ['findings'],
}

const role = r => `Your role: read .claude/agents/${r}.md and act as that agent (its principles are binding).`

phase('Implement')
log(`Sprint ${args.sprint}: ${args.goal}`)
const impl = await parallel(args.tasks.map(t => () =>
  agent(`${role(t.role)}\n${ENV}\nSprint ${args.sprint} goal: ${args.goal}\nYour area: ${t.area}\n\nYour task:\n${t.prompt}\n\nImplement fully, write tests, run them, and verify for real. Return the structured report.`,
    { label: `impl:${t.role}`, phase: 'Implement', schema: IMPL_SCHEMA })))
const reports = args.tasks.map((t, i) => ({ role: t.role, area: t.area, report: impl[i] }))
const reportText = JSON.stringify(reports, null, 1)

const qaPrompt = extra => `${role('qa-engineer')}\n${ENV}\nSprint ${args.sprint} goal: ${args.goal}
The engineers' reports:\n${reportText}\n${extra || ''}
You are the integration gate. You MAY restart the dev backend on :8000 and vite on :5173 with the new code (kill the old processes; backend: cd server && set -a && . ../.env && set +a && ../.venv/bin/alembic upgrade head && nohup ../.venv/bin/uvicorn src.main:app --port 8000 &; frontend: cd web && nohup npx vite --port 5173 &; simulator: .venv-edge/bin/python -m edge.src.simulator --server http://localhost:8000 --broker localhost). Leave them running at the end.
Run EVERY test suite in the repo (server, edge, web unit, typecheck, web build, e2e Playwright) and the end-to-end flow. Focus for this sprint:\n${args.qaFocus}
Do not modify production code. You may add/repair tests and e2e specs. Report precisely.`

phase('QA')
let qa = await agent(qaPrompt(), { label: 'qa', phase: 'QA', schema: QA_SCHEMA })

phase('Review')
const review = await agent(`${role('code-reviewer')}\n${ENV}\nReview the full uncommitted working tree diff (git diff HEAD plus untracked files from git status) for sprint ${args.sprint} (${args.goal}). Engineer reports:\n${reportText}`,
  { label: 'review', phase: 'Review', schema: REVIEW_SCHEMA })

let blocking = (review?.findings || []).filter(f => f.severity !== 'suggestion')
let round = 0
const fixLog = []
while (round < 3 && (!qa?.all_green || blocking.length)) {
  round++
  phase('Fix')
  const fix = await agent(`You are a senior full-stack engineer fixing sprint ${args.sprint} before merge. ${ENV}
QA failures:\n${JSON.stringify(qa?.failures || [], null, 1)}\nFailed acceptance:\n${JSON.stringify((qa?.acceptance || []).filter(a => !a.met), null, 1)}
Review findings to address (blocking + important):\n${JSON.stringify(blocking, null, 1)}
Fix root causes (never skip/disable tests). You may touch any area. Re-run the affected suites. Return the structured report.`,
    { label: `fix:round${round}`, phase: 'Fix', schema: IMPL_SCHEMA })
  fixLog.push(fix)
  qa = await agent(qaPrompt(`This is re-verification round ${round} after fixes:\n${JSON.stringify(fix, null, 1)}`), { label: `qa:round${round}`, phase: 'Fix', schema: QA_SCHEMA })
  blocking = []
}

return { sprint: args.sprint, reports, qa, review: review?.findings || [], fixRounds: round, fixLog }
