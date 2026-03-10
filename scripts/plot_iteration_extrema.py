import json
import argparse
import re
from pathlib import Path
from collections import defaultdict

from matplotlib import pyplot as plt

parser = argparse.ArgumentParser(
    description="Plot baseline and per-iteration extrema for a single target."
)
parser.add_argument("--path", type=str, required=True, help="Path to directory with JSON files.")
args = parser.parse_args()

PATH = Path(args.path)


def _extract_baseline_from_summary(summary_text: str, target_name: str):
    if not summary_text:
        return None
    pattern = rf"Baseline:\s*{re.escape(target_name)}\s*:\s*([0-9]*\.?[0-9]+)"
    match = re.search(pattern, summary_text)
    if not match:
        return None
    return float(match.group(1))


def _get_targets(item):
    if "targets" in item:
        return item["targets"]
    if "configuration" in item and "targets" in item["configuration"]:
        return item["configuration"]["targets"]
    return []


def _get_experimental_results(item):
    if "experimental_results" in item:
        return item["experimental_results"]
    if "experimental_data" in item:
        return item["experimental_data"]
    return []


def _plot_for_file(file_path: Path):
    with file_path.open("r") as f:
        item = json.load(f)

    targets = _get_targets(item)
    if len(targets) != 1:
        return

    target = targets[0]
    target_name = target["name"]
    target_mode = str(target.get("mode", "")).upper()

    data = _get_experimental_results(item)
    if not data:
        return

    values_by_iter = defaultdict(list)
    for res in data:
        iteration = res.get("iteration")
        props = res.get("properties", {})
        if iteration is None or target_name not in props:
            continue
        values_by_iter[int(iteration)].append(props[target_name])

    if not values_by_iter:
        return

    if target_mode == "MIN":
        selector = min
        ylabel = f"{target_name} (min per iteration)"
    else:
        selector = max
        ylabel = f"{target_name} (max per iteration)"

    iterations = sorted(values_by_iter.keys())
    extrema = [selector(values_by_iter[i]) for i in iterations]

    baseline = _extract_baseline_from_summary(item.get("summary", ""), target_name)
    x_labels = ["Baseline"] + [str(i) for i in iterations]
    y_values = [baseline] + extrema if baseline is not None else extrema

    plt.figure(figsize=(8, 4))
    plt.plot(range(len(y_values)), y_values, marker="o")
    plt.xticks(range(len(x_labels)), x_labels)
    plt.xlabel("Iteration")
    plt.ylabel(ylabel)
    title_suffix = "min" if target_mode == "MIN" else "max"
    plt.title(f"Baseline and per-iteration {title_suffix} for {target_name}")
    plt.tight_layout()

    output_path = file_path.with_name(f"{file_path.stem}_iteration_extrema_{target_name}.png")
    plt.savefig(output_path, dpi=300)
    plt.close()


for json_file in PATH.glob("*.json"):
    _plot_for_file(json_file)
