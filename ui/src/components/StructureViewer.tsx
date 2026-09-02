import { useEffect, useRef, useState } from 'react'
import { createViewer, type GLViewer } from '3dmol'
import { Maximize2, Pause, Play, RotateCcw } from 'lucide-react'
import { getCheckpointText } from '../api'

type StructureViewerProps = {
  runId: string
  filename: string
}

type Representation = 'cartoon' | 'stick' | 'ball-stick' | 'sphere'
type ColorMode = 'spectrum' | 'chain' | 'element'
type Background = 'light' | 'dark' | 'black'

const backgroundColors: Record<Background, string> = {
  light: '#f8fafc',
  dark: '#172033',
  black: '#000000',
}

function polymerColor(colorMode: ColorMode) {
  if (colorMode === 'chain') return { colorscheme: 'chain' as const }
  if (colorMode === 'element') return { colorscheme: 'Jmol' as const }
  return { color: 'spectrum' as const }
}

function applyStructureStyle(
  viewer: GLViewer,
  representation: Representation,
  colorMode: ColorMode,
) {
  const color = polymerColor(colorMode)
  viewer.setStyle({}, {})

  if (representation === 'cartoon') {
    viewer.setStyle({ hetflag: false }, { cartoon: color })
    viewer.setStyle({ hetflag: true }, { stick: { colorscheme: 'Jmol', radius: 0.2 } })
  } else if (representation === 'stick') {
    viewer.setStyle({ hetflag: false }, { stick: { ...color, radius: 0.14 } })
    viewer.setStyle({ hetflag: true }, { stick: { colorscheme: 'Jmol', radius: 0.2 } })
  } else if (representation === 'ball-stick') {
    viewer.setStyle({ hetflag: false }, {
      stick: { ...color, radius: 0.12 },
      sphere: { ...color, scale: 0.22 },
    })
    viewer.setStyle({ hetflag: true }, {
      stick: { colorscheme: 'Jmol', radius: 0.18 },
      sphere: { colorscheme: 'Jmol', scale: 0.28 },
    })
  } else {
    viewer.setStyle({ hetflag: false }, { sphere: { ...color, scale: 0.5 } })
    viewer.setStyle({ hetflag: true }, { sphere: { colorscheme: 'Jmol', scale: 0.55 } })
  }

  viewer.render()
}

export function StructureViewer({ runId, filename }: StructureViewerProps) {
  const rootRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const viewerRef = useRef<GLViewer | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [representation, setRepresentation] = useState<Representation>('cartoon')
  const [colorMode, setColorMode] = useState<ColorMode>('spectrum')
  const [background, setBackground] = useState<Background>('light')
  const [spinning, setSpinning] = useState(false)
  const [fullscreen, setFullscreen] = useState(false)

  useEffect(() => {
    if (!containerRef.current) return

    const viewer = createViewer(containerRef.current, {
      backgroundColor: backgroundColors.light,
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
    const handleFullscreenChange = () => {
      const isFullscreen = document.fullscreenElement === rootRef.current
      setFullscreen(isFullscreen)
      window.requestAnimationFrame(() => viewerRef.current?.resize())
    }
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    return () => document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }, [])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return

    let cancelled = false
    setError(null)
    setLoaded(false)
    setSpinning(false)
    viewer.spin(false)
    viewer.clear()
    viewer.render()

    getCheckpointText(runId, filename)
      .then((structure) => {
        if (cancelled) return
        const format = filename.toLowerCase().endsWith('.pdb') ? 'pdb' : 'cif'
        viewer.addModel(structure, format)
        applyStructureStyle(viewer, representation, colorMode)
        viewer.zoomTo()
        viewer.render()
        setLoaded(true)
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

  useEffect(() => {
    const viewer = viewerRef.current
    if (viewer && loaded) applyStructureStyle(viewer, representation, colorMode)
  }, [colorMode, loaded, representation])

  useEffect(() => {
    const viewer = viewerRef.current
    if (!viewer) return
    viewer.setBackgroundColor(backgroundColors[background], 1)
    viewer.render()
  }, [background])

  const toggleSpin = () => {
    const nextSpinning = !spinning
    viewerRef.current?.spin(nextSpinning ? 'y' : false, 0.6)
    setSpinning(nextSpinning)
  }

  const resetView = () => {
    viewerRef.current?.zoomTo({}, 300)
    viewerRef.current?.render()
  }

  const toggleFullscreen = async () => {
    if (document.fullscreenElement) {
      await document.exitFullscreen()
    } else {
      await rootRef.current?.requestFullscreen()
    }
  }

  return (
    <div ref={rootRef} className="structure-viewer">
      <div className="structure-viewer__toolbar" aria-label="Structure display settings">
        <label>
          <span>View</span>
          <select
            value={representation}
            onChange={(event) => setRepresentation(event.target.value as Representation)}
            disabled={!loaded}
          >
            <option value="cartoon">Cartoon</option>
            <option value="stick">Stick</option>
            <option value="ball-stick">Ball &amp; stick</option>
            <option value="sphere">Space fill</option>
          </select>
        </label>
        <label>
          <span>Color</span>
          <select
            value={colorMode}
            onChange={(event) => setColorMode(event.target.value as ColorMode)}
            disabled={!loaded}
          >
            <option value="spectrum">Spectrum</option>
            <option value="chain">Chain</option>
            <option value="element">Element</option>
          </select>
        </label>
        <label>
          <span>Background</span>
          <select
            value={background}
            onChange={(event) => setBackground(event.target.value as Background)}
          >
            <option value="light">Light</option>
            <option value="dark">Dark</option>
            <option value="black">Black</option>
          </select>
        </label>
        <div className="structure-viewer__toolbar-actions">
          <button type="button" onClick={toggleSpin} disabled={!loaded} title={spinning ? 'Stop rotation' : 'Rotate structure'} aria-label={spinning ? 'Stop rotation' : 'Rotate structure'}>
            {spinning ? <Pause size={16} /> : <Play size={16} />}
          </button>
          <button type="button" onClick={resetView} disabled={!loaded} title="Reset view" aria-label="Reset view">
            <RotateCcw size={16} />
          </button>
          <button type="button" onClick={toggleFullscreen} title={fullscreen ? 'Exit fullscreen' : 'View fullscreen'} aria-label={fullscreen ? 'Exit fullscreen' : 'View fullscreen'}>
            <Maximize2 size={16} />
          </button>
        </div>
      </div>
      <div ref={containerRef} className="structure-viewer__canvas" aria-label={`3D structure for ${filename}`} />
      {error ? <div className="structure-viewer__error">{error}</div> : null}
    </div>
  )
}