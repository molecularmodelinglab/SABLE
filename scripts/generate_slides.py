#!/usr/bin/env python3
"""
Generate a standalone HTML slide deck from all "*_final.json" files in a folder.

Usage:
  python scripts/generate_slides.py <folder> [--output OUTFILE] [--pattern PATTERN] [--recursive]

The slide for each JSON shows:
 - prompt (user_prompt or prompt)
 - target_properties (parsed_arguments.target_properties or targets)
 - summary (if present) or a generated summary (best_molecules + experimental_results)
 - starting compound (first entry of starting_molecules)
 - compounds in the summary (experimental_results and best_molecules) with properties/values

Assumptions:
 - If `summary` key is missing, the script will show `best_molecules` and `experimental_results` as the summary.
 - Target properties may exist under `parsed_arguments.target_properties` or top-level `targets`.
"""
from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
import base64
import io

# Try to import RDKit for rendering. If unavailable we'll fall back to showing SMILES text.
try:
    from rdkit import Chem
    from rdkit.Chem import Draw
    RDKit_AVAILABLE = True
except Exception:
    RDKit_AVAILABLE = False


def safe_get_prompt(data: Dict[str, Any]) -> Optional[str]:
    return data.get("user_prompt") or data.get("prompt") or None


def safe_get_targets(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    pa = data.get("parsed_arguments") or {}
    t = pa.get("target_properties") or data.get("targets") or []
    # normalize simple dicts
    result = []
    for item in t:
        if isinstance(item, dict):
            result.append(item)
        else:
            result.append({"name": str(item)})
    return result


def render_props_table(props: Dict[str, Any]) -> str:
    if not props:
        return "<p><em>No properties</em></p>"
    rows = []
    for k, v in props.items():
        rows.append(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>")
    return "<table class=props>" + "".join(rows) + "</table>"


def render_best_molecules(best: List[Any]) -> str:
    if not best:
        return "<p><em>No best_molecules</em></p>"
    rows = ["<tr><th>SMILES</th><th>Score</th></tr>"]
    for item in best:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            smiles = item[0]
            score = item[1]
        else:
            smiles = str(item)
            score = ""
        rows.append(f"<tr><td>{html.escape(str(smiles))}</td><td>{html.escape(str(score))}</td></tr>")
    return "<table class=best>" + "".join(rows) + "</table>"


def render_experimental_results(experimental: List[Dict[str, Any]]) -> str:
    if not experimental:
        return "<p><em>No experimental_results</em></p>"
    out = ["<div class=exp-list>"]
    for e in experimental:
        mid = e.get("molecule_id") or e.get("smiles") or ""
        smiles = e.get("smiles", "")
        props = e.get("properties") or {}
        meta_all = (e.get("metadata") or {}).get("all_properties") or {}
        out.append(f"<div class=exp-item><h4>{html.escape(str(mid))} — {html.escape(str(smiles))}</h4>")
        out.append("<div class=cols>")
        out.append("<div class=col><h5>Properties</h5>" + render_props_table(props) + "</div>")
        out.append("<div class=col><h5>All Properties</h5>" + render_props_table(meta_all) + "</div>")
        out.append("</div></div>")
    out.append("</div>")
    return "".join(out)


def mol_to_base64_png(smiles: str, legend: str = "", size=(320, 200)) -> Optional[str]:
    """Return a data URI for a PNG image of the molecule, or None if rendering fails or RDKit missing."""
    if not smiles:
        return None
    if not RDKit_AVAILABLE:
        return None
    try:
        mol = Chem.MolFromSmiles(smiles)
        if mol is None:
            return None
        img = Draw.MolToImage(mol, size=size, legend=legend)
        bio = io.BytesIO()
        img.save(bio, format='PNG')
        data = base64.b64encode(bio.getvalue()).decode('ascii')
        return f"data:image/png;base64,{data}"
    except Exception:
        return None


def parse_summary_text(summary: str, target_names: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    """Parse a textual summary (like the sample) to extract starting molecules and top optimized molecules with properties.

    Returns a dict with keys 'starting' and 'top', each a list of dicts {'smiles':..., 'properties':{...}}
    This is a best-effort parser and tolerant to variations.
    """
    import re

    lines = summary.splitlines()
    section = None
    starting: List[Dict[str, Any]] = []
    top: List[Dict[str, Any]] = []

    # helper to parse property key:value pairs from a fragment like 'qed: 0.742, logp: 2.540'
    def parse_props(fragment: str) -> Dict[str, Any]:
        props: Dict[str, Any] = {}
        for part in re.split(r'[;,]', fragment):
            part = part.strip()
            m = re.match(r'([A-Za-z0-9_\- ]+)\s*:\s*([0-9eE+\-.]+)', part)
            if m:
                key = m.group(1).strip()
                val = m.group(2).strip()
                try:
                    props[key] = float(val)
                except Exception:
                    props[key] = val
        return props

    last_added = None
    for line in lines:
        s = line.strip()
        if not s:
            continue
        low = s.lower()
        if 'starting molecule' in low or 'starting molecules' in low:
            section = 'starting'
            continue
        if ('top' in low and ('optim' in low or 'optimized' in low)) or 'top' in low and 'optimized' in low:
            section = 'top'
            continue

        # explicit SMILES label
        if 'smiles:' in low:
            try:
                smiles = s.split(':', 1)[1].strip()
            except Exception:
                smiles = ''
            entry = {'smiles': smiles, 'properties': {}}
            if section == 'top':
                top.append(entry)
            else:
                starting.append(entry)
            last_added = entry
            continue

        # numbered list like '1. CN1CC...'
        m = re.match(r'^\d+\.\s+([^\s]+)', s)
        if m:
            token = m.group(1).strip()
            if len(token) > 6 and re.search(r'[A-Za-z0-9@#=\-\[\]\(\)/+\\]', token):
                entry = {'smiles': token, 'properties': {}}
                if section == 'top':
                    top.append(entry)
                else:
                    starting.append(entry)
                last_added = entry
                rest = s[m.end():].strip()
                if rest and ( 'baseline' in rest.lower() or 'properties' in rest.lower() or ':' in rest):
                    props = parse_props(rest)
                    last_added['properties'].update(props)
                continue

        # lines like 'Baseline: qed: 0.742, logp: 2.540' or 'Properties: ...'
        if any(k in low for k in ('baseline', 'properties')):
            parts = s.split(':', 1)
            if len(parts) >= 2 and last_added is not None:
                frag = parts[1]
                props = parse_props(frag)
                last_added['properties'].update(props)
            continue

        # catch inline key:value groups
        m2 = re.search(r'([A-Za-z0-9_\- ]+:\s*[0-9eE+\-.]+(?:\s*,\s*[A-Za-z0-9_\- ]+:\s*[0-9eE+\-.]+)*)', s)
        if m2 and last_added is not None:
            props = parse_props(m2.group(1))
            last_added['properties'].update(props)

    return {'starting': starting, 'top': top}


HTML_TEMPLATE = """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>Final JSON slides</title>
  <style>
    body{font-family: Arial, Helvetica, sans-serif; margin:0; padding:0; background:#222; color:#eee}
    .slide{display:none; padding:30px; box-sizing:border-box; height:100vh}
    .slide.visible{display:block}
    .container{max-width:1100px; margin:0 auto}
    h1,h2,h3{color:#fff}
    .props, .best{border-collapse:collapse; width:100%; margin:8px 0}
    .props td, .best td, .best th{border:1px solid #444; padding:6px}
    .props td:first-child{width:30%; color:#ddd}
    .meta{background:#111; padding:10px; border-radius:6px}
    .controls{position:fixed; bottom:12px; right:12px; background:#0008; padding:8px 12px; border-radius:6px}
    .cols{display:flex; gap:16px}
    .col{flex:1}
    a.file{color:#9cf}
    pre.jsondump{background:#111; padding:10px; border-radius:6px; max-height:300px; overflow:auto}
  </style>
  <script>
    let current = 0;
    function show(i){
      const slides = document.querySelectorAll('.slide');
      if(i<0) i=0; if(i>=slides.length) i=slides.length-1;
      slides.forEach((s,idx)=> s.classList.toggle('visible', idx===i));
      document.getElementById('counter').textContent = `${i+1}/${slides.length}`;
      current = i;
    }
    function next(){ show(current+1); }
    function prev(){ show(current-1); }
    document.addEventListener('keydown', (e)=>{
      if(e.key==='ArrowRight') next();
      if(e.key==='ArrowLeft') prev();
    });
    window.onload = ()=> show(0);
  </script>
</head>
<body>
    __BODY__
  <div class=controls>
    <button onclick="prev()">◀ Prev</button>
    <span id=counter></span>
    <button onclick="next()">Next ▶</button>
  </div>
</body>
</html>
"""


def build_slide_block(filename: str, data: Dict[str, Any]) -> str:
    prompt = safe_get_prompt(data) or "(no prompt)"
    targets = safe_get_targets(data)
    # try a summary key, else fallback
    summary = data.get('summary')
    starting = (data.get('starting_molecules') or [])
    starting_smiles = starting[0] if starting else ""

    parts = [f'<div class="slide" id="slide-{html.escape(filename)}"><div class="container">']
    parts.append(f"<h1>{html.escape(filename)}</h1>")
    parts.append(f"<h2>Prompt</h2><div class=meta><pre class=jsondump>{html.escape(str(prompt))}</pre></div>")
    # targets
    parts.append("<h2>Target properties</h2>")
    if targets:
        trows = ["<table class=props><tr><th>property_name</th><th>mode/optimization</th><th>weight</th></tr>"]
        for t in targets:
            name = t.get('property_name') or t.get('name') or ''
            mode = t.get('optimization_mode') or t.get('mode') or ''
            weight = t.get('weight') or ''
            trows.append(f"<tr><td>{html.escape(str(name))}</td><td>{html.escape(str(mode))}</td><td>{html.escape(str(weight))}</td></tr>")
        trows.append("</table>")
        parts.append("".join(trows))
    else:
        parts.append("<p><em>No target properties</em></p>")

    # Render starting molecule image (if RDKit available) and label with target property values if present
    parts.append("<h2>Starting compound</h2>")
    start_label = ""
    # For starting molecule there may be no properties available; we'll show property names as header
    target_names = [t.get('property_name') or t.get('name') or '' for t in targets]
    if starting_smiles:
        # no properties for starting molecule in many cases; build an empty label of property names
        start_label = " ".join([n for n in target_names if n])
        img_uri = mol_to_base64_png(starting_smiles, legend=start_label)
        if img_uri:
            parts.append(f"<div class=meta><img src=\"{img_uri}\" alt=\"{html.escape(starting_smiles)}\"/></div>")
        else:
            parts.append(f"<div class=meta>{html.escape(str(starting_smiles))}</div>")
    else:
        parts.append("<div class=meta><em>(no starting molecule)</em></div>")

    # summary or fallback
    parts.append("<h2>Summary</h2>")
    if summary:
        # If summary is a dict show it pretty. If it's a textual summary, try to parse molecules and properties.
        if isinstance(summary, dict):
            parts.append("<div class=meta><pre class=jsondump>" + html.escape(json.dumps(summary, indent=2)) + "</pre></div>")
            compounds = summary.get('compounds') or summary.get('molecules') or []
            if compounds:
                parts.append("<h3>Compounds in summary</h3>")
                for c in compounds:
                    s = c.get('smiles') or c.get('molecule_id') or str(c)
                    props = c.get('properties') or c.get('all_properties') or {}
                    # prepare legend from target properties and available values
                    legend_parts = []
                    for tn in target_names:
                        if not tn:
                            continue
                        val = props.get(tn)
                        # also try uppercase/lowercase variants
                        if val is None:
                            val = props.get(tn.lower()) or props.get(tn.upper())
                        if val is None:
                            legend_parts.append(f"{tn}:")
                        else:
                            legend_parts.append(f"{tn}:{val}")
                    legend = " ".join(legend_parts)
                    img_uri = mol_to_base64_png(s, legend=legend)
                    if img_uri:
                        parts.append(f"<div class=meta><h4>{html.escape(str(s))}</h4><img src=\"{img_uri}\" alt=\"{html.escape(str(s))}\"/>" + render_props_table(props) + "</div>")
                    else:
                        parts.append(f"<div class=meta><h4>{html.escape(str(s))}</h4>" + render_props_table(props) + "</div>")
        else:
            # textual summary: attempt to parse molecules and their properties
            parts.append("<div class=meta><pre class=jsondump>" + html.escape(str(summary)) + "</pre></div>")
            parsed = parse_summary_text(str(summary), target_names)
            s_list = parsed.get('starting', [])
            t_list = parsed.get('top', [])
            if s_list:
                parts.append("<h3>Parsed starting molecules</h3>")
                for entry in s_list:
                    s = entry.get('smiles')
                    props = entry.get('properties') or {}
                    legend_parts = []
                    for tn in target_names:
                        if not tn:
                            continue
                        val = props.get(tn)
                        if val is None:
                            val = props.get(tn.lower()) or props.get(tn.upper())
                        if val is None:
                            legend_parts.append(f"{tn}:")
                        else:
                            legend_parts.append(f"{tn}:{val}")
                    legend = " ".join(legend_parts)
                    img_uri = mol_to_base64_png(s, legend=legend)
                    if img_uri:
                        parts.append(f"<div class=meta><h4>{html.escape(str(s))}</h4><img src=\"{img_uri}\" alt=\"{html.escape(str(s))}\"/>" + render_props_table(props) + "</div>")
                    else:
                        parts.append(f"<div class=meta><h4>{html.escape(str(s))}</h4>" + render_props_table(props) + "</div>")
            if t_list:
                parts.append("<h3>Parsed top optimized molecules</h3>")
                for entry in t_list:
                    s = entry.get('smiles')
                    props = entry.get('properties') or {}
                    legend_parts = []
                    for tn in target_names:
                        if not tn:
                            continue
                        val = props.get(tn)
                        if val is None:
                            val = props.get(tn.lower()) or props.get(tn.upper())
                        if val is None:
                            legend_parts.append(f"{tn}:")
                        else:
                            legend_parts.append(f"{tn}:{val}")
                    legend = " ".join(legend_parts)
                    img_uri = mol_to_base64_png(s, legend=legend)
                    if img_uri:
                        parts.append(f"<div class=meta><h4>{html.escape(str(s))}</h4><img src=\"{img_uri}\" alt=\"{html.escape(str(s))}\"/>" + render_props_table(props) + "</div>")
                    else:
                        parts.append(f"<div class=meta><h4>{html.escape(str(s))}</h4>" + render_props_table(props) + "</div>")
    else:
        # fallback to best_molecules and experimental_results
        bm = data.get('best_molecules') or []
        er = data.get('experimental_results') or []
        # Render best_molecules (include small images where possible)
        parts.append("<h3>Best molecules</h3>")
        if bm:
            parts.append('<div class=best-list>')
            for item in bm:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    smiles = item[0]
                    score = item[1]
                else:
                    smiles = str(item)
                    score = ''
                legend_parts = []
                for tn in target_names:
                    legend_parts.append(tn)
                legend = " ".join([p for p in legend_parts if p])
                img_uri = mol_to_base64_png(smiles, legend=legend)
                if img_uri:
                    parts.append(f"<div class=meta><h4>{html.escape(str(smiles))} — score:{html.escape(str(score))}</h4><img src=\"{img_uri}\" alt=\"{html.escape(str(smiles))}\"/></div>")
                else:
                    parts.append(f"<div class=meta><h4>{html.escape(str(smiles))} — score:{html.escape(str(score))}</h4></div>")
            parts.append('</div>')
        else:
            parts.append("<p><em>No best_molecules</em></p>")

        parts.append("<h3>Experimental results</h3>" + render_experimental_results(er))

    parts.append("</div></div>")
    return "\n".join(parts)


def main():
    p = argparse.ArgumentParser(description="Generate slides.html from *_final.json files")
    p.add_argument('folder', help='Folder to scan for *_final.json (default: current directory)')
    p.add_argument('--output', '-o', default='slides.html', help='Output HTML file path')
    p.add_argument('--pattern', default='*_final.json', help='Filename pattern to match')
    p.add_argument('--recursive', '-r', action='store_true', help='Recursively search subfolders')
    args = p.parse_args()

    folder = Path(args.folder)
    if not folder.exists():
        print(f"Error: folder {folder} does not exist")
        raise SystemExit(1)

    if args.recursive:
        files = sorted(folder.rglob(args.pattern))
    else:
        files = sorted(folder.glob(args.pattern))

    if not files:
        print(f"No files matching {args.pattern} in {folder}")

    slide_blocks = []
    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception as e:
            print(f"Skipping {f}: failed to parse JSON ({e})")
            continue
        slide_blocks.append(build_slide_block(str(f.name), data))

    body = "\n".join(slide_blocks)
    html_out = HTML_TEMPLATE.replace('__BODY__', body)

    outpath = Path(args.output)
    outpath.write_text(html_out)
    print(f"Wrote {outpath} with {len(slide_blocks)} slides from {len(files)} file(s)")


if __name__ == '__main__':
    main()
