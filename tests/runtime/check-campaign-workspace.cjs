// Exercise the conversation workspace and its collapsible supporting panels.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('/workspace/ui/node_modules/typescript')
const source = fs.readFileSync('/workspace/ui/src/pages/NewRunPage.tsx', 'utf8')
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText

async function view(scenario) {
  const state = [], effects = [], refs = []; if (scenario.campaign) state[1] = scenario.campaign
  let index = 0, pending = [], dirty = false, tree
  const react = {
    useState(value) { const i = index++; if (!(i in state)) state[i] = value; return [state[i], value => { state[i] = typeof value === 'function' ? value(state[i]) : value; dirty = true }] },
    useRef(value) { return refs[index++] ||= { current: value } },
    useEffect(fn, deps) { const i = index++; if (!effects[i] || deps.some((v, n) => effects[i][n] !== v)) pending.push(() => { effects[i] = deps; fn() }) },
  }
  const exports = {}
  vm.runInNewContext(code, { exports, console, TextEncoder, window: { setInterval() {}, clearInterval() {}, setTimeout() {}, clearTimeout() {} }, require(name) {
    if (name === 'react') return react
    if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    if (name === '../hooks/useAuthProfile') return { useAuthProfile: () => ({ data: null }) }
    if (name === '../api') return { API_BASE: '', getAccessToken: () => 'test-token' }
    throw Error(name)
  }, fetch: async (url, options) => {
    assert.equal(options.method, 'GET')
    if (url.endsWith('/capabilities')) {
      if (scenario.loading) return new Promise(() => {})
      return { ok: !scenario.status, status: scenario.status || 200, json: async () => scenario.status ? { detail: scenario.detail || 'Not Found' } : scenario.catalog }
    }
    return { ok: true, json: async () => [] }
  } })
  function render() { index = 0; dirty = false; tree = exports.NewRunPage(); const tasks = pending; pending = []; tasks.forEach(fn => fn()) }
  async function settle() { for (let n = 0; n < 20; n++) { await Promise.resolve(); if (dirty) render() } }
  function nodes(node) { if (arguments.length === 0) node = tree; if (!node || typeof node !== 'object') return []; if (Array.isArray(node)) return node.flatMap(n => nodes(n)); return [node, ...nodes(node.props?.children || [])] }
  function text(node) { if (arguments.length === 0) node = tree; if (node == null || typeof node === 'boolean') return ''; if (typeof node !== 'object') return String(node); if (Array.isArray(node)) return node.map(n => text(n)).join(' '); return text(node.props?.children || '') }
  render(); await settle()
  return { nodes, text, async click(label) { const button = nodes().find(node => node.type === 'button' && text(node).replace(/\s+/g, ' ').trim() === label.replace(/\s+/g, ' ').trim()); assert.ok(button, label); button.props.onClick(); await settle() } }

}


;(async () => {
  const catalog = [{ id: 'descriptors', version: '1.0.0', available: true, operations: [] }]
  const fresh = await view({ catalog })
  assert.equal(fresh.nodes().filter(node => node.type === 'textarea').length, 1)
  assert.equal(fresh.nodes().find(node => node.props?.id === 'campaign-details').props.hidden, true)
  await fresh.click('Campaign details')
  assert.equal(fresh.nodes().find(node => node.props?.id === 'campaign-details').props.hidden, false)
  await fresh.click('Hide details')
  assert.equal(fresh.nodes().find(node => node.props?.id === 'campaign-details').props.hidden, true)
  await fresh.click('Compare the properties of these molecules  ↗')
  assert.equal(fresh.nodes().find(node => node.props?.id === 'agent-prompt').props.value, 'Compare the properties of these molecules')
  const campaign = { id: 'test-campaign', status: 'awaiting_user', question: 'Which property matters most?', busy: false, turns: 1, max_turns: 24, results: {}, history: [{ role: 'user', content: 'Compare my molecules' }, { role: 'assistant', decision: { name: 'ask_user', arguments: { question: 'Which property matters most?' } } }] }
  const active = await view({ catalog, campaign })
  assert.equal(active.nodes().filter(node => node.type === 'textarea').length, 1)
  assert.ok(active.nodes().find(node => node.props?.id === 'agent-reply'))
  assert.equal(active.nodes().filter(node => node.type === 'p' && active.text(node) === campaign.question).length, 1)
  assert.match(active.text(), /Compare my molecules/)
  assert.equal(active.nodes().find(node => node.type === 'details' && active.text(node.props.children[0]).startsWith('Activity')).props.open, undefined)
  const complete = await view({ catalog, campaign: { ...campaign, question: null, status: 'completed', report: { summary: 'The comparison is complete.', evidence: [] } } })
  assert.match(complete.text(), /The comparison is complete/)
  assert.equal(complete.nodes().filter(node => node.type === 'textarea').length, 0)
  await complete.click('Explore results and evidence')
  assert.equal(complete.nodes().find(node => node.props?.id === 'campaign-details').props.hidden, false)
  console.log('PASS: collapsed details, example prompts, single conversation composer, durable question history, and report access.')
})().catch(error => { console.error(error); process.exitCode = 1 })
