const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('/workspace/ui/node_modules/typescript')
const source = fs.readFileSync('/workspace/ui/src/pages/AgentCampaignPage.tsx', 'utf8')
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText
const job = (status, extra = {}) => ({ id: 'j1', campaign: 'c1', action: 'a1', skill_id: 'remote.skill', remote_id: null, status, can_cancel: true, can_reconcile: status === 'unknown', created: 1000, updated: 1000, next_poll: 1000, deadline: 2000, ...extra })
const campaign = jobs => ({ id: 'c1', status: 'waiting_jobs', busy: false, turns: 1, max_turns: 8, results: {}, jobs })
function harness(initial, admin = true) {
  let state = [], effects = [], refs = [], index = 0, pending = [], dirty = false, tree, now = 1000000, timerId = 0
  let current = initial, failure = null, posts = [], timers = new Map()
  const react = {
    useState(value) { const i = index++; if (!(i in state)) state[i] = value; return [state[i], v => { state[i] = typeof v === 'function' ? v(state[i]) : v; dirty = true }] },
    useRef(value) { const i = index++; return refs[i] ||= { current: value } },
    useEffect(fn, deps) { const i = index++; const old = effects[i]; if (!old || deps.some((v, j) => v !== old.deps[j])) pending.push(() => { old?.cleanup?.(); effects[i] = { deps, cleanup: fn() } }) },
  }
  class FakeDate extends Date { static now() { return now } }
  const exports = {}
  vm.runInNewContext(code, { exports, Date: FakeDate, TextEncoder, console, window: {
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + delay }); return id },
    clearTimeout(id) { timers.delete(id) }, setInterval() { return 0 }, clearInterval() {},
  }, require(name) {
    if (name === 'react') return react
    if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }), Fragment: 'fragment' }
    if (name === '../hooks/useAuthProfile') return { useAuthProfile: () => ({ data: { user: { roles: admin ? ['admin'] : [] } } }) }
    if (name === '../api') return { API_BASE: '', getAccessToken: () => 'token' }
    throw Error(name)
  }, fetch: async (url, options) => {
    if (options.method === 'POST') { posts.push({ url, body: JSON.parse(options.body) }); if (failure) return { ok: false, status: 409, json: async () => ({ detail: failure }) } }
    const data = url.endsWith('/events') ? [] : url === '/v2/campaigns' && options.method !== 'POST' ? [current] : current
    return { ok: true, json: async () => data }
  } })
  function render() { index = 0; dirty = false; tree = exports.AgentCampaignPage(); const jobs = pending; pending = []; jobs.forEach(fn => fn()) }
  async function settle() { for (let i = 0; i < 15; i++) { await Promise.resolve(); if (dirty) render() } }
  function nodes(node) { if (arguments.length === 0) node = tree; if (!node || typeof node !== 'object') return []; if (Array.isArray(node)) return node.flatMap(n => nodes(n)); return [node, ...nodes(node.props?.children)] }
  function text(node) { if (arguments.length === 0) node = tree; if (node == null || typeof node === 'boolean') return ''; if (typeof node !== 'object') return String(node); if (Array.isArray(node)) return node.map(text).join(' '); return text(node.props?.children) }
  return { posts, nodes, text, setCurrent(v) { current = v }, fail(v) { failure = v },
    async start() { render(); await settle(); nodes().find(n => n.type === 'select').props.onChange({ target: { value: 'c1' } }); await settle() },
    async tick(ms) { now += ms; const due = [...timers].filter(([, t]) => t.at <= now); for (const [id, t] of due) { timers.delete(id); await t.fn(); await settle() } },
    async click(label) { const button = nodes().find(n => n.type === 'button' && text(n) === label); assert.ok(button, label); assert.equal(button.props.disabled, false); button.props.onClick(); await settle() },
    async upload(file) { const input = nodes().find(n => n.props?.id === 'campaign-artifact'); assert.equal(input.props.disabled, false); input.props.onChange({ currentTarget: { files: [file], value: 'selected' } }); await settle() },
    async reconcile(value) { nodes().find(n => n.type === 'input' && n.props.type !== 'file').props.onChange({ target: { value } }); await settle(); const input = nodes().find(n => n.type === 'input' && n.props.type !== 'file'); const form = nodes().find(n => n.type === 'form' && nodes(n).includes(input)); form.props.onSubmit({ preventDefault() {} }); await settle() },
    stop() { effects.forEach(e => e?.cleanup?.()) },
  }
}
;(async () => {
  const queued = harness(campaign([job('queued')]))
  await queued.start(); await queued.tick(4999); assert.equal(queued.posts.length, 0)
  await queued.tick(1); assert.equal(queued.posts.length, 1); assert.match(queued.posts[0].url, /\/jobs\/j1\/poll$/)
  await queued.tick(4999); assert.equal(queued.posts.length, 1)
  queued.setCurrent({ ...campaign([job('succeeded', { result: { score: 1 } })]), status: 'ready' })
  await queued.tick(1); await queued.tick(30000); assert.equal(queued.posts.length, 1); assert.match(queued.text(), /Continue campaign/); queued.stop()

  const unknown = harness(campaign([job('unknown')]))
  await unknown.start(); await unknown.tick(5000); await unknown.tick(5000); assert.equal(unknown.posts.length, 0)
  assert.match(unknown.text(), /will not be automatically resubmitted/)
  await unknown.reconcile('  remote-42  '); assert.match(unknown.posts[0].url, /\/reconcile$/); assert.equal(unknown.posts[0].body.remote_id, 'remote-42'); unknown.stop()

  const nonAdmin = harness(campaign([job('unknown')]), false)
  await nonAdmin.start(); assert.equal(nonAdmin.nodes().filter(n => n.type === 'input' && n.props.type !== 'file').length, 0); assert.match(nonAdmin.text(), /Contact an administrator/); assert.match(nonAdmin.text(), /verify that the provider job belongs/); nonAdmin.stop()

  const future = harness(campaign([job('running', { next_poll: 1100 })]))
  await future.start(); await future.tick(5000); assert.equal(future.posts.length, 0); future.stop()

  const conflict = harness(campaign([job('running', { remote_id: 'r1' })]))
  await conflict.start(); conflict.fail('The service does not support cancellation through this skill')
  await conflict.click('Request cancellation'); assert.match(conflict.text(), /does not support cancellation/)
  await conflict.tick(5000); assert.equal(conflict.posts.length, 1)
  await conflict.tick(25000); assert.equal(conflict.posts.length, 2); conflict.stop()

  const timeout = harness(campaign([job('timed_out', { remote_id: 'r1' })]))
  await timeout.start(); await timeout.tick(5000); assert.equal(timeout.posts.length, 0)
  await timeout.click('Poll job now'); await timeout.click('Request cancellation'); assert.equal(timeout.posts.length, 2); assert.match(timeout.text(), /Remote work may still be running/); timeout.stop()

  const partial = harness(campaign([job('partial', { result: { score: 0.5 }, error: 'Incomplete batch', diagnostics: { quality_rejected: true } }), job('cancelling', { id: 'j2' })]))
  await partial.start(); assert.match(partial.text(), /Partial job result/); assert.match(partial.text(), /Incomplete batch/); assert.match(partial.text(), /Job diagnostics/); assert.match(partial.text(), /quality_rejected/); assert.match(partial.text(), /not yet confirmed stopped/); partial.stop()
  const cancelled = harness(campaign([job('cancelled')]))
  await cancelled.start(); assert.match(cancelled.text(), /This job is recorded as cancelled; see trace for submission details/); cancelled.stop()

  const descriptor = { input_smiles: 'C', canonical_smiles: 'C', method: 'descriptor', method_version: '1', values: [{ property: 'mass', value: 16, unit: 'g/mol' }] }
  const observationFixtures = [[descriptor], [{ data: [1, 2] }], [null], [{ ...descriptor, values: { mass: 16 } }], [{ ...descriptor, values: [{ property: 'mass', value: '16', unit: 'g/mol' }] }], null, { data: [1] }, [descriptor, { data: [2] }], [], [{ ...descriptor, values: [{ property: 'mass', value: Infinity, unit: 'g/mol' }] }]]
  for (const [index, observations] of observationFixtures.entries()) {
    const result = { observations, provenance: 'retained' }
    const view = harness({ ...campaign([]), status: 'ready', results: { r1: result } })
    await view.start()
    assert.equal(view.nodes().filter(n => n.type === 'table').length, index === 0 ? 1 : 0)
    if (index > 0) assert.ok(view.nodes().some(n => n.type === 'pre' && view.text(n) === JSON.stringify(result, null, 2)))
    view.stop()
  }
  console.log('PASS: 7 job/control scenarios, 1 neutral cancellation wording scenario, 10 observation fixtures (descriptor table or complete JSON fallback).')
  for (const status of ['ready', 'awaiting_user', 'waiting', 'waiting_jobs']) {
    const view = harness({ ...campaign([]), status })
    await view.start()
    view.setCurrent({ ...campaign([]), status, artifacts: [{ id: 'artifact-1', name: 'ligand.sdf', size_bytes: 10, sha256: 'abc123' }] })
    await view.upload({ name: 'ligand.sdf', size: 10, text: async () => 'coordinates' })
    assert.equal(view.posts.length, 1); assert.equal(view.posts[0].url, '/v2/campaigns/c1/artifacts')
    assert.equal(view.posts[0].body.name, 'ligand.sdf'); assert.equal(view.posts[0].body.content, 'coordinates')
    assert.match(view.text(), /artifact-1/); assert.match(view.text(), /ligand.sdf/); view.stop()
  }
  const large = harness({ ...campaign([]), status: 'ready' })
  await large.start(); let reads = 0
  await large.upload({ name: 'large.pdb', size: 3000001, text: async () => { reads++; return 'x' } })
  assert.equal(reads, 0); assert.equal(large.posts.length, 0); assert.match(large.text(), /no larger than 3 MB/); large.stop()
  const encoding = harness({ ...campaign([]), status: 'ready' })
  await encoding.start(); await encoding.upload({ name: 'text.txt', size: 1, text: async () => 'é'.repeat(1500001) })
  assert.equal(encoding.posts.length, 0); assert.match(encoding.text(), /File text exceeds/); encoding.stop()
  const uploadError = harness({ ...campaign([]), status: 'ready' })
  await uploadError.start(); uploadError.fail('Campaign decision is in progress')
  await uploadError.upload({ name: 'ligand.sdf', size: 1, text: async () => 'x' })
  assert.match(uploadError.text(), /Campaign decision is in progress/); uploadError.stop()
  for (const state of [{ ...campaign([]), busy: true }, { ...campaign([]), status: 'completed' }]) {
    const view = harness(state); await view.start()
    assert.equal(view.nodes().find(n => n.props?.id === 'campaign-artifact').props.disabled, true); view.stop()
  }
  const create = harness({ ...campaign([]), status: 'ready' })
  await create.start(); await create.click('Create campaign to add files')
  assert.equal(create.posts.length, 1); assert.equal(create.posts[0].url, '/v2/campaigns'); assert.ok(create.posts[0].body.message); create.stop()
  console.log('PASS: 10 artifact scenarios: 4 allowed statuses, file byte cap before read, encoded text cap, API error display, busy/completed gating, create without advance.')
  for (const [status, canCancel, canReconcile, cancelVisible, reconcileVisible] of [
    ['queued', true, false, true, false], ['running', false, true, false, false],
    ['unknown', false, false, false, false], ['unknown', true, true, true, true],
    ['succeeded', true, true, false, false],
  ]) {
    const view = harness(campaign([job(status, { can_cancel: canCancel, can_reconcile: canReconcile })]))
    await view.start()
    assert.equal(view.nodes().some(n => n.type === 'button' && view.text(n) === 'Request cancellation'), cancelVisible)
    assert.equal(view.nodes().some(n => n.type === 'button' && view.text(n) === 'Link remote job'), reconcileVisible)
    if (status === 'unknown' && !canReconcile) {
      assert.match(view.text(), /provider has no safe status lookup/); assert.match(view.text(), /Operator review is required/)
      assert.match(view.text(), /will not be automatically resubmitted/)
      await view.tick(5000); assert.equal(view.posts.length, 0)
    }
    view.stop()
  }
  console.log('PASS: 5 capability-gating scenarios: queued local cancellation, unsupported active cancellation, unsafe unknown lookup, supported admin reconciliation, terminal controls hidden.')
  assert.ok([...queued.posts, ...unknown.posts, ...conflict.posts, ...timeout.posts].every(p => !p.url.endsWith('/advance')))
  console.log('PASS: queued submission polling, 5s bound, ready stop, no unknown retries, explicit trimmed reconciliation, future due time, 409 details and 30s backoff, timeout controls, partial results and cancellation wording, admin-only reconciliation with ownership guidance; no model advance from job controls.')
})().catch(e => { console.error(e); process.exitCode = 1 })
