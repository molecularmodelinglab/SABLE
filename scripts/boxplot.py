import json
import argparse
from pathlib import Path
from collections import defaultdict

from matplotlib import pyplot as plt
import seaborn as sns

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
    useful_data = [exp for exp in data if len(exp['properties']) == len_targets]
    if len_targets > 1:
        continue 
    i_idx = targets[0]
    
    temp_dat = defaultdict(list)
    for i in useful_data:
        temp_dat[i['iteration']].append(i['properties'][targets[0]])

    nested_data =[j for i, j in temp_dat.items()]
    
    sns.boxplot(nested_data)
    plt.xlabel('Iteration')
    plt.ylabel(f'{targets[0]} - {target_info[0]["mode"]}')
    plt.savefig(f'{PATH}/boxplot_{item_name}.png', dpi=300)
    plt.close()