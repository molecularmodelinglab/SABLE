import pytest

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


def test_openfe_selection_preserves_structure_configuration():
    openfe_config = {"complexes": {"mol_1": {"structure": "/data/complex.cif"}}}
    state = WorkflowState(
        user_prompt="minimize OpenFE binding free energy",
        targets=[TargetProperty(name="openfe_binding_free_energy")],
        characterization_config={"openfe": openfe_config},
    )
    result = decide_characterization_node(state)
    assert result.characterization_config["tool_ids"] == ["openfe"]
    assert result.characterization_config["tool"] == "openfe"
    assert result.characterization_config["openfe"] == openfe_config
    assert result.characterization_config["requires_boltz"] is False


def test_openfe_can_be_combined_with_descriptor_tools():
    state = WorkflowState(
        user_prompt="OpenFE and QED",
        targets=[TargetProperty(name="openfe_binding_free_energy"), TargetProperty(name="qed")],
    )
    result = decide_characterization_node(state)
    assert result.characterization_config["tool_ids"] == ["rdkit", "openfe"]


def test_binding_affinity_still_selects_boltz():
    state = WorkflowState(
        user_prompt="binding affinity",
        targets=[TargetProperty(name="binding_affinity")],
    )
    assert decide_characterization_node(state).characterization_config["tool_ids"] == ["boltz"]


def test_prompt_only_openfe_selection_defaults_to_boltz_poses():
    from nodes.characterize_molecules import _build_characterization_request

    state = WorkflowState(
        user_prompt="Optimize aspirin analogs for absolute binding free energy using OpenFE",
        targets=[TargetProperty(name="openfe_binding_free_energy")],
    )
    state = decide_characterization_node(state)
    assert state.characterization_config["tool_ids"] == ["openfe"]

    request = _build_characterization_request(
        "openfe", state, ["mol_1"], {"mol_1": "CCO"}, {"mol_1": "CCO"}, [], {},
    )

    assert request.tool_options == {"structure_source": "boltz"}
    assert "openfe" not in state.characterization_config


def test_openfe_request_preserves_explicit_configuration():
    from nodes.characterize_molecules import _build_characterization_request

    configurations = [
        {"complexes": {"mol_1": {"structure": "/data/complex.cif"}}},
        {"complexes": {}},
        {"structure_source": "explicit"},
        {"structure_source": None},
    ]
    for options in configurations:
        state = WorkflowState(
            user_prompt="OpenFE", characterization_config={"openfe": options},
        )
        request = _build_characterization_request(
            "openfe", state, ["mol_1"], {"mol_1": "CCO"}, {"mol_1": "CCO"}, [], {},
        )
        assert request.tool_options == options
        assert state.characterization_config["openfe"] == options


def test_free_energy_prompt_corrects_llm_affinity_and_routes_to_openfe():
    from unittest.mock import Mock
    from nodes.argument_extraction.hybrid import HybridArgumentExtractor
    from nodes.characterize_molecules import _build_characterization_request
    from schemas.state import ProteinTarget

    smiles = r"CCC/N=C1\S/C(=C\c2ccc(C(=O)OC)c(Cl)c2)C(=O)N1c1ccccc1C"
    prompt = f"Optimize {smiles} for a better free energy to P21453. Do 2 iterations and use a batch size of 1"
    extractor = HybridArgumentExtractor()
    extractor.extract_with_llm = Mock(return_value={
        "starting_molecules": [smiles], "confidence_score": 0.95,
        "proteins": [{"chain_id": "A", "uniprot_id": "P21453"}],
        "target_properties": [{"property_name": "binding_affinity", "optimization_mode": "MAX", "bounds": [-3, 6]}],
        "max_iterations": 2, "batch_size": 1,
    })
    extracted = extractor.extract_dict(prompt)
    target = extracted["target_properties"][0]
    assert target["property_name"] == "openfe_binding_free_energy"
    assert target["optimization_mode"] == "MIN"
    assert list(target["bounds"]) == [-30, 10]
    assert extracted["starting_molecules"] == [smiles]
    assert extracted["max_iterations"] == 2
    assert extracted["batch_size"] == 1
    state = WorkflowState(
        user_prompt=prompt, targets=[TargetProperty(name=target["property_name"])],
        protein_targets=[ProteinTarget(**extracted["proteins"][0])],
    )
    decide_characterization_node(state)
    assert state.characterization_config["tool_ids"] == ["openfe"]
    request = _build_characterization_request(
        "openfe", state, ["mol_1"], {"mol_1": smiles}, {"mol_1": smiles}, [], {},
    )
    assert request.tool_options["structure_source"] == "boltz"


@pytest.mark.parametrize(("prompt", "proteins"), [
    ("Optimize CCO for better binding affinity to P21453", [{"chain_id": "A", "uniprot_id": "P21453"}]),
    ("Optimize CCO for better free energy", []),
    ("Optimize CCO for relative binding free energy to P21453", [{"chain_id": "A", "uniprot_id": "P21453"}]),
    ("Optimize CCO for free energy using RBFE to P21453", [{"chain_id": "A", "uniprot_id": "P21453"}]),
    ("Optimize CCO for binding affinity and free energy to P21453", [{"chain_id": "A", "uniprot_id": "P21453"}]),
])
def test_free_energy_correction_preserves_other_intents(prompt, proteins):
    from nodes.argument_extraction.hybrid import HybridArgumentExtractor

    target = {"property_name": "binding_affinity", "optimization_mode": "MIN", "bounds": [-3, 6]}
    extracted = HybridArgumentExtractor().validate_and_merge({
        "starting_molecules": ["CCO"], "confidence_score": 0.95,
        "proteins": proteins, "target_properties": [target],
    }, {}, prompt)
    assert extracted["target_properties"][0]["property_name"] == "binding_affinity"


def test_free_energy_correction_deduplicates_openfe_and_preserves_qed():
    from nodes.argument_extraction.hybrid import HybridArgumentExtractor

    extracted = HybridArgumentExtractor().validate_and_merge({
        "starting_molecules": ["CCO"], "confidence_score": 0.95,
        "proteins": [{"chain_id": "A", "uniprot_id": "P21453"}],
        "target_properties": [
            {"property_name": "binding_affinity", "optimization_mode": "MIN"},
            {"property_name": "openfe_binding_free_energy", "optimization_mode": "MIN"},
            {"property_name": "qed", "optimization_mode": "MAX"},
        ],
    }, {}, "Optimize CCO for better binding free energy to P21453 and higher QED")
    assert [target["property_name"] for target in extracted["target_properties"]] == [
        "openfe_binding_free_energy", "qed",
    ]
    assert [target["weight"] for target in extracted["target_properties"]] == [0.5, 0.5]