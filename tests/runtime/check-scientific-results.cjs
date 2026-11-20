// Verify scientific result labels and count accounting without a live provider.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const vm = require('node:vm')
const ts = require('/workspace/ui/node_modules/typescript')
const source = fs.readFileSync('/workspace/ui/src/components/campaign/presentation.ts', 'utf8')
const exportsObject = {}
vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText, { exports: exportsObject })
const prediction = { kind: 'boltz_prediction', provider: 'self_hosted', protein_scope_id: 'target', measurement: { id: 'one', smiles: 'CCO', metrics: { affinity: -2, binding_confidence: 0.8 } } }
const view = exportsObject.present({ kind: 'remote_batch_result', results: [prediction], failures: [{ job_id: 'failed' }] })
assert.equal(view.title, 'Binding predictions')
assert.equal(view.blocks[0].rows.length, 2)
assert.match(view.blocks[0].rows[0].metric, /log10/)
assert.match(view.summary, /1 calculation/)
assert.match(exportsObject.present({ kind: 'remote_batch', jobs: [{}, {}] }).summary, /2 molecule jobs/)
const library = exportsObject.present({ kind: 'candidate_library', candidates: [{ id: 'a', smiles: 'CCO' }], enumeration: { source_count: 4, requested_product_cap: 8000, backend_filtered_count: 0, truncated_count: 0, exclusion_counts: { seed_or_duplicate: 3 } } })
assert.match(library.summary, /1 usable analogues from 4 returned products/)
assert.match(library.summary, /separate from the number scored/)
assert.equal(library.blocks[0].title, 'Library generation')
assert.equal(library.blocks[0].rows.find(row => row.stage === 'Seed or duplicate').count, 3)
console.log('PASS: binding metrics, partial failures, enumeration counts, and separate evaluation budgets.')

// Render the result card with a small hook/JSX harness to check export and isolation controls.
function nodes(value) {
  if (!value || typeof value !== 'object') return []
  if (Array.isArray(value)) return value.flatMap(nodes)
  return [value, ...nodes(value.props?.children)]
}
const state = []; let index = 0
const react = { useState(value) { const i = index++; if (!(i in state)) state[i] = value; return [state[i], next => { state[i] = typeof next === 'function' ? next(state[i]) : next }] }, useRef: () => ({ current: null }), useEffect() {} }
const modules = { './presentation': exportsObject }
function load(relative) {
  const output = {}
  const code = ts.transpileModule(fs.readFileSync(`/workspace/ui/src/components/campaign/${relative}.tsx`, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020 } }).outputText
  vm.runInNewContext(code, { exports: output, require(name) {
    if (name === 'react') return react
    if (name === 'react/jsx-runtime') return { jsx: (type, props) => ({ type, props }), jsxs: (type, props) => ({ type, props }) }
    if (name in modules) return modules[name]
    if (name === '../../api') return { API_BASE: '', getAccessToken: () => 'token' }
    return new Proxy({}, { get: (_, key) => String(key) })
  } })
  return output
}
const structures = load('PredictionStructures'); modules['./PredictionStructures'] = structures
const retained = { ...prediction, measurement: { ...prediction.measurement, structure: { id: 'artifact-a', name: 'model.cif' } } }
const resolved = structures.predictionStructures({ kind: 'remote_batch_result', results: [retained] })
assert.equal(resolved[0].structure.id, 'artifact-a'); assert.equal(resolved[0].metrics.affinity, -2); assert.equal(resolved[0].target, 'target')
const component = load('CampaignResult')
const props = { campaignId: 'campaign-a', id: 'plot', result: { figure_svg: '<svg/>', figure_html: '<html>Plot</html>' } }
function render() { index = 0; return nodes(component.CampaignResult(props)) }
let rendered = render()
assert.ok(rendered.some(n => n.type === 'button' && n.props.children === 'Download figure (SVG)'))
assert.ok(!rendered.some(n => n.type === 'iframe'))
rendered.find(n => n.type === 'button' && n.props.children === 'Explore interactive figure').props.onClick()
rendered = render()
const iframe = rendered.find(n => n.type === 'iframe')
assert.equal(iframe.props.sandbox, 'allow-scripts allow-downloads')
assert.equal(iframe.props.referrerPolicy, 'no-referrer')
assert.equal(rendered.find(n => n.type === structures.PredictionStructures).props.campaignId, 'campaign-a')
console.log('PASS: SVG export, opt-in isolated plot viewer, campaign ownership context and molecule-specific structures.')
