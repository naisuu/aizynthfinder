import sys
import gzip
import json

import pytest
import numpy as np
import yaml

from aizynthfinder.context.config import Configuration
from aizynthfinder.context.policy import ExpansionPolicy, FilterPolicy
from aizynthfinder.context.stock import Stock
from aizynthfinder.mcts.node import Node
from aizynthfinder.chem import Molecule, TreeMolecule, RetroReaction
from aizynthfinder.mcts.mcts import SearchTree
from aizynthfinder.analysis import TreeAnalysis
from aizynthfinder.utils.trees import (
    AndOrSearchTreeBase,
    TreeNodeMixin,
    SplitAndOrTree,
)
from aizynthfinder.utils.serialization import MoleculeDeserializer


def pytest_addoption(parser):
    parser.addoption(
        "--finder_config",
        help="the configuration file for the aizynthfinder",
    )
    parser.addoption(
        "--stocks",
        nargs="+",
        help="the stocks to use in the aizynthfinder",
    )
    parser.addoption("--policy", help="the policy to use in the aizynthfinder")
    parser.addoption(
        "--run_integration",
        action="store_true",
        default=False,
        help="run integration tests",
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run_integration"):
        return

    skip_integration = pytest.mark.skip(reason="need --run_integration option to run")
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: this one is for integration tests."
    )


@pytest.fixture
def add_cli_arguments():
    saved_argv = list(sys.argv)

    def wrapper(args):
        sys.argv = [sys.argv[0]] + args.split(" ")

    yield wrapper
    sys.argv = saved_argv


@pytest.fixture
def default_config():
    return Configuration()


@pytest.fixture
def filter_policy(default_config):
    policy = FilterPolicy(default_config)
    return policy


@pytest.fixture
def fresh_tree(default_config):
    return SearchTree(config=default_config, root_smiles=None)


@pytest.fixture
def generate_root(default_config):
    def wrapper(smiles):
        return Node.create_root(smiles, tree=None, config=default_config)

    return wrapper


@pytest.fixture
def mock_expansion_policy(mocker, simple_actions):
    mocked_get_action = mocker.patch(
        "aizynthfinder.context.policy.ExpansionPolicy.get_actions"
    )

    def wrapper(mol):
        mocked_get_action.return_value = simple_actions(mol)
        return mocked_get_action.return_value

    return wrapper


@pytest.fixture
def load_reaction_tree(shared_datadir):
    def wrapper(filename, index=0):
        filename = str(shared_datadir / filename)
        with open(filename, "r") as fileobj:
            trees = json.load(fileobj)
        if isinstance(trees, dict):
            return trees
        else:
            return trees[index]

    return wrapper


@pytest.fixture
def mocked_reaction(mocker):
    """
    Fixture for creating a mocked Reaction object
    Will return a function that should be called with the parent of the reaction
    and the TreeMolecule objects that should be returned when calling ``apply``.
    """

    def wrapper(parent, return_value):
        class MockedReaction(mocker.MagicMock):
            @property
            def index(self):
                return 0

            @property
            def mol(self):
                return parent

            @property
            def reactants(self):
                return [return_value] if return_value else []

            def apply(self, *_):
                return self.reactants

        return MockedReaction()

    return wrapper


@pytest.fixture
def mock_get_actions(mocker, mocked_reaction):
    """
    Fixture for mocking the call to the ``get_actions`` method of the Policy class,
    used to return reactions and probabilities of these actions.

    Will return a function that should be called will:
        - the parent TreeMolecule object
        - the SMILES of the State object of the Node that will be calling ``get_actions``
        - a list of the the SMILES of the molecules that will be returned by the Reaction class
        - a list of probabilities for each action that should be returned by ``get_actions``

    the function will create the TreeMolecule objects that should be returned by the Reaction classes,
    and it will return them to the caller.
    """
    actions = {}

    def get_action(mols):
        key = tuple(mol.smiles for mol in mols)
        return actions[key]

    mocked_get_actions = mocker.patch(
        "aizynthfinder.context.policy.ExpansionPolicy.get_actions"
    )
    mocked_get_actions.side_effect = get_action

    def wrapper(parent, key_smiles, child_smiles_list, probs):
        rxn_objs = []
        mol_objs_list = []
        for child_smiles in child_smiles_list:
            if not child_smiles:
                rxn_objs.append(mocked_reaction(parent, None))
                continue
            mol_objs = [
                TreeMolecule(parent=parent, smiles=smiles) for smiles in child_smiles
            ]
            mol_objs_list.append(mol_objs)
            rxn_objs.append(mocked_reaction(parent, mol_objs))
        actions[key_smiles] = rxn_objs, probs
        return mol_objs_list

    return wrapper


@pytest.fixture
def mock_policy_model(mocker):
    class MockedKerasModel(mocker.MagicMock):
        @property
        def input(self):
            pass

        @property
        def output(self):
            pass

        def predict(self, *_):
            pass

    mocker.patch.object(
        MockedKerasModel, "input", mocker.PropertyMock(return_value=np.zeros((3, 3)))
    )
    mocker.patch.object(
        MockedKerasModel, "output", mocker.PropertyMock(return_value=np.zeros((3, 3)))
    )
    mocker.patch.object(
        MockedKerasModel,
        "predict",
        mocker.MagicMock(return_value=np.array([[0.2, 0.7, 0.1]])),
    )

    return mocker.patch(
        "aizynthfinder.utils.models.load_keras_model", return_value=MockedKerasModel
    )


@pytest.fixture
def mock_stock(tmpdir):
    """
    Fixture for setting up stock of inchi keys in a textfile.
    Will return a function that should be called with any number of Molecule objects as arguments
    """

    def wrapper(config, *molecules):
        molecules = [
            Molecule(smiles=mol) if isinstance(mol, str) else mol for mol in molecules
        ]
        filename = str(tmpdir / "stock.txt")
        with open(filename, "w") as fileobj:
            fileobj.write("\n".join([mol.inchi_key for mol in molecules]))
        config.stock.load(filename, "stock")
        config.stock.select("stock")

    return wrapper


@pytest.fixture
def expansion_policy(default_config):
    policy = ExpansionPolicy(default_config)
    return policy


@pytest.fixture
def setup_analysis(default_config, shared_datadir, tmpdir, mock_stock):
    mock_stock(
        default_config, "N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1", "CN1CCC(Cl)CC1", "O"
    )

    with gzip.open(shared_datadir / "full_search_tree.json.gz", "rb") as gzip_obj:
        with open(tmpdir / "full_search_tree.json", "wb") as fileobj:
            fileobj.write(gzip_obj.read())
    tree = SearchTree.from_json(tmpdir / "full_search_tree.json", default_config)
    nodes = list(tree.graph())

    def wrapper(scorer=None):
        return TreeAnalysis(tree, scorer=scorer), nodes

    return wrapper


@pytest.fixture
def setup_complete_tree(fresh_tree, mocker, mock_stock):
    tree = fresh_tree

    state1 = mocker.MagicMock()
    state1.mols = [
        TreeMolecule(
            parent=None,
            transform=0,
            smiles="CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1",
        )
    ]
    state1.in_stock_list = [False]
    state1.score = 0.049
    state1.is_solved = False
    state1.max_transforms = state1.mols[0].transform
    node1 = mocker.MagicMock()
    node1.state = state1
    node1.parent = None
    node1.tree = tree
    node1.is_expanded = True
    action1 = (
        "([C:2]-[CH;D3;+0:1](-[C:3])-[C;H0;D3;+0:4](=[O;H0;D1;+0:6])-[c:5])"
        ">>(Cl-[CH;D3;+0:1](-[C:2])-[C:3]).(N#[C;H0;D2;+0:4]-[c:5]).([OH2;D0;+0:6])"
    )
    reaction1 = RetroReaction(state1.mols[0], action1)
    reaction1.apply()
    node1.__getitem__.return_value = {"action": reaction1}
    tree.root = node1

    state2 = mocker.MagicMock()
    state2.mols = [
        TreeMolecule(parent=state1.mols[0], smiles=smiles)
        for smiles in ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    ]
    state2.max_transforms = state2.mols[0].transform
    state2.in_stock_list = [True, False, True]
    state2.score = 0.68
    state2.is_solved = False
    node2 = mocker.MagicMock()
    node2.parent = node1
    node2.is_expanded = True
    node2.state = state2
    node2.tree = tree
    node1.promising_child.return_value = node2
    node1.children.return_value = [node2]
    action2 = (
        "([O;D1;H0:2]=[C;H0;D3;+0:1](-[c:3])-[NH;D2;+0:4]-[c:5])"
        ">>(Cl-[C;H0;D3;+0:1](=[O;D1;H0:2])-[c:3]).([NH2;D1;+0:4]-[c:5])"
    )
    reaction2 = RetroReaction(state2.mols[1], action2)
    reaction2.apply()
    node2.__getitem__.return_value = {"action": reaction2}

    state3 = mocker.MagicMock()
    state3.mols = [
        TreeMolecule(parent=state2.mols[1], smiles=smiles)
        for smiles in ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    ]
    state3.max_transforms = state3.mols[0].transform
    state3.in_stock_list = [True, True]
    state3.stock = mocker.MagicMock()
    state3.stock.__contains__.side_effect = [False, True, False, True, True, True]
    state3.score = 0.99
    state3.is_solved = True
    node3 = mocker.MagicMock()
    node3.parent = node2
    node3.tree = tree
    node3.state = state3
    node2.promising_child.return_value = node3
    node2.children.return_value = [node3]
    node3.children.return_value = []

    return tree, [node1, node2, node3]


@pytest.fixture
def set_default_prior(default_config):
    default_config.use_prior = False

    def wrapper(prior):
        default_config.default_prior = prior

    yield wrapper
    default_config.use_prior = True


@pytest.fixture
def setup_search(fresh_tree, generate_root):
    tree = fresh_tree
    tree.initialize()

    def wrapper(smiles):
        root = generate_root(smiles)
        tree.add_root(root)
        return tree, root

    return wrapper


@pytest.fixture
def simple_actions():
    # These templated reactions are taken from the full USPTO data set
    # action1 and action2 can be applied, action3 cannot
    def wrapper(mol):
        actions = {
            "CCCCOc1ccc(CC(=O)N(C)O)cc1": [
                "([#8:4]-[N;H0;D3;+0:5](-[C;D1;H3:6])-[C;H0;D3;+0:1](-[C:2])=[O;D1;H0:3])"
                ">>(Cl-[C;H0;D3;+0:1](-[C:2])=[O;D1;H0:3]).([#8:4]-[NH;D2;+0:5]-[C;D1;H3:6])",
                "([C:2]-[CH2;D2;+0:1]-[O;H0;D2;+0:3]-[c:4])>>(Br-[CH2;D2;+0:1]-[C:2]).([OH;D1;+0:3]-[c:4])",
                "([C:4]-[N;H0;D3;+0:5](-[C:6])-[C;H0;D3;+0:1](-[C:2])=[O;D1;H0:3])>>"
                "(O-[C;H0;D3;+0:1](-[C:2])=[O;D1;H0:3]).([C:4]-[NH;D2;+0:5]-[C:6])",
            ]
        }
        action1, action2, action3 = actions[mol.smiles]
        action_list = [
            RetroReaction(mol, action1, metadata={"dummy": 1}),
            RetroReaction(mol, action2, metadata={"dummy": 2}),
            RetroReaction(mol, action3, metadata={"dummy": 3}),
        ]
        prior_list = [0.7, 0.5, 0.3]
        return action_list, prior_list

    return wrapper


@pytest.fixture
def stock():
    stock = Stock()
    return stock


@pytest.fixture
def write_yaml(tmpdir):
    filename = str(tmpdir / "test.yaml")

    def wrapper(dict_):
        with open(filename, "w") as fileobj:
            yaml.dump(dict_, fileobj)
        return filename

    return wrapper


@pytest.fixture
def setup_analysis_andor_tree(default_config, shared_datadir, mock_stock):  # noqa
    mock_stock(
        default_config,
        "Nc1ccc(NC(=S)Nc2ccccc2)cc1",
        "Cc1ccc2nc3ccccc3c(Cl)c2c1",
        "Nc1ccccc1",
        "Nc1ccc(N=C=S)cc1",
        "Cc1ccc2nc3ccccc3c(Br)c2c1",
        "Nc1ccc(Br)cc1",
    )

    class BasicAndOrTree(AndOrSearchTreeBase):
        def __init__(self, filename, config):
            super().__init__(config)
            self._mol_nodes = []
            with open(filename, "r") as fileobj:
                dict_ = json.load(fileobj)
            mol_deser = MoleculeDeserializer(dict_["molecules"])
            self.root = AndOrNode(dict_["tree"], config, mol_deser, self)

        @property
        def mol_nodes(self):
            return self._mol_nodes

        def one_iteration(self):
            return False

        def routes(self):
            return SplitAndOrTree(self.root, self.config.stock).routes

    class AndOrNode(TreeNodeMixin):
        def __init__(self, dict_, config, molecules, tree):
            self.tree = tree
            self.config = config
            self._children = [
                AndOrNode(child, config, molecules, tree) for child in dict_["children"]
            ]
            if "reaction" in dict_:
                self._obj = RetroReaction(
                    molecules[dict_["reaction"]["mol"]],
                    dict_["reaction"]["smarts"],
                    dict_["reaction"]["index"],
                    dict_["reaction"].get("metadata", {}),
                )
                self._solved = all(child.prop["solved"] for child in self._children)
            else:
                self.tree._mol_nodes.append(self)
                self._obj = molecules[dict_["mol"]]
                self._solved = self._obj in self.config.stock

        @property
        def prop(self):
            obj_key = "reaction" if isinstance(self._obj, RetroReaction) else "mol"
            return {obj_key: self._obj, "solved": self._solved}

        @property
        def children(self):
            return self._children

    tree = BasicAndOrTree(str(shared_datadir / "and_or_tree.json"), default_config)

    def wrapper(scorer=None):
        return TreeAnalysis(tree, scorer=scorer)

    return wrapper
