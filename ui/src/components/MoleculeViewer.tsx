import { useEffect, useRef, useState } from 'react'
import { Drawer, parse } from 'smiles-drawer'

export function MoleculeViewer({ smiles, caption }: { smiles: string; caption?: string }) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!smiles) return
    const canvas = canvasRef.current
    if (!canvas) return

    const context = canvas.getContext('2d')
    context?.clearRect(0, 0, canvas.width, canvas.height)

    const drawer = new Drawer({ width: 220, height: 180, padding: 10 })
    parse(smiles, (tree) => {
      drawer.draw(tree, canvas, 'light', false)
      setError(null)
    }, (err) => {
      setError(typeof err === 'string' ? err : 'Failed to render molecule')
    })
  }, [smiles])

  return (
    <div className="molecule-viewer">
      <canvas ref={canvasRef} width={220} height={180} />
      <div className="molecule-viewer__caption">
        {caption || smiles}
      </div>
      {error && <div className="molecule-viewer__error">{error}</div>}
    </div>
  )
}
