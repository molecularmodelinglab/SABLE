"""
Plotting utilities for workflow results.
"""
import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import plotly.graph_objects as go
import pandas as pd
from plotly.subplots import make_subplots
from sklearn.manifold import TSNE
from rdkit import Chem
from rdkit.Chem import QED, rdMolDescriptors, Descriptors, rdFingerprintGenerator, DataStructs, rdDepictor
from rdkit.Chem.Draw import rdMolDraw2D

_FPGEN = rdFingerprintGenerator.GetMorganGenerator(radius=2, fpSize=2048)
PROPERTY_UNITS = {
  # Fill in as needed
  "binding_affinity": "-logIC50",
}


HTML_POST_SCRIPT = r"""
(function() {
  var gd = document.getElementsByClassName('plotly-graph-div')[0];
  if (!gd) return;

  // Popup
  var box = document.createElement('div');
  box.style.position = 'fixed';
  box.style.right = '16px';
  box.style.top = '16px';
  box.style.width = '300px';
  box.style.padding = '10px';
  box.style.background = 'rgba(255,255,255,0.96)';
  box.style.border = '1px solid rgba(0,0,0,0.15)';
  box.style.borderRadius = '10px';
  box.style.boxShadow = '0 2px 10px rgba(0,0,0,0.10)';
  box.style.zIndex = 9999;
  box.style.display = 'none';

  var header = document.createElement('div');
  header.style.display = 'flex';
  header.style.justifyContent = 'space-between';
  header.style.alignItems = 'center';

  var title = document.createElement('div');
  title.style.fontFamily = 'sans-serif';
  title.style.fontSize = '12px';
  title.style.fontWeight = '600';
  title.innerText = 'Selected molecule';
  header.appendChild(title);

  var closeBtn = document.createElement('button');
  closeBtn.innerText = '×';
  closeBtn.style.fontSize = '18px';
  closeBtn.style.lineHeight = '18px';
  closeBtn.style.border = 'none';
  closeBtn.style.background = 'transparent';
  closeBtn.style.cursor = 'pointer';
  closeBtn.onclick = function() { box.style.display = 'none'; };
  header.appendChild(closeBtn);

  box.appendChild(header);

  var meta = document.createElement('div');
  meta.style.fontFamily = 'sans-serif';
  meta.style.fontSize = '12px';
  meta.style.margin = '8px 0';
  box.appendChild(meta);

  var propsDiv = document.createElement('div');
  propsDiv.style.fontFamily = 'sans-serif';
  propsDiv.style.fontSize = '12px';
  propsDiv.style.margin = '8px 0';
  box.appendChild(propsDiv);

  var svgBox = document.createElement('div');
  svgBox.style.width = '280px';
  svgBox.style.height = '280px';
  svgBox.style.border = '1px solid rgba(0,0,0,0.06)';
  svgBox.style.borderRadius = '8px';
  svgBox.style.background = 'white';
  svgBox.style.display = 'none';
  svgBox.style.overflow = 'hidden';
  box.appendChild(svgBox);

  var msg = document.createElement('div');
  msg.style.fontFamily = 'sans-serif';
  msg.style.fontSize = '12px';
  msg.style.marginTop = '8px';
  msg.style.color = '#444';
  box.appendChild(msg);

  document.body.appendChild(box);

  function esc(s) {
    return String(s).replace(/[&<>"']/g, function(m) {
      return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]);
    });
  }

  function renderProps(propsObj) {
    if (!propsObj || typeof propsObj !== 'object') return '';
    var keys = Object.keys(propsObj);
    if (!keys.length) return '';
    var rows = keys.map(function(k) {
      var v = propsObj[k];
      function fmtNum(x, nd) {
        if (x === null || x === undefined) return 'NA';
        var n = Number(x);
        if (!isFinite(n)) return String(x);
        return n.toFixed(nd);
      }
      var vv = fmtNum(v, 2);
      return "<tr><td style='padding:2px 6px; color:#333;'>" + esc(k) +
             "</td><td style='padding:2px 6px; text-align:right;'>" + esc(vv) + "</td></tr>";
    }).join("");
    return "<table style='border-collapse:collapse; width:100%;'>" + rows + "</table>";
  }

  gd.on('plotly_click', function(e) {
    try {
      var pt = e.points[0];
      var cd = pt.customdata;

      // Expect customdata to be [[payload]] or [payload] depending on how you passed it.
      var payload = null;
      if (Array.isArray(cd) && cd.length > 0 && typeof cd[0] === 'object' && cd[0] !== null) {
        payload = cd[0];                 // customdata=[{...}]
      } else if (Array.isArray(cd) && cd.length > 0 && Array.isArray(cd[0]) && cd[0].length > 0) {
        payload = cd[0][0];              // customdata=[[{...}]]
      } else if (cd && typeof cd === 'object') {
        payload = cd;
      }

      if (!payload) return;

      var mid = payload.id || payload.molecule_id || '';
      var it  = payload.iter !== undefined && payload.iter !== null ? payload.iter : '';
      var smi = payload.smiles || '';
      var imgUri = payload.img || payload.image_svg || '';

      meta.innerHTML =
        "id: <b>" + esc(mid) + "</b><br>" +
        (it !== '' ? ("iter: <b>" + esc(it) + "</b><br>") : "") +
        "smiles: <span style='word-break:break-all;'>" + esc(smi) + "</span>";

      propsDiv.innerHTML = renderProps(payload.props);

      if (imgUri && String(imgUri).trim().startsWith('<svg')) {
        // Render SVG
        svgBox.innerHTML = imgUri;
      
        // Make SVG fill the box (handles RDKit width/height attributes)
        var svgEl = svgBox.querySelector('svg');
        if (svgEl) {
          svgEl.setAttribute('width', '100%');
          svgEl.setAttribute('height', '100%');
          svgEl.style.width = '100%';
          svgEl.style.height = '100%';
        }
      
        svgBox.style.display = 'block';
        msg.innerText = '';
      } else {
        svgBox.style.display = 'none';
        msg.innerText = 'No image available for this point.';
      }

      box.style.display = 'block';
    } catch(err) {}
  });
})();
"""


def _norm_key(k: str) -> str:
    return (
        str(k)
        .strip()
        .replace(" ", "_")
        .replace("-", "_")
        .replace("__", "_")
        .lower()
    )


def load_workflow_json(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _fp_from_smiles(smi: str) -> DataStructs.ExplicitBitVect:
    mol = Chem.MolFromSmiles(smi)
    return _FPGEN.GetFingerprint(mol)


def _morgan_fp_matrix(smiles_list: list[str]) -> np.ndarray:
    mols = [Chem.MolFromSmiles(smi) for smi in smiles_list]
    fps = [_FPGEN.GetFingerprintAsNumPy(m) for m in mols]
    return np.array(fps)


def smiles_to_svg(smiles: str, size: int = 250) -> str | None:
    mol = Chem.MolFromSmiles(str(smiles))
    if mol is None:
        return None
    rdDepictor.Compute2DCoords(mol)

    drawer = rdMolDraw2D.MolDraw2DSVG(size, size)
    drawer.DrawMolecule(mol)
    drawer.FinishDrawing()
    svg = drawer.GetDrawingText()

    # Strip XML header if present
    svg = svg.replace("<?xml version='1.0' encoding='iso-8859-1'?>", "").strip()
    return svg


def make_hover_string(mol_id, it, smiles, props_dict, directions=None) -> str:
    parts = [f"id={mol_id}"]
    if it is not None:
        parts.append(f"iter={it}")
    parts.append(str(smiles))

    if props_dict:
        parts.append("<br><b>optimized:</b>")
        for k, v in props_dict.items():
            mode = (directions or {}).get(k, "?")
            vv = "NA" if v is None or (isinstance(v, float) and np.isnan(v)) else v
            parts.append(f"{k} ({mode}): {vv:.4f}" if isinstance(vv, float) else f"{k} ({mode}): {vv}")
    return "<br>".join(parts)


def rdkit_descriptors(smiles: str) -> Dict[str, float]:
    """
    Minimal, standard descriptor set. Add/remove here as needed.
    Returns NaN for invalid SMILES.
    """
    m = Chem.MolFromSmiles(smiles)
    if m is None:
        return {
            "rdkit_ok": 0,
            "logp": float("nan"),
            "tpsa": float("nan"),
            "qed": float("nan"),
            "mol_wt": float("nan"),
            "hbd": float("nan"),
            "hba": float("nan"),
            "rot_bonds": float("nan"),
            "rings": float("nan"),
        }

    return {
        "rdkit_ok": 1,
        "logp": float(rdMolDescriptors.CalcCrippenDescriptors(m)[0]),
        "tpsa": float(rdMolDescriptors.CalcTPSA(m)),
        "qed": float(QED.qed(m)),
        "mol_wt": float(Descriptors.MolWt(m)),
        "hbd": float(rdMolDescriptors.CalcNumHBD(m)),
        "hba": float(rdMolDescriptors.CalcNumHBA(m)),
        "rot_bonds": float(rdMolDescriptors.CalcNumRotatableBonds(m)),
        "rings": float(rdMolDescriptors.CalcNumRings(m)),
    }


def get_opt_directions(raw: dict) -> dict[str, str]:
    """
    Returns {prop_name_normalized: 'min'|'max'} from raw['parsed_arguments']['target_properties'].
    Assumes rawp['parsed_arguments']['target_properties'] entries contain fields: 'property_name' 
    and 'optimization_mode' (MIN/MAX).
    """
    d = {}
    for t in raw["parsed_arguments"]["target_properties"]:
        name = _norm_key(t["property_name"])
        mode = str(t["optimization_mode"]).strip().lower()  # "min" or "max" (or "MIN"/"MAX")
        d[name] = "min" if "min" in mode else "max"
    return d


def direction_label(prop: str, directions: dict[str, str]) -> str:
    m = directions.get(_norm_key(prop), None)
    return f"{prop} (opt: {m})" if m else f"{prop} (opt: ?)"


def label_with_unit(prop: str) -> str:
    u = PROPERTY_UNITS.get(_norm_key(prop))
    return f"{prop} ({u})" if u else prop


def build_search_space_df(raw: Dict[str, Any]) -> pd.DataFrame:
    space: Dict[str, str] = raw["search_space"]
    df = pd.DataFrame({"molecule_id": list(space.keys()), "smiles": list(space.values())})
    df["molecule_id"] = df["molecule_id"].astype("string")
    df["smiles"] = df["smiles"].astype("string")

    # RDKit descriptors for the entire search space
    desc_rows = [rdkit_descriptors(smi) for smi in df["smiles"].tolist()]
    desc_df = pd.DataFrame(desc_rows)
    return pd.concat([df, desc_df], axis=1)


def build_observations_df(raw: Dict[str, Any]) -> pd.DataFrame:
    rows: List[Dict[str, Any]] = []
    for r in raw["experimental_results"]:
        meta = r["metadata"]
        obj_props = r["properties"]

        obj = {_norm_key(k): v for k, v in obj_props.items()}
        obj_prefixed = {f"obj_{k}": v for k, v in obj.items()}

        row = {
            "molecule_id": r["molecule_id"],
            "smiles": r["smiles"],
            "iteration": int(r["iteration"]),
            "tool": meta["characterization_tool"],
            "timestamp": r["timestamp"],
            "source": r["source"],
            **obj,
            **obj_prefixed,
        }

        # Compute RDKit descriptors (always, at this stage)
        row.update(rdkit_descriptors(r["smiles"]))

        rows.append(row)

    df = pd.DataFrame(rows)
    df["molecule_id"] = df["molecule_id"].astype("string")
    df["smiles"] = df["smiles"].astype("string")
    df["tool"] = df["tool"].astype("string")
    df["source"] = df["source"].astype("string")
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df["iteration"] = pd.to_numeric(df["iteration"], errors="raise").astype(int)
    return df


def attach_tested_flags(space_df: pd.DataFrame, obs_df: pd.DataFrame) -> pd.DataFrame:
    agg = (
        obs_df.groupby("molecule_id", as_index=False)
        .agg(
            first_seen_iteration=("iteration", "min"),
            n_times_tested=("iteration", "size"),
        )
    )
    out = space_df.merge(agg, on="molecule_id", how="left")
    out["tested"] = out["first_seen_iteration"].notna()
    out["first_seen_iteration"] = out["first_seen_iteration"].astype("Int64")
    out["n_times_tested"] = out["n_times_tested"].fillna(0).astype(int)
    return out


def compute_objective_progress(
    obs_df: pd.DataFrame,
    space_df: pd.DataFrame | None,
    prop: str,
    direction: str,
) -> pd.DataFrame:
    """
    Returns per-iteration table with:
      - iter_best: best in that iteration (min or max)
      - best_so_far: cumulative best
      - abs_improvement: per-iteration improvement in the *optimization direction* (>=0 when improving)
      - regret_norm: normalized regret in [0,1] using global optimum (requires space_df)
    """
    df = obs_df.loc[obs_df[prop].notna(), ["iteration", prop]].copy()
    df["iteration"] = df["iteration"].astype(int)

    g = df.groupby("iteration")[prop]
    if direction == "min":
        iter_best = g.min()
        best_so_far = iter_best.cummin()
        abs_impr = best_so_far.shift(1) - best_so_far  # positive when decreasing
    else:
        iter_best = g.max()
        best_so_far = iter_best.cummax()
        abs_impr = best_so_far - best_so_far.shift(1)  # positive when increasing

    out = pd.DataFrame({
        "iteration": iter_best.index.astype(int),
        "iter_best": iter_best.values,
        "best_so_far": best_so_far.values,
        "abs_improvement": abs_impr.fillna(0.0).values,
    }).sort_values("iteration")

    # normalized regret needs the true optimum from the whole search space
    out["regret_norm"] = np.nan
    if space_df is not None and prop in space_df.columns:
        s = pd.to_numeric(space_df[prop], errors="coerce")
        opt = float(s.min(skipna=True)) if direction == "min" else float(s.max(skipna=True))

        baseline = float(out["best_so_far"].iloc[0])
        denom = (baseline - opt) if direction == "min" else (opt - baseline)

        if denom == 0 or np.isnan(denom):
            out["regret_norm"] = 0.0
        else:
            if direction == "min":
                out["regret_norm"] = (out["best_so_far"] - opt) / denom
            else:
                out["regret_norm"] = (opt - out["best_so_far"]) / denom

            out["regret_norm"] = out["regret_norm"].clip(lower=0.0, upper=1.0)

    return out


def compute_diversity_timeseries(obs_df: pd.DataFrame) -> pd.DataFrame:
    """
    Per iteration, for molecules first tested in that iteration, compute similarity to the PREVIOUS tested set:
      - mean_max_sim: mean of (max similarity to prev set) over new molecules
      - max_max_sim:  max of (max similarity to prev set) over new molecules
    """
    df = obs_df.loc[:, ["iteration", "molecule_id", "smiles"]].copy()
    df["iteration"] = df["iteration"].astype(int)

    # ensure we only consider the first time a molecule appears
    first = df.sort_values("iteration").drop_duplicates("molecule_id", keep="first")

    prev_fps = []
    rows = []

    for it in sorted(first["iteration"].unique()):
        batch = first[first["iteration"] == it]
        max_sims = []

        for smi in batch["smiles"].astype(str).tolist():
            fp = _fp_from_smiles(smi)
            if fp is None:
                continue

            if prev_fps:
                sims = DataStructs.BulkTanimotoSimilarity(fp, prev_fps)
                max_sims.append(float(max(sims)))
            else:
                max_sims.append(np.nan)

        valid = np.asarray(max_sims, dtype=float)
        valid = valid[np.isfinite(valid)]

        mean_max_sim = float(valid.mean()) if valid.size else np.nan
        max_max_sim  = float(valid.max())  if valid.size else np.nan

        rows.append({
            "iteration": it,
            "mean_max_sim": mean_max_sim,
            "max_max_sim": max_max_sim,
            "n_new": int(len(batch)),
        })

        # add this iteration to the previous set
        for smi in batch["smiles"].astype(str).tolist():
            fp = _fp_from_smiles(smi)
            if fp is not None:
                prev_fps.append(fp)

    return pd.DataFrame(rows)


def pareto_mask(values: np.ndarray) -> np.ndarray:
    """
    values: (N, K) in MAXIMIZATION space (higher is better for all K).
    returns boolean mask of nondominated points.
    O(N^2) but fine for typical per-iteration sizes.
    """
    n = values.shape[0]
    mask = np.ones(n, dtype=bool)
    for i in range(n):
        if not mask[i]:
            continue
        vi = values[i]
        # dominated if exists j: vj >= vi all dims and > in at least one
        dom = np.all(values >= vi, axis=1) & np.any(values > vi, axis=1)
        if np.any(dom):
            mask[i] = False
    return mask


### Plotting ###

def plot_prop_vs_iteration(obs_df: pd.DataFrame, prop: str, kind: str = "box",
                           show_points: bool = True,
                           space_df: pd.DataFrame | None = None,
                           directions: dict[str, str] | None = None) -> go.Figure:
    """
    kind: "box" or "violin"
    Fixes the 'offset' issue by using graph_objects with jitter=0 and pointpos=0.
    """
    cols = ["molecule_id", "iteration", "smiles", prop, "image_svg"]
    df = obs_df.loc[obs_df[prop].notna(), cols].copy()
    df["iteration"] = df["iteration"].astype(int)

    df["_payload"] = df.apply(lambda r: {
        "id": r["molecule_id"],
        "iter": int(r["iteration"]),
        "smiles": r["smiles"],
        "img": r["image_svg"],
        "props": {prop: float(r[prop]) if pd.notna(r[prop]) else None},
    }, axis=1)
    df["_payload"] = df["_payload"].apply(
        lambda p: {**p, "hover": make_hover_string(p["id"], p["iter"], p["smiles"], p["props"], directions)}
    )

    fig = go.Figure()

    if kind == "box":
        fig.add_trace(go.Box(
            x=df["iteration"],
            y=df[prop],
            name=prop,
            boxpoints="all" if show_points else False,
            jitter=0.25,          # <-- no x-jitter
            pointpos=0,        # <-- points centered on category
            customdata=df[["_payload"]].to_numpy(),
            hovertemplate="%{customdata[0].hover}<extra></extra>",
        ))
    elif kind == "violin":
        fig.add_trace(go.Violin(
            x=df["iteration"],
            y=df[prop],
            name=prop,
            points="all" if show_points else False,
            jitter=0.25,          # <-- no x-jitter
            pointpos=0,        # <-- points centered
            customdata=df[["_payload"]].to_numpy(),
            hovertemplate="%{customdata[0].hover}<extra></extra>",
        ))
    else:
        raise ValueError("kind must be 'box' or 'violin'")

    # "true" min/max from the whole search space (only if provided)
    if space_df is not None and prop in space_df.columns:
        gmin = float(pd.to_numeric(space_df[prop], errors="coerce").min(skipna=True))
        gmax = float(pd.to_numeric(space_df[prop], errors="coerce").max(skipna=True))
        fig.add_hline(y=gmin, line_dash="dash", annotation_text="global min", annotation_position="bottom left")
        fig.add_hline(y=gmax, line_dash="dash", annotation_text="global max", annotation_position="top left")

    fig.update_layout(
        title=f"{direction_label(prop, directions or {})} vs iteration ({kind})",
        xaxis_title="Iteration",
        yaxis_title=label_with_unit(prop),
        xaxis=dict(type="category"),
    )
    return fig


def plot_bestN_so_far_box(
    obs_df: pd.DataFrame,
    prop: str,
    *,
    directions: dict[str, str] | None = None,
    batch_size: int | None = None,
    min_n: int = 0,
    space_df: pd.DataFrame | None = None,
    show_points: bool = True,
) -> go.Figure:
    """
    For each iteration t, build a boxplot of the best-N molecules *so far* (using all data with iter<=t).
    - N defaults to batch_size (caller should pass raw['parsed_arguments']['batch_size'])
    - effective_N = max(batch_size, min_n)
    - direction-aware: MAX => top-N largest; MIN => top-N smallest
    """

    directions = directions or {}
    mode = (directions.get(_norm_key(prop), "max") or "max").lower()

    base_cols = ["molecule_id", "iteration", "smiles", prop, "image_svg"]
    df0 = obs_df.loc[obs_df[prop].notna(), base_cols].copy()
    df0["iteration"] = df0["iteration"].astype(int)

    if df0.empty:
        # empty figure, consistent styling
        fig = go.Figure()
        fig.update_layout(
            title=f"{direction_label(prop, directions)} | best-N-so-far (empty)",
            xaxis_title="Iteration",
            yaxis_title=label_with_unit(prop),
            xaxis=dict(type="category"),
        )
        return fig

    # compute N
    bs = int(batch_size) if batch_size is not None else 0
    N = max(bs, int(min_n))

    iters = sorted(df0["iteration"].unique())
    rows = []

    for t in iters:
        sub = df0[df0["iteration"] <= t]
        if sub.empty:
            continue

        # best row per molecule -- avoids duplicates if molecule is re-tested
        if mode == "min":
            idx = sub.groupby("molecule_id")[prop].idxmin()
            best_rows = sub.loc[idx].copy()
            best_rows = best_rows.nsmallest(N, prop) if N > 0 else best_rows
        else:
            idx = sub.groupby("molecule_id")[prop].idxmax()
            best_rows = sub.loc[idx].copy()
            best_rows = best_rows.nlargest(N, prop) if N > 0 else best_rows

        best_rows["iter_box"] = t  # x-axis category -- current iteration
        rows.append(best_rows)

    boxdf = pd.concat(rows, ignore_index=True)

    # payload + hover (keep your click script working)
    boxdf["_payload"] = boxdf.apply(lambda r: {
        "id": r["molecule_id"],
        "iter": int(r["iter_box"]),  # show the iteration whose box you clicked
        "smiles": r["smiles"],
        "img": r["image_svg"],
        "props": {prop: float(r[prop]) if pd.notna(r[prop]) else None},
    }, axis=1)

    def _add_hover(p, best_eval_iter: int) -> dict:
        h = make_hover_string(p["id"], p["iter"], p["smiles"], p["props"], directions)
        # show which iteration produced the current best value (often <= iter_box)
        h = h + f"<br>best_eval_iter={best_eval_iter}"
        return {**p, "hover": h}

    boxdf["_payload"] = [
        _add_hover(p, int(best_it))
        for p, best_it in zip(boxdf["_payload"].tolist(), boxdf["iteration"].tolist())
    ]

    fig = go.Figure()

    fig.add_trace(go.Box(
        x=boxdf["iter_box"].astype(str),
        y=boxdf[prop],
        name=f"best-{N}-so-far" if N > 0 else "best-so-far",
        boxpoints="all" if show_points else False,
        jitter=0.25,
        pointpos=0,
        customdata=boxdf[["_payload"]].to_numpy(),
        hovertemplate="%{customdata[0].hover}<extra></extra>",
    ))

    # global min/max reference (same convention as your other plots)
    if space_df is not None and prop in space_df.columns:
        gmin = float(pd.to_numeric(space_df[prop], errors="coerce").min(skipna=True))
        gmax = float(pd.to_numeric(space_df[prop], errors="coerce").max(skipna=True))
        fig.add_hline(y=gmin, line_dash="dash", annotation_text="global min", annotation_position="bottom left")
        fig.add_hline(y=gmax, line_dash="dash", annotation_text="global max", annotation_position="top left")

    fig.update_layout(
        title=f"{direction_label(prop, directions)} | best-{N}-so-far box vs iteration" if N > 0
              else f"{direction_label(prop, directions)} | best-so-far box vs iteration",
        xaxis_title="Iteration",
        yaxis_title=label_with_unit(prop),
        xaxis=dict(type="category"),
    )
    return fig


def plot_tsne_space(space_df: pd.DataFrame,
                    obs_df: pd.DataFrame,
                    opt_props: list[str],
                    directions: dict[str, str],
                    color_by: str = "first_seen_iteration",
                    perplexity: int = 30,
                    random_state: int = 0,
                    starting_smiles: list[str] | None = None) -> go.Figure:
    """
    TSNE over the WHOLE search space.
    - Untested molecules plotted gray.
    - Tested molecules colored by first_seen_iteration (from space_df).
    """
    def _payload_row(r) -> dict:
        props_dict = {p: (r[p] if (p in r.index and pd.notna(r[p])) else None) for p in opt_props_all}
        it = int(r["first_seen_iteration"]) if bool(r.get("tested", False)) and pd.notna(r.get("first_seen_iteration")) else None

        payload = {
            "id": r["molecule_id"],
            "iter": it,
            "smiles": str(r["smiles"]),
            "img": r.get("image_svg", None),
            "props": props_dict,
        }
        payload["hover"] = make_hover_string(payload["id"], payload["iter"], payload["smiles"], payload["props"], directions)
        return payload
    
    opt_props_all = opt_props
    opt_props_present = [p for p in opt_props_all if p in space_df.columns]
    cols = ["molecule_id", "smiles", "tested", "first_seen_iteration"] + opt_props_present + ["image_svg"]
    df = space_df.loc[:, cols].copy()

    # fill objective values from obs_df for props not present in space_df
    df["molecule_id"] = df["molecule_id"].astype(str)

    for p in opt_props_all:
        if p in df.columns:
            continue
        if p not in obs_df.columns:
            continue

        tmp = obs_df.loc[obs_df[p].notna(), ["molecule_id", p]].copy()
        if tmp.empty:
            df[p] = np.nan
            continue

        tmp["molecule_id"] = tmp["molecule_id"].astype(str)
        mode = (directions.get(p, "max") or "max").lower()

        if mode == "min":
            m = tmp.groupby("molecule_id")[p].min()
        else:
            m = tmp.groupby("molecule_id")[p].max()

        # tested molecules get values; untested remain NaN
        df[p] = df["molecule_id"].map(m)

    # embed all molecules
    X = _morgan_fp_matrix(df["smiles"].astype(str).tolist())
    emb = TSNE(
        n_components=2,
        perplexity=perplexity,
        init="pca",
        learning_rate="auto",
        random_state=random_state,
    ).fit_transform(X)
    df["x"] = emb[:, 0]
    df["y"] = emb[:, 1]

    tested = df["tested"] == True
    untested = ~tested
    df["_payload"] = df.apply(_payload_row, axis=1)

    fig = go.Figure()

    # untested gray
    fig.add_trace(go.Scatter(
        x=df.loc[untested, "x"],
        y=df.loc[untested, "y"],
        mode="markers",
        name="unexplored",
        marker=dict(color="lightgray", size=5, opacity=0.5),
        customdata=df.loc[untested, ["_payload"]].to_numpy(),
        hovertemplate="%{customdata[0].hover}<extra></extra>",
    ))

    # tested colored by iteration
    c = df.loc[tested, color_by] if color_by in df.columns else df.loc[tested, "first_seen_iteration"]
    fig.add_trace(go.Scatter(
        x=df.loc[tested, "x"],
        y=df.loc[tested, "y"],
        mode="markers",
        name="tested",
        marker=dict(
            size=7,
            color=c,
            colorscale="Viridis",
            showscale=True,
            opacity=0.9,
            colorbar=dict(title=color_by, x=1.02, y=0.5, len=0.85),
        ),
        customdata=df.loc[tested, ["_payload"]].to_numpy(),
        hovertemplate="%{customdata[0].hover}<extra></extra>",
    ))

    # starting molecules
    if starting_smiles:
        start_mask = df["smiles"].isin(starting_smiles)
        if start_mask.any():
            fig.add_trace(go.Scatter(
                x=df.loc[start_mask, "x"],
                y=df.loc[start_mask, "y"],
                mode="markers",
                name="starting",
                marker=dict(symbol="star", size=16, color="red", line=dict(color="black", width=1)),
                customdata=df.loc[start_mask, ["_payload"]].to_numpy(),
                hovertemplate="%{customdata[0].hover}<extra></extra>",
            ))

    fig.update_layout(
        width=1200,
        height=1000,
        title="Chemical space (t-SNE over search space)",
        xaxis_title="t-SNE 1",
        yaxis_title="t-SNE 2",
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(255,255,255,0.6)"),
        margin=dict(l=40, r=120, t=60, b=40),
    )
    return fig


def plot_diversity(div_df: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=div_df["iteration"], y=div_df["mean_max_sim"],
        mode="lines+markers", name="mean(max sim to prev)"
    ))
    fig.add_trace(go.Scatter(
        x=div_df["iteration"], y=div_df["max_max_sim"],
        mode="lines+markers", name="max(max sim to prev)"
    ))
    fig.update_layout(
        title="Diversity vs iteration (Tanimoto similarity to previously tested set)",
        xaxis_title="Iteration",
        yaxis_title="Tanimoto similarity",
        height=450,
    )
    return fig


def plot_minmax_vs_iteration(obs_df: pd.DataFrame, prop: str, 
                             space_df: pd.DataFrame | None = None,
                             directions: dict[str, str] | None = None) -> go.Figure:
    df = obs_df.loc[obs_df[prop].notna(), ["iteration", prop]].copy()
    df["iteration"] = df["iteration"].astype(int)

    agg = df.groupby("iteration")[prop].agg(["min", "max"]).reset_index()

    fig = go.Figure()
    fig.add_trace(go.Scatter(x=agg["iteration"], y=agg["max"], mode="lines+markers", name=f"{prop} max/iter"))
    fig.add_trace(go.Scatter(x=agg["iteration"], y=agg["min"], mode="lines+markers", name=f"{prop} min/iter"))

    if space_df is not None and prop in space_df.columns:
        gmin = float(pd.to_numeric(space_df[prop], errors="coerce").min(skipna=True))
        gmax = float(pd.to_numeric(space_df[prop], errors="coerce").max(skipna=True))
        fig.add_hline(y=gmin, line_dash="dash", annotation_text="global min", annotation_position="bottom left")
        fig.add_hline(y=gmax, line_dash="dash", annotation_text="global max", annotation_position="top left")

    fig.update_layout(
        title=f"{direction_label(prop, directions or {})} min/max vs iteration", 
        xaxis_title="Iteration", 
        yaxis_title=label_with_unit(prop)
    )
    return fig


def plot_prop_pair(obs_df: pd.DataFrame, prop_x: str, prop_y: str,
                   directions: dict[str, str] | None = None) -> go.Figure:
    cols = ["molecule_id", "iteration", "smiles", prop_x, prop_y, "image_svg"]
    df = obs_df.loc[obs_df[prop_x].notna() & obs_df[prop_y].notna(), cols].copy()
    df["iteration"] = df["iteration"].astype(int)
    
    df["_payload"] = df.apply(lambda r: {
        "id": r["molecule_id"],
        "iter": int(r["iteration"]),
        "smiles": r["smiles"],
        "img": r["image_svg"],
        "props": {prop_x: float(r[prop_x]) if pd.notna(r[prop_x]) else None,
                  prop_y: float(r[prop_y]) if pd.notna(r[prop_y]) else None},
    }, axis=1)
    df["_payload"] = df["_payload"].apply(
        lambda p: {**p, "hover": make_hover_string(p["id"], p["iter"], p["smiles"], p["props"], directions)}
    )

    fig = go.Figure(go.Scatter(
        x=df[prop_x],
        y=df[prop_y],
        mode="markers",
        marker=dict(
            size=7,
            color=df["iteration"],
            colorscale="Viridis",
            showscale=True,
            colorbar=dict(title="iteration"),
            opacity=0.9,
        ),
        customdata=df[["_payload"]].to_numpy(),
        hovertemplate="%{customdata[0].hover}<extra></extra>",
    ))
    fig.update_layout(
        width=800,
        height=600,
        title=f"{direction_label(prop_x, directions or {})} vs {direction_label(prop_y, directions or {})}",
        xaxis_title=label_with_unit(prop_x), 
        yaxis_title=label_with_unit(prop_y),
        legend=dict(x=1.18, y=1.0, xanchor="left", yanchor="top",
                bgcolor="rgba(255,255,255,0.6)"),
        margin=dict(l=40, r=240, t=60, b=40),
    )
    return fig


def make_pair_plots(obs_df: pd.DataFrame, props: list[str],
                    directions: dict[str, str] | None = None) -> dict[tuple[str, str], go.Figure]:
    figs = {}
    for i in range(len(props)):
        for j in range(i + 1, len(props)):
            a, b = props[i], props[j]
            figs[(a, b)] = plot_prop_pair(obs_df, a, b, directions=directions)
    return figs


def plot_prop_triplet_3d(obs_df: pd.DataFrame, a: str, b: str, c: str,
                         directions: dict[str, str] | None = None) -> go.Figure:
    cols = ["molecule_id", "iteration", "smiles", a, b, c, "image_svg"]
    df = obs_df.loc[obs_df[a].notna() & obs_df[b].notna() & obs_df[c].notna(), cols].copy()
    df["iteration"] = df["iteration"].astype(int)

    df["_payload"] = df.apply(lambda r: {
        "id": r["molecule_id"],
        "iter": int(r["iteration"]),
        "smiles": r["smiles"],
        "img": r["image_svg"],
        "props": {a: float(r[a]) if pd.notna(r[a]) else None,
                  b: float(r[b]) if pd.notna(r[b]) else None,
                  c: float(r[c]) if pd.notna(r[c]) else None},
    }, axis=1)
    df["_payload"] = df["_payload"].apply(
        lambda p: {**p, "hover": make_hover_string(p["id"], p["iter"], p["smiles"], p["props"], directions)}
    )

    fig = go.Figure(go.Scatter3d(
        x=df[a], y=df[b], z=df[c],
        mode="markers",
        marker=dict(
            size=4,
            color=df["iteration"],
            colorscale="Viridis",
            showscale=True,
            colorbar=dict(title="iteration"),
            opacity=0.9,
        ),
        customdata=df[["_payload"]].to_numpy(),
        hovertemplate="%{customdata[0].hover}<extra></extra>",
    ))
    fig.update_layout(
        title=f"{direction_label(a, directions or {})} vs {direction_label(b, directions or {})} vs {direction_label(c, directions or {})} (3D)",
        scene=dict(xaxis_title=label_with_unit(a), yaxis_title=label_with_unit(b), zaxis_title=label_with_unit(c)),
        legend=dict(x=1.18, y=1.0, xanchor="left", yanchor="top", bgcolor="rgba(255,255,255,0.6)"),
        margin=dict(l=40, r=240, t=60, b=40),
    )
    return fig


def make_triplet_plots(obs_df: pd.DataFrame, props: list[str],
                       directions: dict[str, str] | None = None) -> dict[tuple[str, str, str], go.Figure]:
    figs = {}
    n = len(props)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                a, b, c = props[i], props[j], props[k]
                figs[(a, b, c)] = plot_prop_triplet_3d(obs_df, a, b, c, directions=directions)
    return figs


def plot_radial_iterations(obs_df: pd.DataFrame,
                           space_df: pd.DataFrame,
                           props: list[str],
                           directions: dict[str, str],
                           starting_smiles: list[str] | None = None) -> go.Figure:
    """
    Rings = iterations, wedges = properties.
    Color = normalized value per (iteration, property).

    Normalization uses global min/max from space_df for each prop.
    """
    df = obs_df.copy()
    df["iteration"] = df["iteration"].astype(int)

    g = df.groupby("iteration")

    # per-property aggregation based on direction:
    # max for "max", min for "min"
    it = pd.DataFrame(index=sorted(df["iteration"].unique()))
    for p in props:
        mode = (directions.get(_norm_key(p), "max")).lower()
        if mode == "min":
            it[p] = g[p].min()
        else:
            it[p] = g[p].max()

    it = it.sort_index()
    iterations = it.index.tolist()

    # global min/max for normalization
    gmin = {p: float(pd.to_numeric(space_df[p], errors="coerce").min(skipna=True)) for p in props}
    gmax = {p: float(pd.to_numeric(space_df[p], errors="coerce").max(skipna=True)) for p in props}

    # handle starting molecules
    start_vals = None
    if starting_smiles:
        start_vals = {}
        for p in props:
            smi_to_v = starting_value_map(p, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
            if not smi_to_v:
                start_vals[p] = np.nan
                continue
            # represent starting "state" as direction-consistent best across starting molecules
            mode = (directions.get(_norm_key(p), "max") or "max").lower()
            vals = list(smi_to_v.values())
            start_vals[p] = float(min(vals)) if mode == "min" else float(max(vals))

    def norm(p: str, v: float) -> float:
        lo, hi = gmin[p], gmax[p]
        if hi == lo or pd.isna(v):
            return float("nan")
        x = (v - lo) / (hi - lo)

        # flip for minimization so "better" -> larger normalized value
        mode = (directions.get(_norm_key(p), "max")).lower()
        if mode == "min":
            x = 1.0 - x

        return x

    n_props = len(props)
    angles = np.linspace(0, 360, n_props, endpoint=False)
    width = 360.0 / n_props

    theta = []
    r = []
    base = []
    color = []
    hovertext = []

    ring_offset = 0
    # starting ring first (base=0)
    if start_vals is not None:
        for j, p in enumerate(props):
            v = float(start_vals[p]) if pd.notna(start_vals[p]) else np.nan
            nv = norm(p, v)
            theta.append(angles[j])
            r.append(1.0)
            base.append(0.0)
            color.append(nv)
            hovertext.append(f"start<br>prop={p}<br>value={v}")
        ring_offset = 1

    # then iteration rings start at base=1
    for ring_idx, it_num in enumerate(iterations):
        for j, p in enumerate(props):
            v = float(it.loc[it_num, p])
            nv = norm(p, v)
            theta.append(angles[j])
            r.append(1.0)
            base.append(float(ring_idx + ring_offset))
            color.append(nv)
            hovertext.append(f"iter={it_num} iteration<br>prop={p}<br>value={v}")

    fig = go.Figure(go.Barpolar(
        theta=theta,
        r=r,
        base=base,
        width=width,
        marker=dict(
            color=color,
            colorscale="Viridis",
            cmin=0.0,
            cmax=1.0,
            showscale=True,
            colorbar=dict(title="normalized"),
        ),
        hovertext=hovertext,
        hoverinfo="text",
        opacity=1.0,
    ))

    # label rings with iteration numbers
    if start_vals is not None:
        tickvals = [0.5] + [i + 1.5 for i in range(len(iterations))]
        ticktext = ["start"] + [f"{it} iteration" for it in iterations]
    else:
        tickvals = [i + 0.5 for i in range(len(iterations))]
        ticktext = [f"it. {it}" for it in iterations]

    fig.update_layout(
        width=800,
        height=600,
        title=f"Radial iteration view | " + ", ".join([direction_label(p, directions or {}) for p in props]),
        polar=dict(
            angularaxis=dict(
                tickmode="array",
                tickvals=angles,
                ticktext=props,
                rotation=90,
                direction="clockwise",
            ),
            radialaxis=dict(
                tickmode="array",
                tickvals=tickvals,
                ticktext=ticktext,
                showline=False,
                ticks="",
            ),
        ),
    )
    return fig


def plot_objective_progress(progress_df: pd.DataFrame,
                            prop: str,
                            direction: str,
                            space_df: pd.DataFrame | None = None) -> go.Figure:
    """
    One figure with 3 rows:
      1) best-so-far (+ global optimum line if available)
      2) abs improvement per iteration
      3) normalized regret (if available)
    """
    fig = make_subplots(rows=3, cols=1, shared_xaxes=True, vertical_spacing=0.06,
                        subplot_titles=("Best-so-far", "Absolute improvement", "Normalized regret"))

    # row 1: best-so-far
    fig.add_trace(go.Scatter(
        x=progress_df["iteration"], y=progress_df["best_so_far"],
        mode="lines+markers", name="best_so_far"
    ), row=1, col=1)

    # global optimum reference
    if space_df is not None and prop in space_df.columns:
        s = pd.to_numeric(space_df[prop], errors="coerce")
        opt = float(s.min(skipna=True)) if direction == "min" else float(s.max(skipna=True))
        fig.add_hline(y=opt, line_dash="dash", annotation_text="global optimum",
                      row=1, col=1)

    # row 2: abs improvement
    fig.add_trace(go.Bar(
        x=progress_df["iteration"], y=progress_df["abs_improvement"],
        name="abs_improvement"
    ), row=2, col=1)

    # row 3: regret
    if progress_df["regret_norm"].notna().any():
        fig.add_trace(go.Scatter(
            x=progress_df["iteration"], y=progress_df["regret_norm"],
            mode="lines+markers", name="regret_norm"
        ), row=3, col=1)
        fig.update_yaxes(range=[0, 1], row=3, col=1)
    else:
        # keep the panel but indicate not available
        fig.add_annotation(
            text="regret unavailable (no global optimum)",
            xref="paper", yref="paper",
            x=0.5, y=0.12, showarrow=False
        )

    fig.update_layout(
        title=f"{direction_label(prop, {_norm_key(prop): direction})}",
        height=850,
        xaxis3_title="Iteration",
    )
    return fig


def plot_pareto_evolution_2d(obs_df: pd.DataFrame,
                             prop_x: str,
                             prop_y: str,
                             directions: dict[str, str],
                             overlay_all: bool = True) -> go.Figure:
    cols = ["molecule_id", "iteration", "smiles", prop_x, prop_y, "image_svg"]
    df = obs_df.loc[obs_df[prop_x].notna() & obs_df[prop_y].notna(), cols].copy()
    df["iteration"] = df["iteration"].astype(int)

    df["_payload"] = df.apply(lambda r: {
        "id": r["molecule_id"],
        "iter": int(r["iteration"]),
        "smiles": r["smiles"],
        "img": r["image_svg"],
        "props": {prop_x: float(r[prop_x]) if pd.notna(r[prop_x]) else None,
                  prop_y: float(r[prop_y]) if pd.notna(r[prop_y]) else None},
    }, axis=1)
    df["_payload"] = df["_payload"].apply(
        lambda p: {**p, "hover": make_hover_string(p["id"], p["iter"], p["smiles"], p["props"], directions)}
    )

    iters = sorted(df["iteration"].unique())

    # convert to maximization space for dominance checks
    sx = -1.0 if directions.get(_norm_key(prop_x), "max") == "min" else 1.0
    sy = -1.0 if directions.get(_norm_key(prop_y), "max") == "min" else 1.0
    df["_mx"] = sx * df[prop_x].astype(float)
    df["_my"] = sy * df[prop_y].astype(float)

    # traces:
    # 0: all points (fixed, optional)
    # 1: cumulative points up to iter (animated)
    # 2: pareto points (animated)
    fig = go.Figure()

    if overlay_all:
        fig.add_trace(go.Scatter(
            x=df[prop_x], y=df[prop_y],
            mode="markers",
            name="all points",
            marker=dict(size=6, color=df["iteration"], colorscale="Viridis", opacity=0.50, showscale=True,
                        colorbar=dict(title="iteration", x=1.02, y=0.5, len=0.85)),
            customdata=df[["_payload"]].to_numpy(),
            hovertemplate="%{customdata[0].hover}<extra></extra>",
        ))
    else:
        fig.add_trace(go.Scatter(x=[], y=[], mode="markers", name="all points", visible=False))

    # cumulative trace (animated)
    fig.add_trace(go.Scatter(
        x=[], y=[], mode="markers", name="up to iter",
        marker=dict(size=6, color="lightgray", opacity=0.50),
        visible=not overlay_all,
        hoverinfo="skip",
    ))

    # pareto trace (animated)
    fig.add_trace(go.Scatter(
        x=[], y=[], mode="markers+lines", name="pareto front",
        marker=dict(size=9, symbol="diamond", opacity=0.9),
        line=dict(width=2),
        hoverinfo="skip",
    ))

    frames = []
    for it in iters:
        dfi = df[df["iteration"] <= it].copy()
        V = dfi[["_mx", "_my"]].to_numpy()
        pm = pareto_mask(V)

        pareto = dfi.loc[pm].sort_values("_mx")  # sort for a nicer connecting line

        frames.append(go.Frame(
            name=str(it),
            data=[
                fig.data[0],  # unchanged
                go.Scatter(x=dfi[prop_x], y=dfi[prop_y]),          # trace 1 cumulative
                go.Scatter(x=pareto[prop_x], y=pareto[prop_y]),    # trace 2 pareto
            ]
        ))

    fig.frames = frames

    # init at first iteration
    if frames:
        fig.data[1].x = df[df["iteration"] <= iters[0]][prop_x]
        fig.data[1].y = df[df["iteration"] <= iters[0]][prop_y]
        dfi0 = df[df["iteration"] <= iters[0]].copy()
        pm0 = pareto_mask(dfi0[["_mx", "_my"]].to_numpy())
        pareto0 = dfi0.loc[pm0].sort_values("_mx")
        fig.data[2].x = pareto0[prop_x]
        fig.data[2].y = pareto0[prop_y]

    # toggle overlay vs cumulative
    fig.update_layout(
        title=f"Pareto evolution: {direction_label(prop_x, directions)} vs {direction_label(prop_y, directions)}",
        xaxis_title=label_with_unit(prop_x),
        yaxis_title=label_with_unit(prop_y),
        width=1000, height=750,
        margin=dict(l=40, r=120, t=60, b=40),
        legend=dict(x=0.01, y=0.99, bgcolor="rgba(255,255,255,0.6)"),
        updatemenus=[{
            "x": -0.15, "y": 0.98,
            "type": "buttons",
            "buttons": [
                {"label": "Play", "method": "animate",
                 "args": [None, {"frame": {"duration": 1000, "redraw": True}, "fromcurrent": True}]},
                {"label": "Pause", "method": "animate",
                 "args": [[None], {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"}]},
                {"label": "Overlay ALL", "method": "update",
                 "args": [{"visible": [True, False, True]}]},
                {"label": "Show <= iter", "method": "update",
                 "args": [{"visible": [False, True, True]}]},
            ],
        }],
        sliders=[{
            "steps": [{"method": "animate", "label": str(it),
                       "args": [[str(it)], {"frame": {"duration": 0, "redraw": True}, "mode": "immediate"}]}
                      for it in iters],
            "currentvalue": {"prefix": "iteration: "},
        }],
    )
    return fig


def plot_pareto_evolution_3d(obs_df: pd.DataFrame,
                             a: str, b: str, c: str,
                             directions: dict[str, str],
                             overlay_all: bool = True) -> go.Figure:
    cols = ["molecule_id", "iteration", "smiles", a, b, c, "image_svg"]
    df = obs_df.loc[obs_df[a].notna() & obs_df[b].notna() & obs_df[c].notna(), cols].copy()
    df["iteration"] = df["iteration"].astype(int)

    df["_payload"] = df.apply(lambda r: {
        "id": r["molecule_id"],
        "iter": int(r["iteration"]),
        "smiles": r["smiles"],
        "img": r["image_svg"],
        "props": {a: float(r[a]) if pd.notna(r[a]) else None,
                  b: float(r[b]) if pd.notna(r[b]) else None,
                  c: float(r[c]) if pd.notna(r[c]) else None},
    }, axis=1)
    df["_payload"] = df["_payload"].apply(
        lambda p: {**p, "hover": make_hover_string(p["id"], p["iter"], p["smiles"], p["props"], directions)}
    )

    iters = sorted(df["iteration"].unique())

    sa = -1.0 if directions.get(_norm_key(a), "max") == "min" else 1.0
    sb = -1.0 if directions.get(_norm_key(b), "max") == "min" else 1.0
    sc = -1.0 if directions.get(_norm_key(c), "max") == "min" else 1.0
    df["_ma"] = sa * df[a].astype(float)
    df["_mb"] = sb * df[b].astype(float)
    df["_mc"] = sc * df[c].astype(float)

    fig = go.Figure()

    if overlay_all:
        fig.add_trace(go.Scatter3d(
            x=df[a], y=df[b], z=df[c],
            mode="markers",
            name="all points",
            marker=dict(size=3.5, color=df["iteration"], colorscale="Viridis", opacity=0.35, showscale=True,
                        colorbar=dict(title="iteration", x=1.02, y=0.5, len=0.85)),
            customdata=df[["_payload"]].to_numpy(),
            hovertemplate="%{customdata[0].hover}<extra></extra>",
        ))
    else:
        fig.add_trace(go.Scatter3d(x=[], y=[], z=[], mode="markers", name="all points", visible=False))

    fig.add_trace(go.Scatter3d(
        x=[], y=[], z=[], mode="markers", name="<= iter",
        marker=dict(size=3.5, color="lightgray", opacity=0.35),
        visible=not overlay_all,
        hoverinfo="skip",
    ))

    fig.add_trace(go.Scatter3d(
        x=[], y=[], z=[], mode="markers", name="pareto",
        marker=dict(size=6, symbol="diamond", opacity=0.95),
        hoverinfo="skip",
    ))

    frames = []
    for it in iters:
        dfi = df[df["iteration"] <= it].copy()
        V = dfi[["_ma", "_mb", "_mc"]].to_numpy()
        pm = pareto_mask(V)
        pareto = dfi.loc[pm]

        frames.append(go.Frame(
            name=str(it),
            data=[
                fig.data[0],
                go.Scatter3d(x=dfi[a], y=dfi[b], z=dfi[c]),
                go.Scatter3d(x=pareto[a], y=pareto[b], z=pareto[c]),
            ]
        ))

    fig.frames = frames

    if frames:
        dfi0 = df[df["iteration"] <= iters[0]].copy()
        pm0 = pareto_mask(dfi0[["_ma", "_mb", "_mc"]].to_numpy())
        pareto0 = dfi0.loc[pm0]
        fig.data[1].x, fig.data[1].y, fig.data[1].z = dfi0[a], dfi0[b], dfi0[c]
        fig.data[2].x, fig.data[2].y, fig.data[2].z = pareto0[a], pareto0[b], pareto0[c]

    fig.update_layout(
        title=f"Pareto evolution 3D: {direction_label(a, directions)} / {direction_label(b, directions)} / {direction_label(c, directions)}",
        width=1000, height=750,
        margin=dict(l=40, r=120, t=60, b=40),
        updatemenus=[{
            "type": "buttons",
            "buttons": [
                {"label": "Play", "method": "animate",
                 "args": [None, {"frame": {"duration": 1000, "redraw": True}, "fromcurrent": True}]},
                {"label": "Pause", "method": "animate",
                 "args": [[None], {"frame": {"duration": 0, "redraw": False}, "mode": "immediate"}]},
                {"label": "Overlay ALL", "method": "update",
                 "args": [{"visible": [True, False, True]}]},
                {"label": "Show <= iter", "method": "update",
                 "args": [{"visible": [False, True, True]}]},
            ],
        }],
        sliders=[{
            "steps": [{"method": "animate", "label": str(it),
                       "args": [[str(it)], {"frame": {"duration": 0, "redraw": True}, "mode": "immediate"}]}
                      for it in iters],
            "currentvalue": {"prefix": "iteration: "},
        }],
        scene=dict(xaxis_title=label_with_unit(a), yaxis_title=label_with_unit(b), zaxis_title=label_with_unit(c)),
    )
    return fig


def starting_value_map(
    prop: str,
    starting_smiles: list[str],
    *,
    space_df: pd.DataFrame,
    obs_df: pd.DataFrame,
    directions: dict[str, str],
) -> dict[str, float]:
    """
    Returns {starting_smiles: value} where value is taken:
      1) from space_df[prop] (if available; one row per starting SMILES)
      2) else from obs_df[prop] aggregated by direction over rows matching starting SMILES
    Missing stays absent (so caller can skip).
    """
    out: dict[str, float] = {}
    mode = (directions.get(_norm_key(prop), "max") or "max").lower()

    # Prefer space_df values when available
    if prop in space_df.columns:
        for smi in starting_smiles:
            s = space_df.loc[space_df["smiles"] == smi, prop]
            if not s.empty and pd.notna(s.iloc[0]):
                out[smi] = float(s.iloc[0])

    # Fill any missing from obs_df when available
    if prop in obs_df.columns:
        tmp = obs_df.loc[
            obs_df["smiles"].isin(starting_smiles) & obs_df[prop].notna(),
            ["smiles", prop],
        ].copy()
        if not tmp.empty:
            if mode == "min":
                agg = tmp.groupby("smiles")[prop].min()
            else:
                agg = tmp.groupby("smiles")[prop].max()
            for smi, v in agg.items():
                out.setdefault(str(smi), float(v))

    return out


def add_starting_hlines(
    fig: go.Figure,
    prop: str,
    starting_smiles: list[str],
    *,
    space_df: pd.DataFrame,
    obs_df: pd.DataFrame,
    directions: dict[str, str],
    row: int | None = None,
    col: int | None = None,
) -> None:
    """
    Adds one red dotted hline per starting molecule (when value is available).
    """
    smi_to_v = starting_value_map(prop, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
    for i, (smi, v) in enumerate(smi_to_v.items(), start=1):
        fig.add_hline(
            y=v,
            line_color="rgba(255,0,0,0.5)",
            line_dash="dot",
            annotation_text=f"start {i}",
            annotation_position="top left",
            row=row,
            col=col,
        )


def _starting_image_svg(smi: str, space_df: pd.DataFrame) -> str:
    if "image_svg" not in space_df.columns:
        return ""
    s = space_df.loc[space_df["smiles"] == smi, "image_svg"]
    if s.empty or pd.isna(s.iloc[0]):
        return ""
    return str(s.iloc[0])


def add_starting_star_2d(
    fig: go.Figure,
    *,
    prop_x: str,
    prop_y: str,
    starting_smiles: list[str],
    space_df: pd.DataFrame,
    obs_df: pd.DataFrame,
    directions: dict[str, str],
    name: str = "starting",
) -> None:
    """
    Adds red star markers for starting molecules on 2D scatter-type figures.
    Skips molecules missing either coordinate.
    """
    xs = starting_value_map(prop_x, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
    ys = starting_value_map(prop_y, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)

    pts = []
    for smi in starting_smiles:
        if smi in xs and smi in ys:
            pts.append((smi, xs[smi], ys[smi]))

    if not pts:
        return

    payloads = []
    X, Y = [], []
    for i, (smi, xv, yv) in enumerate(pts, start=1):
        payload = {
            "id": f"start_{i}",
            "iter": None,
            "smiles": smi,
            "img": _starting_image_svg(smi, space_df),
            "props": {prop_x: xv, prop_y: yv},
        }
        payload["hover"] = make_hover_string(payload["id"], payload["iter"], payload["smiles"], payload["props"], directions)
        payloads.append(payload)
        X.append(xv); Y.append(yv)

    fig.add_trace(go.Scatter(
        x=X, y=Y,
        mode="markers",
        name=name,
        marker=dict(symbol="star", size=16, color="red", line=dict(color="black", width=1)),
        customdata=np.array(payloads, dtype=object).reshape(-1, 1),
        hovertemplate="%{customdata[0].hover}<extra></extra>",
    ))


def add_starting_star_3d(
    fig: go.Figure,
    *,
    a: str, b: str, c: str,
    starting_smiles: list[str],
    space_df: pd.DataFrame,
    obs_df: pd.DataFrame,
    directions: dict[str, str],
    name: str = "starting",
) -> None:
    xs = starting_value_map(a, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
    ys = starting_value_map(b, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
    zs = starting_value_map(c, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)

    pts = []
    for smi in starting_smiles:
        if smi in xs and smi in ys and smi in zs:
            pts.append((smi, xs[smi], ys[smi], zs[smi]))

    if not pts:
        return

    payloads = []
    X, Y, Z = [], [], []
    for i, (smi, xv, yv, zv) in enumerate(pts, start=1):
        payload = {
            "id": f"start_{i}",
            "iter": None,
            "smiles": smi,
            "img": _starting_image_svg(smi, space_df),
            "props": {a: xv, b: yv, c: zv},
        }
        payload["hover"] = make_hover_string(payload["id"], payload["iter"], payload["smiles"], payload["props"], directions)
        payloads.append(payload)
        X.append(xv); Y.append(yv); Z.append(zv)

    fig.add_trace(go.Scatter3d(
        x=X, y=Y, z=Z,
        mode="markers",
        name=name,
        marker=dict(symbol="x", size=3.5, color="red"),
        customdata=np.array(payloads, dtype=object).reshape(-1, 1),
        hovertemplate="%{customdata[0].hover}<extra></extra>",
    ))


def fix_updatemenus_visible_for_added_trace(fig: go.Figure) -> None:
    """
    Pareto evolution buttons use explicit visible arrays. If we add a starting trace,
    extend those arrays by one True so the star stays visible in both modes.
    """
    if not getattr(fig.layout, "updatemenus", None):
        return
    for menu in fig.layout.updatemenus:
        if not getattr(menu, "buttons", None):
            continue
        for btn in menu.buttons:
            args = getattr(btn, "args", None)
            if not args or not isinstance(args, (list, tuple)):
                continue
            if len(args) < 1 or not isinstance(args[0], dict):
                continue
            if "visible" in args[0] and isinstance(args[0]["visible"], (list, tuple)):
                vis = list(args[0]["visible"])
                vis.append(True)
                args[0]["visible"] = vis
                btn.args = args


def plot_from_raw(
    raw: Dict[str, Any],
    outdir: str | Path,
    *,
    dist_kind: str = "box",
    tsne_perplexity: int = 30,
    tsne_random_state: int = 0,
    bestN_min_n: int = 15,
    generate_images: bool = False,
) -> dict[str, Any]:
    """
    Python API for plotting from raw workflow data.
      - takes workflow's `raw` dict as input
      - writes the same CSVs + HTML plots as the CLI
      - returns useful artifacts for downstream use

    Parameters:
    - raw: the raw dict from the workflow execution
    - outdir: where to write CSVs and HTML plots
    - dist_kind: the kind of distance plot to make, either "box" or "violin"
    - tsne_perplexity: perplexity parameter for t-SNE chemical space plot
    - tsne_random_state: random state for t-SNE embedding
    - bestN_min_n: minimum N to show in the best-N-so-far box plot (default 15)
    - generate_images: whether to generate SVG images for molecules (can slow down plotting and increase output size)

    Returns:
    A dict containing:
    - "workflow_id": the workflow ID from the raw data
    - "outdir": the output directory where files were written
    - "opt_props": the list of optimized properties
    - "directions": the optimization directions for each property
    - "obs_df": the processed observations DataFrame
    - "space_df": the processed search space DataFrame
    - "allow_global_extrema": whether global extrema are allowed based on the tools used
    - "batch_size": the batch size used in the workflow
    """
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    workflow_id = str(raw["workflow_id"])
    prefix = f"{workflow_id}__"

    directions = get_opt_directions(raw)
    opt_props = list(directions.keys())
    starting_smiles = list(raw["parsed_arguments"].get("starting_molecules", []))

    batch_size = int(raw["parsed_arguments"]["batch_size"])

    obs_df = build_observations_df(raw)
    space_df = build_search_space_df(raw)
    space_df = attach_tested_flags(space_df, obs_df)

    if generate_images:
        space_df["image_svg"] = space_df["smiles"].astype(str).apply(smiles_to_svg)
        obs_df = obs_df.merge(space_df[["molecule_id", "image_svg"]], on="molecule_id", how="left")
    else:
        # keep column present so payload/click logic doesn't break
        obs_df["image_svg"] = ""
        space_df["image_svg"] = ""

    opt_props_str = ";".join(opt_props)
    obs_df["optimized_properties"] = opt_props_str
    space_df["optimized_properties"] = opt_props_str

    # keep filenames prefixed with workflow_id
    obs_df.drop(columns=["image_svg"], errors="ignore").to_csv(outdir / f"{prefix}observations.csv", index=False)
    space_df.drop(columns=["image_svg"], errors="ignore").to_csv(outdir / f"{prefix}search_space.csv", index=False)

    allow_global_extrema = (obs_df["tool"].dropna().nunique() == 1) and (obs_df["tool"].dropna().unique()[0] == "rdkit")
    extrema_space_df = space_df if allow_global_extrema else None

    def _write(fig: go.Figure, name: str, post_script: str | None = None) -> None:
        fig.write_html(outdir / f"{prefix}{name}.html", include_plotlyjs="cdn", post_script=post_script)

    # --- Always make TSNE (chemical space) ---
    fig = plot_tsne_space(
        space_df=space_df,
        obs_df=obs_df,
        opt_props=opt_props,
        directions=directions,
        perplexity=tsne_perplexity,
        random_state=tsne_random_state,
        starting_smiles=starting_smiles
    )
    _write(fig, "tsne_space", post_script=HTML_POST_SCRIPT)

    # --- Diversity ---
    div_df = compute_diversity_timeseries(obs_df)
    _write(plot_diversity(div_df), "diversity_tanimoto")

    # --- Per-objective plots ---
    for p in opt_props:
        if p not in obs_df.columns:
            continue

        fig = plot_prop_vs_iteration(
            obs_df,
            prop=p,
            kind=dist_kind,
            show_points=True,
            space_df=extrema_space_df,
            directions=directions,
        )
        add_starting_hlines(fig, p, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
        _write(fig, f"{dist_kind}_iter_{p}", post_script=HTML_POST_SCRIPT)

        fig = plot_minmax_vs_iteration(
            obs_df,
            prop=p,
            space_df=extrema_space_df,
            directions=directions,
        )
        add_starting_hlines(fig, p, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
        _write(fig, f"minmax_iter_{p}")

        prog = compute_objective_progress(obs_df, extrema_space_df, p, directions[_norm_key(p)])
        add_starting_hlines(fig, p, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions, row=1, col=1)
        _write(plot_objective_progress(prog, p, directions[_norm_key(p)], space_df=extrema_space_df), f"progress_{p}")

        fig = plot_bestN_so_far_box(
            obs_df,
            prop=p,
            directions=directions,
            batch_size=batch_size,
            min_n=bestN_min_n,
            space_df=extrema_space_df,
            show_points=True,
        )
        add_starting_hlines(fig, p, starting_smiles, space_df=space_df, obs_df=obs_df, directions=directions)
        _write(fig, f"bestN_sofar_box_{p}", post_script=HTML_POST_SCRIPT)

    # --- Pair plots (if >=2 objectives) ---
    if len(opt_props) >= 2:
        for i in range(len(opt_props)):
            for j in range(i + 1, len(opt_props)):
                a, b = opt_props[i], opt_props[j]
                if a not in obs_df.columns or b not in obs_df.columns:
                    continue

                fig = plot_prop_pair(obs_df, a, b, directions=directions)
                add_starting_star_2d(fig, prop_x=a, prop_y=b, starting_smiles=starting_smiles,
                                     space_df=space_df, obs_df=obs_df, directions=directions)
                _write(fig, f"pair_{a}_vs_{b}", post_script=HTML_POST_SCRIPT)

                fig = plot_pareto_evolution_2d(obs_df, a, b, directions=directions, overlay_all=True)
                add_starting_star_2d(fig, prop_x=a, prop_y=b, starting_smiles=starting_smiles,
                                     space_df=space_df, obs_df=obs_df, directions=directions)
                fix_updatemenus_visible_for_added_trace(fig)
                _write(fig, f"pareto_evolution_{a}_vs_{b}", post_script=HTML_POST_SCRIPT)

    # --- Triplet plots + radial (if >=3 objectives) ---
    if len(opt_props) >= 3:
        for i in range(len(opt_props)):
            for j in range(i + 1, len(opt_props)):
                for k in range(j + 1, len(opt_props)):
                    a, b, c = opt_props[i], opt_props[j], opt_props[k]
                    if a not in obs_df.columns or b not in obs_df.columns or c not in obs_df.columns:
                        continue

                    fig = plot_prop_triplet_3d(obs_df, a, b, c, directions=directions)
                    add_starting_star_3d(fig, a=a, b=b, c=c, starting_smiles=starting_smiles,
                                         space_df=space_df, obs_df=obs_df, directions=directions)
                    _write(fig, f"triplet_{a}_{b}_{c}", post_script=HTML_POST_SCRIPT)

                    fig = plot_pareto_evolution_3d(obs_df, a, b, c, directions=directions, overlay_all=True)
                    add_starting_star_3d(fig, a=a, b=b, c=c, starting_smiles=starting_smiles,
                                         space_df=space_df, obs_df=obs_df, directions=directions)
                    fix_updatemenus_visible_for_added_trace(fig)
                    _write(fig, f"pareto_evolution_{a}_{b}_{c}", post_script=HTML_POST_SCRIPT)

        fig = plot_radial_iterations(
            obs_df=obs_df,
            space_df=space_df,
            props=[p for p in opt_props if p in obs_df.columns and p in space_df.columns],
            directions=directions,
            starting_smiles=starting_smiles
        )
        _write(fig, "radial_iterations", post_script=HTML_POST_SCRIPT)

    return {
        "workflow_id": workflow_id,
        "outdir": outdir,
        "opt_props": opt_props,
        "directions": directions,
        "obs_df": obs_df,
        "space_df": space_df,
        "allow_global_extrema": allow_global_extrema,
        "batch_size": batch_size,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--outdir", required=True)

    ap.add_argument("--dist_kind", default="box", choices=["box", "violin"])
    ap.add_argument("--tsne_perplexity", type=int, default=30)
    ap.add_argument("--tsne_random_state", type=int, default=0)
    ap.add_argument("--bestN_min_n", type=int, default=15)
    ap.add_argument("--generate_images", action="store_true",
                    help="If set, generate RDKit SVG depictions.")
    args = ap.parse_args()

    raw = load_workflow_json(args.input)
    plot_from_raw(
        raw=raw,
        outdir=args.outdir,
        dist_kind=args.dist_kind,
        tsne_perplexity=args.tsne_perplexity,
        tsne_random_state=args.tsne_random_state,
        bestN_min_n=args.bestN_min_n,
        generate_images=args.generate_images,
    )
    

if __name__ == "__main__":
    main()
