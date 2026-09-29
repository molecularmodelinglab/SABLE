from nodes.characterize_molecules import _map_target_properties
from schemas.state import TargetProperty


def test_platform_score_is_not_mapped_to_binding_affinity():
    targets = [TargetProperty(name="binding_affinity")]

    mapped = _map_target_properties(
        targets,
        {"boltz_optimization_score": 0.82},
    )

    assert mapped == {}


def test_platform_score_maps_to_resolved_platform_target():
    targets = [TargetProperty(name="boltz_optimization_score")]

    mapped = _map_target_properties(
        targets,
        {"boltz_optimization_score": 0.82},
    )

    assert mapped == {"boltz_optimization_score": 0.82}


def test_openfe_request_and_registry_dispatch():
    from unittest.mock import Mock
    from nodes.characterize_molecules import _build_characterization_request, _run_characterization_tool
    from schemas.state import WorkflowState
    from schemas.tool_schemas import CharacterizationResult
    from tools.registry import get_tool_registry

    options = {"complexes": {"mol_1": {"structure": "/data/complex.cif"}}}
    state = WorkflowState(user_prompt="OpenFE", characterization_config={"openfe": options})
    request = _build_characterization_request(
        "openfe", state, ["mol_1"], {"mol_1": "CCO"}, {"mol_1": "CCO"}, [], {},
    )
    assert request.tool_options == options
    assert request.proteins == []
    tool = Mock()
    tool.characterize.return_value = CharacterizationResult(
        results={"mol_1": {"openfe_binding_free_energy": -8.2}},
    )
    result = _run_characterization_tool(get_tool_registry().get("openfe"), tool, request)
    assert result.results["mol_1"]["openfe_binding_free_energy"] == -8.2
    tool.characterize.assert_called_once_with(request)


def test_openfe_energy_does_not_become_boltz_affinity():
    assert _map_target_properties(
        [TargetProperty(name="binding_affinity")], {"openfe_binding_free_energy": -8.2},
    ) == {}


def test_openfe_boltz_request_includes_proteins():
    from nodes.characterize_molecules import _build_characterization_request
    from schemas.state import WorkflowState, ProteinTarget

    state = WorkflowState(
        user_prompt="OpenFE", characterization_config={"openfe": {"structure_source": "boltz"}},
        protein_targets=[ProteinTarget(chain_id="A", sequence="MAAA")],
    )
    request = _build_characterization_request(
        "openfe", state, ["mol_1"], {"mol_1": "CCO"}, {"mol_1": "CCO"}, [], {},
    )
    assert request.proteins[0]["sequence"] == "MAAA"


def test_openfe_selection_preserves_upstream_boltz_configuration():
    from nodes.decide_characterization import decide_characterization_node
    from schemas.state import WorkflowState

    config = {"base_url": "https://boltz.example", "timeout": 123, "cif_save_dir": "/tmp/poses"}
    state = WorkflowState(
        user_prompt="OpenFE", targets=[TargetProperty(name="openfe_binding_free_energy")],
        characterization_config={"openfe": {"structure_source": "boltz"}, "boltz_config": config},
    )
    decide_characterization_node(state)
    assert state.characterization_config["boltz_config"] == config
    assert state.characterization_config["openfe"]["structure_source"] == "boltz"


def test_openfe_generates_platform_poses_before_upload():
    from unittest.mock import Mock, patch
    from nodes.characterize_molecules import _run_openfe_with_boltz
    from schemas.state import WorkflowState
    from schemas.tool_schemas import CharacterizationRequest, CharacterizationResult

    state = WorkflowState(user_prompt="OpenFE", characterization_config={"boltz": {"provider": "platform"}})
    request = CharacterizationRequest(
        smiles={"mol_1": "CCO"}, search_space={"mol_1": "CCO"}, molecule_ids=["mol_1"],
        properties=["openfe_binding_free_energy"], proteins=[{"chain_id": "A", "sequence": "MAAA"}],
        tool_options={"structure_source": "boltz"},
    )
    poses = CharacterizationResult(results={"mol_1": {}}, metadata={"provider": "platform"})
    tool = Mock()
    tool.complexes_from_boltz.return_value = {"mol_1": {"structure": "/tmp/pose.cif"}}
    tool.characterize.return_value = CharacterizationResult(results={"mol_1": {"openfe_binding_free_energy": -8.2}})
    with patch("nodes.characterize_molecules._run_boltz_platform_characterization", return_value=poses) as generate:
        result = _run_openfe_with_boltz(state, request, Mock(), tool)
    tool.ensure_upload_support.assert_called_once()
    generate.assert_called_once()
    uploaded = tool.characterize.call_args.args[0]
    assert uploaded.tool_options["upload_inputs"] is True
    assert uploaded.tool_options["complexes"] == tool.complexes_from_boltz.return_value
    assert "complexes" not in request.tool_options
    assert result.metadata["boltz"] == {"provider": "platform"}


def test_openfe_boltz_resume_does_not_generate_or_upload():
    from unittest.mock import Mock, patch
    from nodes.characterize_molecules import _run_openfe_with_boltz
    from schemas.state import WorkflowState
    from schemas.tool_schemas import CharacterizationRequest
    from tools.openfe_tool import OpenFETool

    request = CharacterizationRequest(
        smiles={"mol_1": "CCO"}, search_space={"mol_1": "CCO"}, molecule_ids=["mol_1"],
        properties=["openfe_binding_free_energy"],
        tool_options={"structure_source": "boltz", "resume": True, "campaign_name": "existing"},
    )
    tool = OpenFETool(base_url="https://openfe.example", session=Mock())
    tool.wait_for_results = Mock(return_value=[{
        "ligand": "mol_1", "dg": -8.2, "uncertainty": 0.1, "quality": "pass",
    }])
    with patch("nodes.characterize_molecules._run_boltz_platform_characterization") as generate:
        result = _run_openfe_with_boltz(WorkflowState(user_prompt="OpenFE"), request, Mock(), tool)
    generate.assert_not_called()
    tool.session.request.assert_not_called()
    assert result.results["mol_1"]["openfe_binding_free_energy"] == -8.2