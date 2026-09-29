import json
import os
from unittest.mock import Mock, patch

import pytest
import requests

from tools.openfe_tool import OpenFEError, OpenFETool, OpenFETimeout
from schemas.tool_schemas import CharacterizationRequest


def make_tool(**kwargs):
    return OpenFETool(base_url="https://openfe.example", session=Mock(), **kwargs)


def summary(operation="submit", status="done", runs=None):
    return {"last_operation": {"operation": operation, "status": status}, "runs": runs or []}


def test_campaign_lifecycle_waits_before_next_stage():
    tool = make_tool()
    tool.session.request.return_value.status_code = 200
    tool.session.request.return_value.json.side_effect = [
        {"name": "test"}, {}, summary("prep"), {}, summary("plan"), {},
    ]
    assert tool.submit_campaign({"name": "test"}) == {"name": "test", "status": "submitted"}
    assert [(call.args[0], call.args[1]) for call in tool.session.request.call_args_list] == [
        ("POST", "https://openfe.example/campaigns"),
        ("POST", "https://openfe.example/campaigns/test/prep"),
        ("GET", "https://openfe.example/campaigns/test"),
        ("POST", "https://openfe.example/campaigns/test/plan"),
        ("GET", "https://openfe.example/campaigns/test"),
        ("POST", "https://openfe.example/campaigns/test/submit"),
    ]


@pytest.mark.parametrize("fail", [False, True])
def test_upload_maps_unique_files_and_closes_handles(tmp_path, fail):
    tool = make_tool()
    first = tmp_path / "first" / "model.cif"
    second = tmp_path / "second" / "model.cif"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    campaign = {"name": "test", "complexes": [
        {"structure": str(first), "ligand": {"smiles": "CCO"}},
        {"structure": str(second), "protein": {"path": str(first)}},
    ]}
    handles = []

    def receive(method, url, **kwargs):
        assert url.endswith("/campaigns/upload")
        payload = json.loads(kwargs["data"]["campaign"])
        assert payload["complexes"][0]["structure"] == "input_1.cif"
        assert payload["complexes"][1]["structure"] == "input_2.cif"
        assert payload["complexes"][1]["protein"]["path"] == "input_1.cif"
        assert payload["complexes"][0]["ligand"]["smiles"] == "CCO"
        handles.extend(part[1][1] for part in kwargs["files"])
        assert [handle.read() for handle in handles] == [b"first", b"second"]
        if fail:
            raise requests.Timeout()
        return Mock(status_code=201, json=Mock(return_value={"name": "test"}))

    tool.session.request.side_effect = receive
    if fail:
        with pytest.raises(OpenFEError):
            tool.upload_campaign(campaign)
    else:
        assert tool.upload_campaign(campaign) == {"name": "test"}
    assert all(handle.closed for handle in handles)
    assert campaign["complexes"][0]["structure"] == str(first)
    assert tool.session.request.call_count == 1


def test_upload_missing_file_does_not_send(tmp_path):
    tool = make_tool()
    with pytest.raises(OpenFEError, match="nonempty local file"):
        tool.upload_campaign({"name": "test", "complexes": [{"structure": str(tmp_path / "missing.cif")}]})
    tool.session.request.assert_not_called()


def boltz_pose_result(tmp_path, *, extra_ligand=False, suffix=".cif"):
    import gemmi
    from schemas.tool_schemas import CharacterizationResult

    structure = gemmi.Structure()
    model = gemmi.Model("1")
    for chain_name, residue_name in [("A", "ALA"), ("Z", "LIG")] + ([("Y", "COF")] if extra_ligand else []):
        chain = gemmi.Chain(chain_name)
        residue = gemmi.Residue()
        residue.name = residue_name
        residue.seqid = gemmi.SeqId(1, " ")
        residue.het_flag = "A" if residue_name == "ALA" else "H"
        atom = gemmi.Atom()
        atom.name = "CA" if residue_name == "ALA" else "C1"
        atom.element = gemmi.Element("C")
        residue.add_atom(atom)
        chain.add_residue(residue)
        model.add_chain(chain)
    structure.add_model(model)
    structure.setup_entities()
    path = tmp_path / f"pose{suffix}"
    if suffix == ".pdb":
        structure.write_pdb(str(path))
    else:
        structure.make_mmcif_document().write_file(str(path))
    return CharacterizationResult(
        results={"mol_1": {"binding_affinity": 1.2}},
        metadata={"per_molecule_metadata": {"mol_1": {"cif_file": str(path)}}},
    )


@pytest.mark.parametrize("suffix", [".cif", ".pdb"])
def test_boltz_pose_preserves_smiles_and_uses_actual_chains(tmp_path, suffix):
    request = characterization_request()
    result = boltz_pose_result(tmp_path, suffix=suffix)
    complexes = OpenFETool.complexes_from_boltz(request, result)
    assert complexes["mol_1"]["protein"] == {"chains": ["A"]}
    assert complexes["mol_1"]["ligand"] == {
        "smiles": "CCO", "selector": {"chain": "Z", "resname": "LIG"},
    }
    assert complexes["mol_1"]["extra_ligand_copies"] == "drop"


def test_boltz_pose_rejects_cofactors_and_missing_downloads(tmp_path):
    request = characterization_request()
    with pytest.raises(OpenFEError, match="exactly one ligand"):
        OpenFETool.complexes_from_boltz(request, boltz_pose_result(tmp_path, extra_ligand=True))
    result = boltz_pose_result(tmp_path)
    result.metadata["per_molecule_metadata"]["mol_1"]["cif_file"] = None
    with pytest.raises(OpenFEError, match="did not download"):
        OpenFETool.complexes_from_boltz(request, result)


@pytest.mark.parametrize("default_source", [False, True])
def test_self_hosted_boltz_to_openfe_lifecycle(tmp_path, default_source):
    from nodes.characterize_molecules import _build_characterization_request, _run_openfe_with_boltz
    from nodes.decide_characterization import decide_characterization_node
    from schemas.state import ProteinTarget, TargetProperty, WorkflowState
    from tools.registry import get_tool_registry

    tool = make_tool()
    request = characterization_request(
        complexes={}, structure_source="boltz", campaign_name="generated", execution={"repeats": 1},
    ).model_copy(update={"proteins": [{"chain_id": "A", "sequence": "MAAA"}]})
    poses = boltz_pose_result(tmp_path)
    responses = iter([
        {"paths": {"/campaigns/upload": {"post": {}}}}, {"name": "generated"},
        {}, summary("prep"), {}, summary("plan"), {}, summary("submit"),
        [prediction(ligand="mol_1", repeats=1)],
    ])

    def respond(method, url, **kwargs):
        if url.endswith("/campaigns/upload"):
            payload = json.loads(kwargs["data"]["campaign"])
            spec = payload["complexes"][0]
            assert spec["structure"] == "input_1.cif"
            assert spec["ligand"]["selector"] == {"chain": "Z", "resname": "LIG"}
            assert spec["ligand"]["smiles"] == "CCO"
            assert kwargs["files"][0][1][1].read().startswith(b"data_")
        return Mock(status_code=200, json=Mock(return_value=next(responses)))

    tool.session.request.side_effect = respond
    state = WorkflowState(user_prompt="OpenFE", characterization_config={"boltz_config": {
        "base_url": "https://boltz.example", "api_token": "test", "fetch_cif": False,
    }})
    if default_source:
        state.targets = [TargetProperty(name="openfe_binding_free_energy")]
        state.protein_targets = [ProteinTarget(chain_id="A", sequence="MAAA")]
        state.characterization_config["openfe"] = {
            "campaign_name": "generated", "execution": {"repeats": 1},
        }
        decide_characterization_node(state)
        assert state.characterization_config["tool_ids"] == ["openfe"]
        request = _build_characterization_request(
            "openfe", state, ["mol_1"], {"mol_1": "CCO"}, {"mol_1": "CCO"}, [], {},
        )
        assert request.tool_options["structure_source"] == "boltz"
    with patch("nodes.characterize_molecules._run_characterization_tool", return_value=poses) as generate:
        result = _run_openfe_with_boltz(state, request, get_tool_registry(), tool)
    assert generate.call_args.args[2].tool_options["fetch_cif"] is True
    assert generate.call_args.args[2].proteins[0]["sequence"] == "MAAA"
    assert result.results == {"mol_1": {"openfe_binding_free_energy": -8.2}}
    assert result.metadata["structure_source"] == "boltz"
    assert result.metadata["campaign_name"] == "generated"
    assert tool.session.request.call_count == 9


@pytest.mark.parametrize("upload_supported", [False, True])
def test_upstream_failure_never_submits_openfe(tmp_path, upload_supported):
    from nodes.characterize_molecules import _run_openfe_with_boltz
    from schemas.state import WorkflowState

    tool = make_tool()
    schema = {"paths": {"/campaigns/upload": {"post": {}}}} if upload_supported else {"paths": {}}
    tool.session.request.return_value = Mock(status_code=200, json=Mock(return_value=schema))
    request = characterization_request(complexes={}, structure_source="boltz").model_copy(
        update={"proteins": [{"chain_id": "A", "sequence": "MAAA"}]},
    )
    poses = boltz_pose_result(tmp_path)
    poses.failed_molecules = ["mol_1"]
    state = WorkflowState(user_prompt="OpenFE", characterization_config={"boltz": {"provider": "platform"}})
    with patch("nodes.characterize_molecules._run_boltz_platform_characterization", return_value=poses) as generate:
        with pytest.raises(OpenFEError):
            _run_openfe_with_boltz(state, request, Mock(), tool)
    assert generate.call_count == int(upload_supported)
    assert [call.args[0] for call in tool.session.request.call_args_list] == ["GET"]


def test_characterize_uploads_only_when_requested():
    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_results = Mock(return_value=[prediction()])
    tool.characterize(characterization_request(upload_inputs=True))
    assert tool.submit_campaign.call_args.kwargs == {"upload_inputs": True}


def test_upload_submission_uses_existing_stages():
    tool = make_tool()
    tool.upload_campaign = Mock()
    tool.start_operation = Mock()
    tool.wait_for_operation = Mock()
    tool.submit_campaign({"name": "test"}, upload_inputs=True)
    tool.upload_campaign.assert_called_once_with({"name": "test"})
    assert [call.args[1] for call in tool.start_operation.call_args_list] == ["prep", "plan", "submit"]


@pytest.mark.parametrize("from_boltz", [False, True])
def test_upload_client_matches_reference_service(tmp_path, from_boltz):
    service = pytest.importorskip("openfe_api.service")
    from fastapi.testclient import TestClient
    from openfe_api.campaign import Campaign
    from openfe_api.config import ServiceSettings

    structure = tmp_path / "bound.cif"
    structure.write_bytes(b"data_bound\n")
    campaign = {
        "name": "roundtrip", "protocol": "abfe", "complexes": [{
            "name": "ethanol", "structure": str(structure),
            "protein": {"chains": ["A"]},
            "ligand": {"smiles": "CCO", "selector": {"chain": "B"}},
            "extra_ligand_copies": "drop",
        }],
    }
    expected_chain = "B"
    if from_boltz:
        poses = boltz_pose_result(tmp_path)
        campaign["complexes"] = list(OpenFETool.complexes_from_boltz(characterization_request(), poses).values())
        from pathlib import Path

        structure = Path(campaign["complexes"][0]["structure"])
        expected_chain = "Z"
    tool = make_tool()
    root = tmp_path / "campaigns"
    with TestClient(service.create_app(ServiceSettings(root=root))) as client:
        def send(method, url, **kwargs):
            prepared = requests.Request(
                method, url, headers=kwargs["headers"], data=kwargs["data"], files=kwargs["files"],
            ).prepare()
            return client.request(method, "/campaigns/upload", headers=dict(prepared.headers), content=prepared.body)

        tool.session.request.side_effect = send
        assert tool.upload_campaign(campaign)["name"] == "roundtrip"
    stored = Campaign.open(root / "roundtrip").manifest.request.complexes[0]
    assert stored.structure.read_bytes() == structure.read_bytes()
    assert stored.ligand.smiles == "CCO"
    assert stored.ligand.selector.chain == expected_chain


def test_slurm_submission_and_partial_repeats_are_not_predictions():
    tool = make_tool()
    complete = {"ligand": "mol_1", "dg": -8.2, "repeats": 3}
    tool.status = Mock(return_value=summary(runs=[{"name": "mol_1", "state": "submitted"}]))
    tool.results = Mock(side_effect=[[], [dict(complete, repeats=1)], [complete]])
    with patch("tools.openfe_tool.time.sleep"):
        assert tool.wait_for_results("test", {"mol_1": 3}) == [complete]
    assert tool.results.call_count == 3


@pytest.mark.parametrize("payload", [
    summary(status="failed"),
    summary("prep", runs=[{"name": "mol_1", "state": "failed"}]),
])
def test_background_and_per_run_failures_raise(payload):
    tool = make_tool()
    tool.status = Mock(return_value=payload)
    with pytest.raises(OpenFEError, match="failed"):
        tool.wait_for_operation("test", "prep")


def test_stale_operation_does_not_finish_new_stage():
    tool = make_tool()
    tool.status = Mock(side_effect=[summary("prep"), summary("plan")])
    with patch("tools.openfe_tool.time.sleep"):
        assert tool.wait_for_operation("test", "plan") == summary("plan")
    assert tool.status.call_count == 2


def test_timeout_leaves_remote_work_intact():
    tool = make_tool(wait_timeout=1)
    tool.status = Mock(return_value=summary(status="running"))
    with patch("tools.openfe_tool.time.monotonic", side_effect=[0, 2]):
        with pytest.raises(OpenFETimeout, match="not cancelled"):
            tool.wait_for_results("test", {"mol_1": 3})
    tool.session.request.assert_not_called()


def test_authentication_and_http_errors_do_not_retry_posts():
    tool = make_tool(api_token="test-token")
    tool.session.request.return_value.status_code = 409
    with pytest.raises(OpenFEError, match="409"):
        tool.create_campaign({"name": "test"})
    assert tool.session.request.call_count == 1
    assert tool.session.request.call_args.kwargs["headers"]["Authorization"] == "Bearer test-token"
    assert tool.session.request.call_args.kwargs["allow_redirects"] is False


def test_transport_and_invalid_json_errors():
    tool = make_tool()
    tool.session.request.side_effect = requests.Timeout()
    with pytest.raises(OpenFEError, match="check campaign status"):
        tool.health()
    tool.session.request.side_effect = None
    tool.session.request.return_value.status_code = 200
    tool.session.request.return_value.json.side_effect = ValueError()
    with pytest.raises(OpenFEError, match="invalid JSON"):
        tool.health()


@pytest.mark.parametrize("name", ["../test", "test/name", "", "test?force=true"])
def test_campaign_names_cannot_change_endpoint(name):
    tool = make_tool()
    with pytest.raises(ValueError):
        tool.status(name)
    tool.session.request.assert_not_called()


def characterization_request(**options):
    return CharacterizationRequest(
        smiles={"mol_1": "CCO"}, search_space={"mol_1": "CCO"}, molecule_ids=["mol_1"],
        properties=["openfe_binding_free_energy"],
        tool_options={
            "complexes": {"mol_1": {
                "name": "complex_1", "structure": "/data/complex.cif",
                "ligand": {"name": "ethanol", "smiles": "CCO", "selector": {"chain": "B"}},
                "extra_ligand_copies": "drop",
            }},
            **options,
        },
    )


def prediction(**values):
    return {"ligand": "ethanol", "dg": -8.2, "uncertainty": 0.3,
            "uncertainty_kind": "SEM", "repeats": 3, "quality": "unknown", "checks": [], **values}


def test_characterize_maps_ligands_preserves_units_and_quality():
    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_results = Mock(return_value=[prediction()])
    request = characterization_request(campaign_name="test")
    result = tool.characterize(request)
    assert result.results == {"mol_1": {"openfe_binding_free_energy": -8.2}}
    assert result.metadata["openfe"]["mol_1"]["uncertainty"] == 0.3
    assert result.metadata["openfe"]["mol_1"]["quality"] == "unknown"
    assert result.metadata["openfe"]["mol_1"]["units"] == "kcal/mol"
    tool.wait_for_results.assert_called_once_with("test", {"ethanol": 3})
    assert tool.submit_campaign.call_args.args[0]["neutralize_ligands"] is False
    assert request.tool_options["complexes"]["mol_1"]["name"] == "complex_1"


def test_resume_never_submits_again():
    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_results = Mock(return_value=[prediction()])
    tool.characterize(characterization_request(campaign_name="test", resume=True))
    tool.submit_campaign.assert_not_called()


@pytest.mark.parametrize("values", [{"quality": "fail"}, {"dg": float("nan")}, {"uncertainty": -1}])
def test_unusable_predictions_are_not_measurements(values):
    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_results = Mock(return_value=[prediction(**values)])
    with pytest.raises(OpenFEError):
        tool.characterize(characterization_request())


@pytest.mark.parametrize("options", [{"complexes": {}}, {"resume": True}, {"execution": {"repeats": 0}}])
def test_invalid_characterization_does_not_submit(options):
    tool = make_tool()
    with pytest.raises(OpenFEError):
        tool.characterize(characterization_request(**options))
    tool.session.request.assert_not_called()


def test_mismatched_smiles_does_not_submit():
    tool = make_tool()
    request = characterization_request()
    request.search_space["mol_1"] = "CCC"
    with pytest.raises(OpenFEError, match="SMILES must match"):
        tool.characterize(request)
    tool.session.request.assert_not_called()


def test_run_request_preserves_openfe_configuration():
    from server.schemas.run import RunCreateRequest

    options = characterization_request().tool_options
    payload = RunCreateRequest.model_validate({
        "prompt": "minimize openfe_binding_free_energy",
        "characterization": {"openfe": options},
    })
    assert payload.model_dump()["characterization"]["openfe"] == options


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("OPENFE_LIVE_TEST") != "1", reason="Hosted OpenFE test is opt-in")
def test_hosted_openfe_read_only():
    tool = OpenFETool()
    assert tool.health()["status"] == "ok"
    assert isinstance(tool.list_campaigns(), list)