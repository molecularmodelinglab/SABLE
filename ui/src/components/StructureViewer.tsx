import { useEffect, useRef, useState } from 'react'
import { createViewer, type GLViewer } from '3dmol'
import { getCheckpointText } from '../api'

type StructureViewerProps = {
  runId: string
  filename: string
}

export function StructureViewer({ runId, filename }: StructureViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<GLViewer | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const viewer = createViewer(containerRef.current, {
      backgroundColor: '#f8fafc',
      antialias: true,
    })
    viewerRef.current = viewer

    return () => {
      viewer.clear()
      viewerRef.current = null
      containerRef.current?.replaceChildren()
    }
  }, [])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return

    let cancelled = false
    setError(null)
    viewer.clear()
    viewer.render()

    getCheckpointText(runId, filename)
      .then((structure) => {
        if (cancelled) return
        const format = filename.toLowerCase().endsWith('.pdb') ? 'pdb' : 'cif'
        viewer.addModel(structure, format)
        viewer.setStyle({ polymer: true }, { cartoon: { color: 'spectrum' } })
        viewer.setStyle({ hetflag: true }, { stick: { colorscheme: 'Jmol', radius: 0.2 } })
        viewer.zoomTo()
        viewer.render()
      })
      .catch((loadError: unknown) => {
        if (!cancelled) {
          setError(loadError instanceof Error ? loadError.message : 'Unable to load structure')
        }
      })

    return () => {
      cancelled = true
    }
  }, [filename, runId])

  return (
    <div className="structure-viewer">
      <div ref={containerRef} className="structure-viewer__canvas" aria-label={`3D structure for ${filename}`} />
      {error ? <div className="structure-viewer__error">{error}</div> : null}
    </div>
  )
}