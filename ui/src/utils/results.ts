import { type RunResults } from '../api'

const SMILES_KEYS = ['smiles', 'smiles_string', 'canonical_smiles', 'SMILES', 'Smiles']

export type MoleculeRecord = {
  id: string
  smiles: string
  data: Record<string, unknown>
}

export function extractMoleculeRecords(results: RunResults): MoleculeRecord[] {
  const items = normalizeItems(results)
  return items
    .map((item, index) => {
      if (!item || typeof item !== 'object') return null
      const record = item as Record<string, unknown>
      const smilesKey = SMILES_KEYS.find((key) => key in record)
      const smilesValue = smilesKey ? record[smilesKey] : undefined
      if (typeof smilesValue !== 'string') return null
      const id = readIdentifier(record, index)
      return { id, smiles: smilesValue, data: record }
    })
    .filter((item): item is MoleculeRecord => Boolean(item))
}

export function extractNumericSeries(records: Record<string, unknown>[]) {
  const values: Record<string, number[]> = {}
  records.forEach((record) => {
    walk(record, (key, value) => {
      if (typeof value === 'number' && Number.isFinite(value)) {
        if (!values[key]) values[key] = []
        values[key].push(value)
      }
    })
  })
  return values
}

export function flattenRecord(record: Record<string, unknown>) {
  const flat: Record<string, unknown> = {}
  walk(record, (key, value) => {
    flat[key] = value
  })
  return flat
}

function walk(value: unknown, visitor: (key: string, value: number) => void, prefix = '') {
  if (!value || typeof value !== 'object') return
  const entries = Object.entries(value as Record<string, unknown>)
  entries.forEach(([key, val]) => {
    const nextKey = prefix ? `${prefix}.${key}` : key
    if (typeof val === 'number') {
      visitor(nextKey, val)
    } else if (Array.isArray(val)) {
      const numericArray = val.filter((x) => typeof x === 'number') as number[]
      if (numericArray.length === val.length && numericArray.length) {
        numericArray.forEach((num, idx) => visitor(`${nextKey}[${idx}]`, num))
      }
    } else if (val && typeof val === 'object') {
      walk(val, visitor, nextKey)
    }
  })
}

function normalizeItems(results: RunResults): unknown[] {
  if (!results) return []
  if (Array.isArray(results)) return results
  if (typeof results === 'object') {
    const record = results as Record<string, unknown>
    if (Array.isArray(record.experimental_data)) return record.experimental_data
    if (Array.isArray(record.items)) return record.items
    if (Array.isArray(record.molecules)) return record.molecules
    if (Array.isArray(record.results)) return record.results
    if (Array.isArray(record.candidates)) return record.candidates
    const arrays = Object.values(record).filter(Array.isArray)
    if (arrays.length) return arrays[0] as unknown[]
  }
  return []
}

function readIdentifier(record: Record<string, unknown>, fallbackIndex: number) {
  const candidates = ['molecule_id', 'id', 'name', 'label', 'identifier', 'compound', 'molecule']
  for (const key of candidates) {
    const value = record[key]
    if (typeof value === 'string' && value.trim()) return value
    if (typeof value === 'number') return String(value)
  }
  return `candidate_${fallbackIndex + 1}`
}
