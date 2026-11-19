// Exercise catalog loading/error/empty states without running navigation checks.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('/workspace/ui/node_modules/typescript')
const source = fs.readFileSync('/workspace/ui/src/pages/NewRunPage.tsx', 'utf8')
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText

async function view(scenario) {
  const state = [], effects = [], refs = []
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
  nodes().find(node => node.props?.id === 'agent-prompt').props.onChange({ target: { value: 'Calculate molecular weight of CCO' } })
  await settle()
  return { text: text(), disabled: nodes().find(node => node.type === 'button' && text(node) === 'Start campaign').props.disabled }
}

;(async () => {
  const loading = await view({ loading: true })
  assert.match(loading.text, /Loading capabilities/); assert.doesNotMatch(loading.text, /Available capabilities \(0\)/); assert.equal(loading.disabled, true)
  const missing = await view({ status: 404 })
  assert.match(missing.text, /Capabilities unavailable/); assert.match(missing.text, /update or restart the API/); assert.doesNotMatch(missing.text, /Available capabilities \(0\)/); assert.equal(missing.disabled, true)
  const disabled = await view({ status: 503, detail: 'Agent campaigns are not enabled' })
  assert.match(disabled.text, /Agent campaigns are not enabled/); assert.equal(disabled.disabled, true)
  const malformed = await view({ catalog: { unexpected: true } })
  assert.match(malformed.text, /invalid response/); assert.equal(malformed.disabled, true)
  const empty = await view({ catalog: [] })
  assert.match(empty.text, /Available capabilities \(0\)/); assert.match(empty.text, /No capabilities are available/); assert.equal(empty.disabled, true)
  const ready = await view({ catalog: [{ id: 'descriptors', version: '1.0.0', available: true, operations: [] }] })
  assert.match(ready.text, /Available capabilities \(1\)/); assert.equal(ready.disabled, false)
  console.log('PASS: loading, missing API, disabled service, malformed catalog, empty catalog, and available catalog states.')
})().catch(error => { console.error(error); process.exitCode = 1 })
