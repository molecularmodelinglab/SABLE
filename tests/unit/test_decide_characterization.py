from nodes.decide_characterization import decide_characterization_node
from schemas.state import TargetProperty, WorkflowState


def test_decision_preserves_boltz_provider_configuration():
    boltz_config = {
        "provider": "platform",
        "credential_id": "98f1a5e8-bf16-4955-a244-91b01c8cb44c",
        "execution_preference": "library_screen",
        "metrics": ["optimization_score"],
    }
    state = WorkflowState(
        user_prompt="maximize platform score",
        targets=[TargetProperty(name="boltz_optimization_score")],
        characterization_config={"boltz": boltz_config},
    )

    result = decide_characterization_node(state)

    assert result.characterization_config["boltz"] == boltz_config
    assert result.characterization_config["tool_ids"] == ["boltz"]