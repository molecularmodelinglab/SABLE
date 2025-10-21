import { useEffect, useRef, useState } from 'react'
import SmilesDrawer from 'smiles-drawer'

export function MoleculeViewer({ smiles, caption }: { smiles: string; caption?: string }) {
  const containerRef = useRef<HTMLDivElement | null>(null)
  const drawerRef = useRef<any>(null)
  const [error, setError] = useState<string | null>(null)
  const idRef = useRef<string>(`smiles-${Math.random().toString(36).slice(2, 11)}`)

  // Instantiate the drawer once
  useEffect(() => {
    if (!drawerRef.current) {
      drawerRef.current = new SmilesDrawer.SmiDrawer({
        width: 220,
        height: 180,
        padding: 10,
      })
    }
  }, [])

  useEffect(() => {
    const drawer = drawerRef.current
    if (!smiles || !drawer) {
      return
    }

    const container = containerRef.current
    if (!container) {
      return
    }

    let isCancelled = false

    // Clear previous content and mount a fresh SVG target
    container.innerHTML = ''
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('id', idRef.current)
    svg.setAttribute('width', '220')
    svg.setAttribute('height', '180')
    svg.setAttribute('viewBox', '0 0 220 180')
    container.appendChild(svg)

    const handleSuccess = () => {
      if (!isCancelled) {
        setError(null)
      }
    }

    const handleError = (err: unknown) => {
      if (!isCancelled) {
        console.error('SMILES draw error:', err)
        setError(typeof err === 'string' ? err : 'Failed to render molecule')
      }
    }

    try {
      drawer.draw(smiles, svg, 'light', handleSuccess, handleError)
    } catch (err) {
      handleError(err)
    }

    return () => {
      isCancelled = true
      if (container.contains(svg)) {
        container.removeChild(svg)
      }
    }
  }, [smiles])

  return (
    <div className="molecule-viewer">
      <div ref={containerRef} className="molecule-viewer__canvas" />
      <div className="molecule-viewer__caption">
        {caption || smiles}
      </div>
      {error && <div className="molecule-viewer__error">{error}</div>}
    </div>
  )
}
