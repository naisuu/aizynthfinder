import json
import os
import sys
from datetime import datetime
from typing import *

from aizynthfinder.aizynthfinder import AiZynthFinder


def disablePrint():
    sys.stdout = open(os.devnull, 'w')


def enablePrint():
    sys.stdout = sys.__stdout__


def generate_time_tag() -> str:
    d = datetime.now()
    return d.strftime("%d%m%y_%H%M")


def setup_finder(config_path: str = "data/config.yml"):
    finder = AiZynthFinder(config_path)
    finder.stock.select("zinc")
    finder.expansion_policy.select("uspto")
    finder.filter_policy.select("uspto")
    return finder


def load_smiles(smiles_path: str) -> List[str]:
    print(f"Loading SMILES from '{smiles_path}'.")
    with open(smiles_path, mode="r", encoding="utf-8") as f:
        smiles_list = [smiles for smiles in f.read().split("\n")]
    print(f"Loaded {len(smiles_list)} SMILES.")
    return smiles_list


def experiment_with_C(finder: AiZynthFinder, C_values: List[float],
                      smiles_path: str = "data/smiles_list.txt") -> Dict[float, List[Dict[str, str]]]:
    smiles_list = load_smiles(smiles_path)
    result_dict = {}
    for C in C_values:
        finder.config.C = C
        print(f"Running finder with C set to {finder.config.C}")
        stats_list = process_smiles_list(finder, target_smiles=smiles_list)
        result_dict[C] = stats_list
    return result_dict


def process_smiles_list(finder: AiZynthFinder, target_smiles: List[str]) -> List[Dict[str, str]]:
    stats_list: List[Dict[str, str]] = []
    for smiles in target_smiles:
        finder.target_smiles = smiles
        finder.tree_search()
        finder.build_routes()
        stats_list.append(finder.extract_statistics())
    return stats_list


def main(verbose=False):
    if not verbose:
        disablePrint()

    finder = setup_finder()

    time_tag = generate_time_tag()

    C_values = [1.0, 1.2, 1.4]
    result_dict = experiment_with_C(finder=finder, C_values=C_values)

    with open(f"data/result_dict_json_{time_tag}.json", mode="w", encoding="utf-8") as json_file:
        json.dump(result_dict, json_file)
