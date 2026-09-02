import pytest

from nodes.bo_iteration import _measurement_data_from_state
from schemas.errors import NodeError
from schemas.state import ExperimentResult, ProteinTarget, TargetProperty, WorkflowState
from tools.boltz import protein_scope_id


def _state(result_scope_id: str | None) -> WorkflowState:
    protein = ProteinTarget(chain_id="A", sequence="AAAA")
    metadata = {"boltz": {"protein_scope_id": result_scope_id}} if result_scope_id else {}
    return WorkflowState(
        user_prompt="maximize score",
        targets=[TargetProperty(name="boltz_optimization_score")],
        search_space={"mol-1": "CCO"},
        protein_targets=[protein],
        experimental_results=[ExperimentResult(
            molecule_id="mol-1",
            smiles="CCO",
            iteration=1,
            properties={"boltz_optimization_score": 0.8},
            metadata=metadata,
        )],
    )


def test_same_protein_scope_is_accepted():
    scope_id = protein_scope_id([{"chain_id": "A", "sequence": "AAAA"}])

    assert _measurement_data_from_state(_state(scope_id)) == [{
        "Molecule_ID": "mol-1",
        "boltz_optimization_score": 0.8,
    }]


@pytest.mark.parametrize("scope_id", [None, "different-scope"])
def test_missing_or_different_protein_scope_is_rejected(scope_id):
    with pytest.raises(NodeError) as error:
        _measurement_data_from_state(_state(scope_id))

    assert error.value.code == "BOLTZ_OPTIMIZATION_SCOPE_MISMATCH"