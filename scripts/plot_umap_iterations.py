import json
import argparse
from pathlib import Path

import numpy as np
from matplotlib import pyplot as plt

try:
    from rdkit import Chem
    from rdkit.Chem import DataStructs
    from rdkit.Chem.rdFingerprintGenerator import GetMorganGenerator
except Exception as exc:  # pragma: no cover - optional runtime dependency
    raise ImportError(
        "RDKit is required for UMAP plots. Please install RDKit in the current environment."
    ) from exc

try:
    import umap
except Exception as exc:  # pragma: no cover - optional runtime dependency
    raise ImportError(
        "umap-learn is required for UMAP plots. Install with `pip install umap-learn`."
    ) from exc


parser = argparse.ArgumentParser(description="Plot UMAP of tested molecules by iteration.")
parser.add_argument("--path", type=str, required=True, help="Path to directory containing JSON files.")
parser.add_argument("--n-neighbors", type=int, default=15, help="UMAP n_neighbors parameter.")
parser.add_argument("--min-dist", type=float, default=0.1, help="UMAP min_dist parameter.")
parser.add_argument("--n-bits", type=int, default=2048, help="Morgan fingerprint bit size.")
args = parser.parse_args()

PATH = Path(args.path)


def _get_experimental_results(item):
    if "experimental_results" in item:
        return item["experimental_results"]
    if "experimental_data" in item:
        return item["experimental_data"]
    return []


def _get_search_space(item):
    if "search_space" in item:
        return item["search_space"]
    if "configuration" in item and "search_space" in item["configuration"]:
        return item["configuration"]["search_space"]
    return {}


def _fingerprints_from_smiles(smiles_list, n_bits=2048):
    morgan = GetMorganGenerator(radius=2, fpSize=n_bits)
    fps = []
    valid_idx = []
    for idx, smi in enumerate(smiles_list):
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            continue
        fp = morgan.GetFingerprint(mol)
        arr = np.zeros((n_bits,), dtype=np.uint8)
        DataStructs.ConvertToNumpyArray(fp, arr)
        fps.append(arr)
        valid_idx.append(idx)
    return np.array(fps), valid_idx


def _plot_umap_for_file(file_path: Path):
    with file_path.open("r") as f:
        item = json.load(f)

    data = _get_experimental_results(item)
    search_space = _get_search_space(item)

    if not search_space:
        return

    all_ids = list(search_space.keys())
    all_smiles = [search_space[mid] for mid in all_ids]

    tested_by_id = {}
    for res in data:
        mol_id = res.get("molecule_id")
        iteration = res.get("iteration")
        if mol_id:
            tested_by_id[mol_id] = iteration

    fps, valid_idx = _fingerprints_from_smiles(all_smiles, n_bits=args.n_bits)
    if fps.size == 0:
        return

    valid_ids = [all_ids[i] for i in valid_idx]
    tested_mask = [mid in tested_by_id for mid in valid_ids]
    iterations = [tested_by_id[mid] for mid in valid_ids if mid in tested_by_id]
    reducer = umap.UMAP(
        n_neighbors=args.n_neighbors,
        min_dist=args.min_dist,
        metric="jaccard",
        random_state=42,
    )
    embedding = reducer.fit_transform(fps)

    unique_iters = sorted({it for it in iterations if it is not None})

    plt.figure(figsize=(8, 6))
    if any(tested_mask):
        untested_mask = [not flag for flag in tested_mask]
        plt.scatter(
            embedding[untested_mask, 0],
            embedding[untested_mask, 1],
            s=10,
            alpha=0.2,
            color="#9aa0a6",
            label="Unexplored",
        )

        cmap = plt.get_cmap("tab20", max(1, len(unique_iters)))
        for idx, iteration in enumerate(unique_iters):
            mask = [mid in tested_by_id and tested_by_id[mid] == iteration for mid in valid_ids]
            plt.scatter(
                embedding[mask, 0],
                embedding[mask, 1],
                s=18,
                alpha=0.85,
                color=cmap(idx),
                label=f"Iteration {iteration}",
            )
    else:
        plt.scatter(
            embedding[:, 0],
            embedding[:, 1],
            s=10,
            alpha=0.3,
            color="#9aa0a6",
            label="Unexplored",
        )

    plt.legend(title="Iterations", fontsize=8)
    plt.xlabel("UMAP-1")
    plt.ylabel("UMAP-2")
    plt.title("UMAP of Search Space (Colored by Iteration)")
    plt.tight_layout()
    output_path = file_path.with_name(f"{file_path.stem}_umap_by_iteration.png")
    plt.savefig(output_path, dpi=300)
    plt.close()


for json_file in PATH.glob("*.json"):
    _plot_umap_for_file(json_file)
