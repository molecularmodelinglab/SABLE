import json
import argparse
from pathlib import Path
from matplotlib import pyplot as plt


parser = argparse.ArgumentParser(description='Produce plots from JSON files.')
parser.add_argument('--path', type=str, required=True, help='Path to the directory containing JSON files.')
args = parser.parse_args()

PATH = args.path

ALL_JSON = {}

def load_json_file(file_path):
    for file in Path(file_path).glob('*.json'):
        with open(file, 'r') as f:
            data = json.load(f)
            ALL_JSON[file.name] = data


load_json_file(PATH)

for item_name, item in ALL_JSON.items():
    
    targets = [target['name'] for target in item['targets']]
    target_info = item['targets']
    data = item['experimental_results']
    len_targets = len(targets)
    if 'binding_affinity' in targets:
        x_idx = targets.index('binding_affinity')
        other_idxs = [i for i in range(len_targets) if i != x_idx]
    else:
        x_idx = 0
        other_idxs = [i for i in range(1, len_targets)]
    useful_data = [exp for exp in data if len(exp['properties']) == len_targets]
    for i in other_idxs:
        plt.figure()
        plt.scatter(
            [res['properties'][targets[x_idx]] for res in useful_data],
            [res['properties'][targets[i]] for res in useful_data],
            # color is iteration
            c=[res['iteration'] for res in useful_data],
            cmap='viridis',
        )
        plt.colorbar(label='Iteration')
        plt.xlabel(f'{targets[x_idx]} - {target_info[x_idx]["mode"]}')
        plt.ylabel(f'{targets[i]} - {target_info[i]["mode"]}')
        if target_info[i]['mode'] == 'MATCH':
            plt.ylabel(f'{targets[i]} - {target_info[i]["mode"]} ({target_info[i]["bounds"]})')
        elif target_info[x_idx]['mode'] == 'MAXIMIZE':
            plt.xlabel(f'{targets[x_idx]} - {target_info[x_idx]["mode"]} ({target_info[x_idx]["bounds"]})')
        plt.title(f'{targets[x_idx]} vs {targets[i]}')
        plt.savefig(f'{PATH}/{item_name[:-5]}_plot_{targets[x_idx]}_vs_{targets[i]}.png', dpi=300)
        plt.close()