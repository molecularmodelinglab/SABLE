declare module 'smiles-drawer' {
  export type ThemeName = 'light' | 'dark'

  export interface DrawerOptions {
    width?: number
    height?: number
    padding?: number
    bondThickness?: number
    bondLength?: number
    explicitHydrogens?: boolean
    overlapSensitivity?: number
    themes?: Record<string, unknown>
    [key: string]: any
  }

  export class Drawer {
    constructor(options?: DrawerOptions)
    draw(tree: unknown, target: string | HTMLCanvasElement, theme?: ThemeName, hydrate?: boolean): void
    static apply(themeName: ThemeName): void
  }

  export class SvgDrawer {
    constructor(options?: DrawerOptions)
    draw(tree: unknown, target: string | SVGElement, theme?: ThemeName, hydrate?: boolean): void
  }

  export class SmiDrawer {
    constructor(moleculeOptions?: DrawerOptions, reactionOptions?: Record<string, unknown>)
    draw(
      smiles: string,
      target: string | HTMLImageElement | HTMLCanvasElement | SVGElement,
      theme?: ThemeName,
      successCallback?: (element: Element) => void,
      errorCallback?: (err: unknown) => void,
      weights?: unknown
    ): void
    apply(
      attribute?: string,
      theme?: ThemeName,
      successCallback?: (element: Element) => void,
      errorCallback?: (err: unknown) => void
    ): void
  }

  export function parse(smiles: string, onSuccess: (tree: unknown) => void, onError?: (err: unknown) => void): void

  const SmilesDrawer: {
    Drawer: typeof Drawer
    SvgDrawer: typeof SvgDrawer
    SmiDrawer: typeof SmiDrawer
    parse: typeof parse
  }

  export default SmilesDrawer
}
