declare module 'smiles-drawer' {
  export type ThemeName = 'light' | 'dark'

  export interface DrawerOptions {
    width?: number
    height?: number
    padding?: number
    explicitHydrogens?: boolean
    overlapSensitivity?: number
    themes?: Record<string, unknown>
  }

  export class Drawer {
    constructor(options?: DrawerOptions)
    draw(tree: unknown, target: string | HTMLCanvasElement, theme?: ThemeName, hydrate?: boolean): void
    static apply(themeName: ThemeName): void
  }

  export function parse(smiles: string, onSuccess: (tree: unknown) => void, onError?: (err: unknown) => void): void
}
