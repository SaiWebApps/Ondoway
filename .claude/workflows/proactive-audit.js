export const meta = {
  name: 'proactive-audit',
  description: 'Aggressive loop-until-dry red-team: find real defects across every surface, adversarially verify, fix each with a regression test, repeat until 2 dry rounds. Does NOT commit — the human runs the full bar + reviews + commits.',
  whenToUse: 'Run periodically (or after a big change) to proactively hunt + fix bugs. Optional args: {maxRounds?: number (default 3), surfaces?: string[] (subset), known?: string (extra already-fixed context to skip)}.',
  phases: [
    { title: 'Find', detail: 'red-teamers per surface, one round' },
    { title: 'Verify', detail: 'adversarially confirm real + new' },
    { title: 'Fix', detail: 'one fix agent per file, +regression test' },
  ],
}

// Surfaces to attack. Each finder is blind to the others; loop-until-dry catches the tail.
const ALL_SURFACES = [
  { key: 'security', brief: 'Auth/authorization, injection (Cypher/SQL/shell/path-traversal), secret leakage, unauthenticated write/DoS surfaces, CORS, SSRF. src/api/, src/**.' },
  { key: 'data-integrity', brief: 'Node/edge MERGE keys, dedup/merge/upload flows, silent data loss/overwrite, orphaning, casing/whitespace forks, migrations. src/api/crud/, src/seed/, scripts/upload*.py, frontend/review.html merge logic.' },
  { key: 'api-robustness', brief: 'FastAPI routes: unhandled exceptions -> 500, missing validation, wrong status codes, contract violations, resource leaks, uncaught provider/DB errors. src/api/routes/.' },
  { key: 'tour-engine', brief: 'The tour algorithm correctness: selection/beat_select/generation/density/verify/claim_dedup — wrong outputs, edge cases, off-by-one, ordering, cap/governor bugs. src/tour/.' },
  { key: 'audio', brief: 'TTS/audio pipeline + endpoints: cost/DoS (uncached paid calls), unbounded input, provider errors, artifact authz/leakage, soft-fail contract. src/audio/, src/api/routes/audio.py.' },
  { key: 'frontend', brief: 'The workbench UI (frontend/review.html): XSS/HTML-injection sinks, unchecked fetches -> false success, broken/locked states, race conditions, DB-down handling.' },
  { key: 'config-deploy', brief: 'Config/env resolution, .env layering, Makefile targets that could hit prod, deploy/startup hooks, destructive-op guards, the auto-resume. Makefile, src/connection.py, src/api/app.py, render.yaml.' },
]

const HOSTILE = `You are a relentless, skeptical security+correctness reviewer hunting for REAL, currently-present bugs. Report only concrete defects with file:line, the exact trigger/input, and the concrete broken outcome — NOT style or speculation. A finding with no reproducible break is worthless and will be thrown out in verification.`

const FINDINGS = { type: 'object', additionalProperties: false, required: ['findings'],
  properties: { findings: { type: 'array', items: {
    type: 'object', additionalProperties: false, required: ['severity', 'title', 'file', 'line', 'trigger', 'broken_outcome'],
    properties: {
      severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
      title: { type: 'string' }, file: { type: 'string' }, line: { type: 'integer' },
      trigger: { type: 'string' }, broken_outcome: { type: 'string' },
    } } } } }

const VERDICT = { type: 'object', additionalProperties: false, required: ['is_real', 'severity', 'reason'],
  properties: {
    is_real: { type: 'boolean', description: 'true ONLY if a concrete, reproducible, currently-present defect' },
    severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low', 'not-a-bug'] },
    reason: { type: 'string' }, fix_hint: { type: 'string' } } }

const FIXRESULT = { type: 'object', additionalProperties: false, required: ['fixed', 'unfixed'],
  properties: {
    fixed: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['title', 'what_changed', 'test_passed'],
      properties: { title: { type: 'string' }, what_changed: { type: 'string' }, test_added: { type: 'string' }, test_passed: { type: 'boolean' } } } },
    unfixed: { type: 'array', items: { type: 'object', additionalProperties: false, required: ['title', 'why'],
      properties: { title: { type: 'string' }, why: { type: 'string' } } } } } }

const REPO = '/Users/sairambkrishnan/git/ondoway'
const KNOWN = `Already fixed (do NOT re-report these): node id/merge-key overwrite protection; POI name_key canonical merge; beat merges on id; partial-coord 422; workbench CRUD gated off public via WORKBENCH_API_ENABLED; unknown-TTS-provider soft-fail; keep-exploring TTS caching; extra_narration 20k cap; worklist beat accumulate; executeMerge response.ok; case-insensitive dup check; edge-POST ok checks; escHtml popup XSS; Set-City lockout; the localhost-only destructive guards (src/main._assert_local_target, upload_paris --allow-cloud, backfill_name_key); the Aura auto-resume (src/aura_resume.py). ` + (args?.known || '')

const surfaces = (args?.surfaces && args.surfaces.length)
  ? ALL_SURFACES.filter((s) => args.surfaces.includes(s.key)) : ALL_SURFACES
const MAX = args?.maxRounds || 3
const key = (f) => `${f.file}:${f.line}:${(f.title || '').slice(0, 40)}`

const seen = new Set()
const allFixed = []
const allUnfixed = []
let dry = 0
let round = 0

while (dry < 2 && round < MAX) {
  round += 1
  phase('Find')
  const found = (await parallel(surfaces.map((s) => () =>
    agent(
      `${HOSTILE}\nSURFACE: ${s.brief}\nRepo: ${REPO}. ${KNOWN}\nFind NEW real defects on this surface only. Read the actual code. Return concrete findings with file:line + trigger + broken outcome, ranked by severity. Empty array if you genuinely find nothing new — do not invent.`,
      { label: `find:r${round}:${s.key}`, phase: 'Find', schema: FINDINGS, agentType: 'general-purpose', effort: 'high' }
    ).then((r) => (r?.findings || []))
  ))).filter(Boolean).flat()

  const fresh = found.filter((f) => !seen.has(key(f)))
  fresh.forEach((f) => seen.add(key(f)))
  if (!fresh.length) { dry += 1; log(`Round ${round}: no new candidates (dry ${dry}/2)`); continue }

  phase('Verify')
  const verified = (await parallel(fresh.map((f) => () =>
    agent(
      `Adversarially VERIFY this claimed defect. Skeptical both ways. Repo ${REPO}. [${f.severity}] "${f.title}" at ${f.file}:${f.line}. Trigger: ${f.trigger}. Broken: ${f.broken_outcome}. Read the real code (probe live if needed). is_real=true ONLY if a concrete, reproducible, currently-present defect. Give a fix_hint if real.`,
      { label: `verify:r${round}:${f.file}`, phase: 'Verify', schema: VERDICT, effort: 'high' }
    ).then((v) => ({ ...f, v }))
  ))).filter(Boolean).filter((x) => x.v?.is_real)

  if (!verified.length) { dry += 1; log(`Round ${round}: 0 confirmed (dry ${dry}/2)`); continue }
  dry = 0

  // Group confirmed defects by file so parallel fix agents never touch the same file.
  const byFile = {}
  verified.forEach((x) => { (byFile[x.file] ||= []).push(x) })
  phase('Fix')
  const results = await parallel(Object.entries(byFile).map(([file, defects]) => () =>
    agent(
      `Fix these confirmed defects in ${REPO}, editing ONLY ${file} (and, if strictly needed, a single new/adjacent TEST file — do NOT touch any other source file, another fix agent owns those). For EACH defect apply the MINIMAL correct fix and add a RED-FIRST regression test that fails without the fix and passes with it; run the affected test + \`make lint\` and confirm green. Do NOT commit. Defects:\n${defects.map((d, i) => `${i + 1}. [${d.v.severity}] ${d.title} @ line ${d.line} — ${d.broken_outcome}. fix hint: ${d.v.fix_hint || ''}`).join('\n')}\nReport what you fixed (with test_passed) and anything you could NOT fix (with why).`,
      { label: `fix:r${round}:${file}`, phase: 'Fix', schema: FIXRESULT, agentType: 'general-purpose', effort: 'high' }
    )
  ))
  results.filter(Boolean).forEach((r) => { allFixed.push(...(r.fixed || [])); allUnfixed.push(...(r.unfixed || [])) })
  log(`Round ${round}: ${verified.length} confirmed, ${allFixed.length} fixed cumulatively`)
}

return {
  rounds_run: round,
  stopped_because: dry >= 2 ? 'two dry rounds — nothing new found' : `hit maxRounds=${MAX}`,
  fixed_count: allFixed.length,
  fixed: allFixed,
  unfixed: allUnfixed,
  next_step: 'Human: run `make test` + `make test-workbench` + review the diff, then commit the fixes (they are staged in the working tree, NOT committed).',
}
