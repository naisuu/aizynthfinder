import pytest

from aizynthfinder.aizynthfinder import AiZynthFinder
from aizynthfinder.chem import TreeMolecule
from aizynthfinder.mcts.state import State
from aizynthfinder.mcts.node import Node


def test_reset_tree():
    finder = AiZynthFinder()
    finder.target_smiles = "CCCO"
    finder.prepare_tree()

    assert finder.tree is not None

    finder.target_smiles = "CCO"

    assert finder.tree is None


@pytest.fixture
def mock_create_root(mocker):
    """
    Fixture for mocking the creating of the root node.
    Will return the TreeMolecule object that is in the root node
    """
    mocked_create_root = mocker.patch("aizynthfinder.mcts.node.Node.create_root")

    def wrapper(root_smiles, config):
        mol = TreeMolecule(parent=None, transform=0, smiles=root_smiles)
        state = State(mols=[mol], config=config)
        mocked_create_root.return_value = Node(state=state, owner=None, config=config)
        return mol

    return wrapper


def test_one_expansion(mock_get_actions, mock_create_root, mock_stock):
    """
    Test the building of this tree:
                root
                  |
                child 1
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child1_mol, *_ = mock_get_actions(root_mol, tuple([root_smi]), [child1_smi], [0.3])
    mock_stock(finder.config, *child1_mol)
    finder.target_mol = root_mol

    # Test first with return_first
    finder.config.return_first = True
    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 2
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert finder.search_stats["iterations"] == 1
    assert finder.search_stats["returned_first"]

    # then test with iteration limit
    finder.config.return_first = False
    finder.config.iteration_limit = 45
    finder.prepare_tree()
    finder.tree_search()

    assert len(finder.tree.graph()) == 2
    assert finder.search_stats["iterations"] == 45
    assert not finder.search_stats["returned_first"]


def test_two_expansions(mock_get_actions, mock_create_root, mock_stock):
    """
    Test the building of this tree:
                root
                  |
                child 1
                  |
                child 2
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    child1_mol, *_ = mock_get_actions(root_mol, tuple([root_smi]), [child1_smi], [0.3])
    child2_mol, *_ = mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [child2_smi],
        [0.3],
    )
    mock_stock(finder.config, child1_mol[0], child1_mol[2], *child2_mol)
    finder.target_mol = root_mol
    finder.config.return_first = True

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 3
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == [child1_mol[0], child1_mol[2]] + child2_mol
    assert finder.search_stats["iterations"] == 1


def test_two_expansions_two_children(mock_get_actions, mock_create_root, mock_stock):
    """
    Test the building of this tree:
                root
            /           \
        child 1        child 2
            |             |
        grandchild 1   grandchild 2
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F"]
    grandchild_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    child1_mol, child2_mol = mock_get_actions(
        root_mol,
        tuple([root_smi]),
        [child1_smi, child2_smi],
        [0.3, 0.1],
    )
    grandchild1_mol = mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [grandchild_smi],
        [0.3],
    )
    grandchild2_mol = mock_get_actions(
        child2_mol[1],
        tuple(child2_smi),
        [grandchild_smi],
        [0.3],
    )
    mock_stock(finder.config, child1_mol[0], child1_mol[2], *grandchild1_mol[0])
    finder.target_mol = root_mol

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 5
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == [child1_mol[0], child1_mol[2]] + grandchild1_mol[0]
    assert nodes[3].state.mols == child2_mol
    assert nodes[4].state.mols == [child2_mol[0]] + grandchild2_mol[0]
    assert finder.search_stats["iterations"] == 100


def test_three_expansions(
    default_config, mock_get_actions, mock_create_root, mock_stock
):
    """
    Test the building of this tree:
                root
                  |
                child 1
                  |
                child 2
                  |
                child 3 (*)
        - child 3 state is solved
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    child3_smi = ["O=C(Cl)c1ccccc1"]
    child1_mol, *_ = mock_get_actions(root_mol, tuple([root_smi]), [child1_smi], [0.3])
    child2_mol, *_ = mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [child2_smi],
        [0.3],
    )
    smiles_state2 = [child1_smi[0], child1_smi[2]] + child2_smi
    child3_mol, *_ = mock_get_actions(
        child2_mol[1],
        tuple(smiles_state2),
        [child3_smi],
        [0.3],
    )
    mock_stock(finder.config, child1_mol[0], child1_mol[2], child2_mol[0], *child3_mol)
    finder.target_mol = root_mol
    finder.config.return_first = True

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 4
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == [child1_mol[0], child1_mol[2]] + child2_mol
    expected_list = [child1_mol[0], child1_mol[2], child2_mol[0]] + child3_mol
    assert nodes[3].state.mols == expected_list
    assert nodes[3].state.is_solved
    assert finder.search_stats["iterations"] == 1


def test_three_expansions_not_solved(
    default_config, mock_get_actions, mock_create_root, mock_stock
):
    """
    Test the building of this tree:
                root
                  |
                child 1
                  |
                child 2
                  |
                child 3
        - child 3 state is not solved (not in stock)
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    child3_smi = ["O=C(Cl)c1ccccc1"]
    child1_mol, *_ = mock_get_actions(root_mol, tuple([root_smi]), [child1_smi], [0.3])
    child2_mol, *_ = mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [child2_smi],
        [0.3],
    )
    smiles_state2 = [child1_smi[0], child1_smi[2]] + child2_smi
    child3_mol, *_ = mock_get_actions(
        child2_mol[1],
        tuple(smiles_state2),
        [child3_smi],
        [0.3],
    )
    mock_stock(finder.config, child1_mol[0], child1_mol[2], child2_mol[0])
    finder.target_mol = root_mol
    finder.config.return_first = True
    finder.config.max_transforms = 2
    finder.config.iteration_limit = 15

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 4
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == [child1_mol[0], child1_mol[2]] + child2_mol
    expected_list = [child1_mol[0], child1_mol[2], child2_mol[0]] + child3_mol
    assert nodes[3].state.mols == expected_list
    assert not nodes[3].state.is_solved
    assert finder.search_stats["iterations"] == 15


def test_two_expansions_no_expandable_root(
    mock_get_actions, mock_create_root, mock_stock
):
    """
    Test the following scenario:
                root
                  |
              child 1 (+)

        - child 1 will be selected first for expansion (iteration 1)
        - it has no children that can be expanded (marked by +)
        -- end of iteration 1
        - iteration 2 starts but selecting a leaf will raise an exception
        -- will continue to iterate until reached number of iteration (set 10 in the test)
        * nodes in tree will be root, child 1
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child1_mol, *_ = mock_get_actions(root_mol, tuple([root_smi]), [child1_smi], [0.3])
    mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [None],
        [0.3],
    )  # Will try to expand child1
    mock_stock(finder.config, child1_mol[0], child1_mol[2])
    finder.target_mol = root_mol
    finder.config.return_first = True
    finder.config.iteration_limit = 10

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 2
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert finder.search_stats["iterations"] == 10


def test_two_expansions_no_reactants_first_child(
    mock_get_actions, mock_create_root, mock_stock
):
    """
    Test the following scenario:
                root
            /           \
        child 1 (+)        child 2
                             |
                        grandchild 1 (*)

        - child 1 will be selected first for expansion (iteration 1)
        - it has no children that can be expanded (marked by +)
        -- end of iteration 1
        - child 2 will be selected for expansion  (iteration 2)
        - grandchild 1 will be selected next and it is in stock (marked by *)
        -- a solution is found and the tree search is terminated
        * nodes in tree will be root, child1, child2, grandchild 1
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F"]
    grandchild1_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    child1_mol, child2_mol = mock_get_actions(
        root_mol,
        tuple([root_smi]),
        [child1_smi, child2_smi],
        [0.3, 0.1],
    )
    mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [None],
        [0.3],
    )  # Will try to expand child1
    grandchild1_mol, *_ = mock_get_actions(
        child2_mol[1],
        tuple(child2_smi),
        [grandchild1_smi],
        [0.3],
    )
    mock_stock(finder.config, child1_mol[0], child1_mol[2], *grandchild1_mol)
    finder.target_mol = root_mol
    finder.config.return_first = True

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 4
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == child2_mol
    assert nodes[3].state.mols == [child2_mol[0]] + grandchild1_mol
    assert finder.search_stats["iterations"] == 2


def test_three_expansions_no_reactants_first_child(
    mock_get_actions, mock_create_root, mock_stock
):
    """
    Test the following scenario:
                root
            /           \
        child 1 (+)        child 2
                          |
                    grandchild 1
                          |
                    grandchild 2 (*)

        - child 1 will be selected first for expansion (iteration 1)
        - it has no children that can be expanded (marked by +)
        -- end of iteration 1
        - child 2 will be selected for expansion  (iteration 2)
        - grandchild 1 will be selected next
        - grandchild 2 will be selected next and it is in stock (marked by *)
        -- a solution is found and the tree search is terminated
        * nodes in tree will be root, child1, child2, grandchild 1, grandchild 2
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F"]
    grandchild1_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    grandchild2_smi = ["O=C(Cl)c1ccccc1"]
    child1_mol, child2_mol = mock_get_actions(
        root_mol,
        tuple([root_smi]),
        [child1_smi, child2_smi],
        [0.3, 0.1],
    )
    mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [None],
        [0.3],
    )  # Will try to expand child1
    grandchild1_mol, *_ = mock_get_actions(
        child2_mol[1],
        tuple(child2_smi),
        [grandchild1_smi],
        [0.3],
    )
    smiles_state2 = [child2_smi[0]] + grandchild1_smi
    grandchild2_mol, *_ = mock_get_actions(
        grandchild1_mol[1],
        tuple(smiles_state2),
        [grandchild2_smi],
        [0.3],
    )
    mock_stock(
        finder.config,
        child1_mol[0],
        child1_mol[2],
        grandchild1_mol[0],
        *grandchild2_mol
    )
    finder.target_mol = root_mol
    finder.config.return_first = True

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 5
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == child2_mol
    assert nodes[3].state.mols == [child2_mol[0]] + grandchild1_mol
    expected_list = [child2_mol[0], grandchild1_mol[0]] + grandchild2_mol
    assert nodes[4].state.mols == expected_list
    assert finder.search_stats["iterations"] == 2


def test_three_expansions_no_reactants_second_level(
    mock_get_actions, mock_create_root, mock_stock
):
    """
    Test the following scenario:
                root
            /           \
        child 1         child 2
           |               |
        grandchild 1 (+) grandchild 2 (*)

        - child 1 will be selected first for expansion (iteration 1)
        - grandchild 1 will be selected next,
        - it has no children that can be expanded (marked by x)
        -- end of iteration 1
        - child 2 will be selected for expansion  (iteration 2)
        - grandchild 2 will be selected next and it is in stock (marked by *)
        -- a solution is found and the tree search is terminated
        * nodes in tree will be root, child1, grandchild 1, child2, grandchild 2
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F"]
    grandchild1_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    grandchild2_smi = ["N#Cc1cccc(N)c1", "O=C(Cl)c1ccc(F)c(F)c1"]
    child1_mol, child2_mol = mock_get_actions(
        root_mol,
        tuple([root_smi]),
        [child1_smi, child2_smi],
        [0.3, 0.1],
    )
    grandchild1_mol, *_ = mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [grandchild1_smi],
        [0.3],
    )
    smiles_state1 = [child1_smi[0], child1_smi[2]] + grandchild1_smi
    mock_get_actions(
        grandchild1_mol[1],
        tuple(smiles_state1),
        [None],
        [0.3],
    )  # Will try to expand grandchild 1
    grandchild2_mol, *_ = mock_get_actions(
        child2_mol[1],
        tuple(child2_smi),
        [grandchild2_smi],
        [0.3],
    )
    mock_stock(
        finder.config,
        child1_mol[0],
        child1_mol[2],
        grandchild1_mol[0],
        *grandchild2_mol
    )
    finder.target_mol = root_mol
    finder.config.return_first = True

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 5
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == [child1_mol[0], child1_mol[2]] + grandchild1_mol
    assert nodes[3].state.mols == child2_mol
    assert nodes[4].state.mols == [child2_mol[0]] + grandchild2_mol
    assert finder.search_stats["iterations"] == 2


def test_two_expansions_no_reactants_second_child(
    mock_get_actions, mock_create_root, mock_stock
):
    """
    Test the following scenario:
                root
            /           \
        child 1        child 2 (+)
            |
        grandchild 1 (*)

        - child 1 will be selected first for expansion (iteration 1)
        - grandchild 1 will be selected next and it is in stock (marked by *)
        -- end of iteration 1
        - child 2 will be selected for expansion  (iteration 2)
        - it has no children that can be expanded (marked with +)
        -- will continue to iterate until reached number of iteration (set 10 in the test)
        * nodes in tree will be root, child1, grandchild 1, child2
    """
    finder = AiZynthFinder()
    root_smi = "CN1CCC(C(=O)c2cccc(NC(=O)c3ccc(F)cc3)c2F)CC1"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F", "O"]
    child2_smi = ["CN1CCC(Cl)CC1", "N#Cc1cccc(NC(=O)c2ccc(F)cc2)c1F"]
    grandchild1_smi = ["N#Cc1cccc(N)c1F", "O=C(Cl)c1ccc(F)cc1"]
    child1_mol, child2_mol = mock_get_actions(
        root_mol,
        tuple([root_smi]),
        [child1_smi, child2_smi],
        [0.3, 0.1],
    )
    grandchild1_mol, *_ = mock_get_actions(
        child1_mol[1],
        tuple(child1_smi),
        [grandchild1_smi],
        [0.3],
    )
    mock_get_actions(
        child2_mol[1],
        tuple(child2_smi),
        [None],
        [0.3],
    )  # Will try to expand child2
    mock_stock(finder.config, child1_mol[0], child1_mol[2], *grandchild1_mol)
    finder.target_mol = root_mol
    finder.config.iteration_limit = 10

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 4
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == [child1_mol[0], child1_mol[2]] + grandchild1_mol
    assert nodes[3].state.mols == child2_mol
    assert finder.search_stats["iterations"] == 10


def test_two_expansions_cyclic(mock_get_actions, mock_create_root, mock_stock):
    """
    Test the building of this tree:
                root
                  |
                child 1
                  |
                child 2
    But making child 2 should be rejected because child 2 == root
    """
    finder = AiZynthFinder()
    root_smi = "COc1cc2cc(-c3ccc(OC(C)=O)c(OC(C)=O)c3)[n+](C)c(C)c2cc1OC"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["COc1cc2cc(-c3ccc(O)c(OC(C)=O)c3)[n+](C)c(C)c2cc1OC"]
    child2_smi = ["COc1cc2cc(-c3ccc(OC(C)=O)c(OC(C)=O)c3)[n+](C)c(C)c2cc1OC"]
    child1_mol, *_ = mock_get_actions(root_mol, tuple([root_smi]), [child1_smi], [0.3])
    child2_mol, *_ = mock_get_actions(
        child1_mol[0],
        tuple(child1_smi),
        [child2_smi],
        [0.3],
    )
    mock_stock(finder.config)
    finder.target_mol = root_mol
    finder.config.iteration_limit = 1

    finder.tree_search()

    nodes = list(finder.tree.graph())
    assert len(nodes) == 2
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert finder.search_stats["iterations"] == 1


def test_two_expansions_prune_cyclic(mock_get_actions, mock_create_root, mock_stock):
    """
    Test the building of this tree:
                root
                  |
                child 1
                  |
                child 2
    Child 2 will not be rejected, but the tree search will not end, so catch an exception and
    assert on what we got.
    """
    finder = AiZynthFinder()
    root_smi = "COc1cc2cc(-c3ccc(OC(C)=O)c(OC(C)=O)c3)[n+](C)c(C)c2cc1OC"
    root_mol = mock_create_root(root_smi, finder.config)
    child1_smi = ["COc1cc2cc(-c3ccc(O)c(OC(C)=O)c3)[n+](C)c(C)c2cc1OC"]
    child2_smi = ["COc1cc2cc(-c3ccc(OC(C)=O)c(OC(C)=O)c3)[n+](C)c(C)c2cc1OC"]
    child1_mol, *_ = mock_get_actions(root_mol, tuple([root_smi]), [child1_smi], [0.3])
    child2_mol, *_ = mock_get_actions(
        child1_mol[0],
        tuple(child1_smi),
        [child2_smi],
        [0.3],
    )
    mock_stock(finder.config)
    finder.target_mol = root_mol
    finder.config.iteration_limit = 1
    finder.config.prune_cycles_in_search = False

    try:
        finder.tree_search()
    except KeyError:
        pass

    nodes = list(finder.tree.graph())
    assert len(nodes) == 4
    assert nodes[0].state.mols == [root_mol]
    assert nodes[1].state.mols == child1_mol
    assert nodes[2].state.mols == child2_mol
    assert finder.search_stats["iterations"] == 1
