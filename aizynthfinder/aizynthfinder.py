""" Module containing a class that is the main interface the retrosynthesis tool.
"""
from __future__ import annotations
import time
import importlib
from collections import defaultdict
from typing import TYPE_CHECKING

from deprecated import deprecated
from tqdm import tqdm

# This must be imported first to setup logging for rdkit, tensorflow etc
from aizynthfinder.utils.logging import logger
from aizynthfinder.context.config import Configuration
from aizynthfinder.mcts.mcts import SearchTree as MctsSearchTree
from aizynthfinder.reactiontree import ReactionTreeFromExpansion
from aizynthfinder.analysis import TreeAnalysis, RouteCollection
from aizynthfinder.chem import Molecule, TreeMolecule, FixedRetroReaction
from aizynthfinder.utils.trees import AndOrSearchTreeBase

if TYPE_CHECKING:
    from aizynthfinder.utils.type_utils import (
        StrDict,
        Optional,
        Union,
        Callable,
        List,
        Tuple,
        Dict,
    )
    from aizynthfinder.chem import RetroReaction


class AiZynthFinder:
    """
    Public API to the aizynthfinder tool

    If instantiated with the path to a yaml file or dictionary of settings
    the stocks and policy networks are loaded directly.
    Otherwise, the user is responsible for loading them prior to
    executing the tree search.

    :ivar config: the configuration of the search
    :ivar expansion_policy: the expansion policy model
    :ivar filter_policy: the filter policy model
    :ivar stock: the stock
    :ivar scorers: the loaded scores
    :ivar tree: the search tree
    :ivar analysis: the tree analysis
    :ivar routes: the top-ranked routes
    :ivar search_stats: statistics of the latest search

    :param configfile: the path to yaml file with configuration (has priority over configdict), defaults to None
    :param configdict: the config as a dictionary source, defaults to None
    """

    def __init__(self, configfile: str = None, configdict: StrDict = None) -> None:
        self._logger = logger()

        if configfile:
            self.config = Configuration.from_file(configfile)
        elif configdict:
            self.config = Configuration.from_dict(configdict)
        else:
            self.config = Configuration()

        self.expansion_policy = self.config.expansion_policy
        self.filter_policy = self.config.filter_policy
        self.stock = self.config.stock
        self.scorers = self.config.scorers
        self.tree: Optional[Union[MctsSearchTree, AndOrSearchTreeBase]] = None
        self._target_mol: Optional[Molecule] = None
        self.search_stats: StrDict = dict()
        self.routes = RouteCollection([])
        self.analysis: Optional[TreeAnalysis] = None

    @property
    def target_smiles(self) -> str:
        """The SMILES representation of the molecule to predict routes on."""
        if not self._target_mol:
            return ""
        return self._target_mol.smiles

    @target_smiles.setter
    def target_smiles(self, smiles: str) -> None:
        self.target_mol = Molecule(smiles=smiles)

    @property
    def target_mol(self) -> Optional[Molecule]:
        """The molecule to predict routes on"""
        return self._target_mol

    @target_mol.setter
    def target_mol(self, mol: Molecule) -> None:
        self.tree = None
        self._target_mol = mol

    def build_routes(self, min_nodes: int = 5, scorer: str = "state score") -> None:
        """
        Build reaction routes

        This is necessary to call after the tree search has completed in order
        to extract results from the tree search.

        :param min_nodes: the minimum number of top-ranked nodes to consider, defaults to 5
        :param scorer: a reference to the object used to score the nodes
        :raises ValueError: if the search tree not initialized
        """
        if not self.tree:
            raise ValueError("Search tree not initialized")

        self.analysis = TreeAnalysis(self.tree, scorer=self.scorers[scorer])
        self.routes = RouteCollection.from_analysis(self.analysis, min_nodes)

    def extract_statistics(self) -> StrDict:
        """Extracts tree statistics as a dictionary"""
        if not self.analysis:
            return {}
        stats = {
            "target": self.target_smiles,
            "search_time": self.search_stats["time"],
            "first_solution_time": self.search_stats.get("first_solution_time", 0),
            "first_solution_iteration": self.search_stats.get(
                "first_solution_iteration", 0
            ),
        }
        stats.update(self.analysis.tree_statistics())
        return stats

    def prepare_tree(self) -> None:
        """
        Setup the tree for searching

        :raises ValueError: if the target molecule was not set
        """
        if not self.target_mol:
            raise ValueError("No target molecule set")

        self.stock.reset_exclusion_list()
        if self.config.exclude_target_from_stock and self.target_mol in self.stock:
            self.stock.exclude(self.target_mol)
            self._logger.debug("Excluding the target compound from the stock")

        self._setup_search_tree()
        self.analysis = None
        self.routes = RouteCollection([])

    @deprecated(version="2.1.0", reason="Not supported anymore")
    def run_from_json(self, params: StrDict) -> StrDict:
        """
        Run a search tree by reading settings from a JSON

        :param params: the parameters of the tree search
        :return: dictionary with all settings and top scored routes
        """
        self.stock.select(params["stocks"])
        self.expansion_policy.select(params.get("policy", params.get("policies", "")))
        if "filter" in params:
            self.filter_policy.select(params["filter"])
        else:
            self.filter_policy.deselect()
        self.config.C = params["C"]
        self.config.max_transforms = params["max_transforms"]
        self.config.cutoff_cumulative = params["cutoff_cumulative"]
        self.config.cutoff_number = params["cutoff_number"]
        self.target_smiles = params["smiles"]
        self.config.return_first = params["return_first"]
        self.config.time_limit = params["time_limit"]
        self.config.iteration_limit = params["iteration_limit"]
        self.config.exclude_target_from_stock = params["exclude_target_from_stock"]
        self.config.filter_cutoff = params["filter_cutoff"]

        self.prepare_tree()
        self.tree_search()
        self.build_routes()
        if not params.get("score_trees", False):
            return {
                "request": self._get_settings(),
                "trees": self.routes.dicts,
            }

        self.routes.compute_scores(*self.scorers.objects())
        return {
            "request": self._get_settings(),
            "trees": self.routes.dict_with_scores(),
        }

    def tree_search(self, show_progress: bool = False) -> float:
        """
        Perform the actual tree search

        :param show_progress: if True, shows a progress bar
        :return: the time past in seconds
        """
        if not self.tree:
            self.prepare_tree()
        assert (
            self.tree is not None
        )  # This is for type checking, prepare_tree is creating it.
        self.search_stats = {"returned_first": False, "iterations": 0}

        time0 = time.time()
        i = 1
        self._logger.debug("Starting search")
        time_past = time.time() - time0

        if show_progress:
            pbar = tqdm(total=self.config.iteration_limit, leave=False)

        while time_past < self.config.time_limit and i <= self.config.iteration_limit:
            if show_progress:
                pbar.update(1)
            self.search_stats["iterations"] += 1

            try:
                is_solved = self.tree.one_iteration()
            except StopIteration:
                break

            if is_solved and "first_solution_time" not in self.search_stats:
                self.search_stats["first_solution_time"] = time.time() - time0
                self.search_stats["first_solution_iteration"] = i

            if self.config.return_first and is_solved:
                self._logger.debug("Found first solved route")
                self.search_stats["returned_first"] = True
                break
            i = i + 1
            time_past = time.time() - time0

        if show_progress:
            pbar.close()
        time_past = time.time() - time0
        self._logger.debug("Search completed")
        self.search_stats["time"] = time_past
        return time_past

    def _get_settings(self) -> StrDict:
        """Get the current settings as a dictionary"""
        # To be backward-compatible
        if (
            self.expansion_policy.selection
            and len(self.expansion_policy.selection) == 1
        ):
            policy_value = self.expansion_policy.selection[0]
            policy_key = "policy"
        else:
            policy_value = self.expansion_policy.selection  # type: ignore
            policy_key = "policies"

        dict_ = {
            "stocks": self.stock.selection,
            policy_key: policy_value,
            "C": self.config.C,
            "max_transforms": self.config.max_transforms,
            "cutoff_cumulative": self.config.cutoff_cumulative,
            "cutoff_number": self.config.cutoff_number,
            "smiles": self.target_smiles,
            "return_first": self.config.return_first,
            "time_limit": self.config.time_limit,
            "iteration_limit": self.config.iteration_limit,
            "exclude_target_from_stock": self.config.exclude_target_from_stock,
            "filter_cutoff": self.config.filter_cutoff,
        }
        if self.filter_policy.selection:
            dict_["filter"] = self.filter_policy.selection
        return dict_

    def _setup_search_tree(self):
        self._logger.debug("Defining tree root: %s" % self.target_smiles)
        if self.config.search_algorithm.lower() == "mcts":
            self.tree = MctsSearchTree(
                root_smiles=self.target_smiles, config=self.config
            )
        else:
            module_name, cls_name = self.config.search_algorithm.rsplit(".", maxsplit=1)
            try:
                module_obj = importlib.import_module(module_name)
            except ImportError:
                raise ValueError(f"Could not import module {module_name}")

            if not hasattr(module_obj, cls_name):
                raise ValueError(f"Could not identify class {cls_name} in module")

            self.tree: AndOrSearchTreeBase = getattr(module_obj, cls_name)(
                root_smiles=self.target_smiles, config=self.config
            )


class AiZynthExpander:
    """
    Public API to the AiZynthFinder expansion and filter policies

    If instantiated with the path to a yaml file or dictionary of settings
    the stocks and policy networks are loaded directly.
    Otherwise, the user is responsible for loading them prior to
    executing the tree search.

    :ivar config: the configuration of the search
    :ivar expansion_policy: the expansion policy model
    :ivar filter_policy: the filter policy model

    :param configfile: the path to yaml file with configuration (has priority over configdict), defaults to None
    :param configdict: the config as a dictionary source, defaults to None
    """

    def __init__(self, configfile: str = None, configdict: StrDict = None) -> None:
        self._logger = logger()

        if configfile:
            self.config = Configuration.from_file(configfile)
        elif configdict:
            self.config = Configuration.from_dict(configdict)
        else:
            self.config = Configuration()

        self.expansion_policy = self.config.expansion_policy
        self.filter_policy = self.config.filter_policy

    def do_expansion(
        self,
        smiles: str,
        return_n: int = 5,
        filter_func: Callable[[RetroReaction], bool] = None,
    ) -> List[Tuple[FixedRetroReaction, ...]]:
        """
        Do the expansion of the given molecule returning a list of
        reaction tuples. Each tuple in the list contains reactions
        producing the same reactants. Hence, nested structure of the
        return value is way to group reactions.

        If filter policy is setup, the probability of the reactions are
        added as metadata to the reaction.

        The additional filter functions makes it possible to do customized
        filtering. The callable should take as only argument a `RetroReaction`
        object and return True if the reaction can be kept or False if it should
        be removed.

        :param smiles: the SMILES string of the target molecule
        :param return_n: the length of the return list
        :param filter_func: an additional filter function
        :return: the grouped reactions
        """

        mol = TreeMolecule(parent=None, smiles=smiles)
        actions, _ = self.expansion_policy.get_actions([mol])
        results: Dict[Tuple[str, ...], List[FixedRetroReaction]] = defaultdict(list)
        for action in actions:
            reactants = action.apply()
            if not reactants:
                continue
            if filter_func and not filter_func(action):
                continue
            if self.filter_policy.selection:
                _, feasibility_prob = self.filter_policy.feasibility(action)
                action.metadata["feasibility"] = float(feasibility_prob)
            action.metadata["expansion_rank"] = len(results) + 1
            unique_key = tuple(sorted(mol.inchi_key for mol in reactants[0]))
            if unique_key not in results and len(results) >= return_n:
                continue
            rxn = next(ReactionTreeFromExpansion(action).tree.reactions())  # type: ignore
            results[unique_key].append(rxn)
        return [tuple(reactions) for reactions in results.values()]
