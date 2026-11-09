"""Offline contracts for protocol routing, network results, and stable references."""

from copy import deepcopy
import json
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from schemas.openfe import OPENFE_PROPERTIES, prompt_protocol, route_openfe_targets
from schemas.state import TargetProperty, WorkflowState
from schemas.tool_schemas import CharacterizationRequest, CharacterizationResult
from tools.openfe_tool import OpenFEError, OpenFETool


def make_tool():
    return OpenFETool(base_url="https://openfe.example", session=Mock())


def request_for(protocol_name="rbfe", **options):
    return CharacterizationRequest(
        smiles={"ref": "CCO", "candidate": "CCCO"},
        search_space={"ref": "CCO", "candidate": "CCCO"},
        molecule_ids=["ref", "candidate"], properties=[OPENFE_PROPERTIES[protocol_name]],
        tool_options={
            "reference": "ref", "campaign_name": "series",
            "ligands": [{"name": "ref", "smiles": "CCO", "structure": "bound.cif", "selector": {"chain": "B"}},
                        {"name": "candidate", "smiles": "CCCO"}],
            **options,
        },
    )


def network_result():
    return {
        "edges": [{"edge": "ref_candidate", "ligand_a": "ref", "ligand_b": "candidate",
                   "ddg": -2.0, "uncertainty": 0.2, "uncertainty_kind": "sem",
                   "quality": "unknown", "note": None}],
        "ligands": [{"ligand": "ref", "dg_relative": 1.0, "uncertainty": 0.1, "edges": 1},
                    {"ligand": "candidate", "dg_relative": -1.0, "uncertainty": 0.1, "edges": 1}],
        "cycles": [], "unreachable": [],
        "checks": [{"name": "cycle_closure", "status": "unknown", "detail": "No cycles"}],
    }


def status(state="done", repeats=3, protocol="rbfe"):
    return {"protocol": protocol, "last_operation": {"operation": "submit", "status": "done"},
            "runs": [{"name": "ref_candidate", "state": state, "repeats": repeats}]}


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_native_payload_and_reference_rebasing(protocol):
    tool = make_tool()
    tool.submit_campaign = Mock()
    network = network_result()
    tool.wait_for_network = Mock(return_value=network)
    result = tool.characterize(request_for(protocol))
    payload = tool.submit_campaign.call_args.args[0]
    assert payload["protocol"] == protocol
    assert payload["network"] == {"method": "radial", "central_ligand": "ref"}
    assert payload["alignment"] == {"pose": "mcs"}
    assert "neutralize_ligands" not in payload and "complexes" not in payload
    assert result.results == {"ref": {OPENFE_PROPERTIES[protocol]: 0.0},
                              "candidate": {OPENFE_PROPERTIES[protocol]: -2.0}}
    assert result.metadata["network"] == network
    assert result.metadata["openfe"]["candidate"]["uncertainty_scope"] == "network_mean_fit_not_reference_difference"
    assert result.metadata["openfe"]["candidate"]["uncertainty"] == 0.1


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
@pytest.mark.parametrize("environment,expected", [
    (None, "local"), ("", "local"), ("   ", "local"),
    ("local", "local"), (" gpu-profile ", "gpu-profile"),
])
def test_network_execution_profile_comes_only_from_environment(protocol, environment, expected, monkeypatch):
    monkeypatch.delenv("OPENFE_EXECUTION_PROFILE", raising=False)
    if environment is not None:
        monkeypatch.setenv("OPENFE_EXECUTION_PROFILE", environment)
    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_network = Mock(return_value=network_result())
    request = request_for(protocol, execution={"profile": "ignored-per-run", "repeats": 5})
    original = deepcopy(request.tool_options)
    tool.characterize(request)
    assert tool.submit_campaign.call_args.args[0]["execution"] == {"profile": expected, "repeats": 5}
    assert request.tool_options == original
    tool.wait_for_network.assert_called_once_with("series", protocol=protocol, expected_repeats=5)


@pytest.mark.parametrize("alignment", [{}, {"n_conformers": 25}, {"pose": "keep", "n_conformers": 25}])
@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_network_pose_defaults_preserve_explicit_overrides(protocol, alignment):
    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_network = Mock(return_value=network_result())
    request = request_for(protocol, alignment=alignment)
    request.tool_options["ligands"][1].update(pose="keep", path="candidate.sdf")
    original = deepcopy(request.tool_options)
    tool.characterize(request)
    campaign = tool.submit_campaign.call_args.args[0]
    assert campaign["alignment"] == {"pose": "mcs", **alignment}
    assert campaign["ligands"] == original["ligands"]
    assert request.tool_options == original


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_network_with_no_execution_options_submits_local_profile(protocol, monkeypatch):
    monkeypatch.delenv("OPENFE_EXECUTION_PROFILE", raising=False)
    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_network = Mock(return_value=network_result())
    tool.characterize(request_for(protocol))
    assert tool.submit_campaign.call_args.args[0]["execution"] == {"profile": "local"}


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_payload_validates_against_updated_reference_schema(protocol, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "reference_code" / "src"
    if not source.is_dir() or sys.version_info < (3, 12):
        pytest.skip("Optional updated OpenFE reference source is not installed")
    monkeypatch.syspath_prepend(str(source))
    validate_request = pytest.importorskip("openfe_api.schema.request").validate_request

    tool = make_tool()
    tool.submit_campaign = Mock()
    tool.wait_for_network = Mock(return_value=network_result())
    tool.characterize(request_for(protocol))
    native = validate_request(tool.submit_campaign.call_args.args[0])
    assert native.protocol == protocol
    assert native.reference == "ref"


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_series_multipart_roundtrip_with_reference_service(protocol, tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[2] / "reference_code" / "src"
    if not source.is_dir() or sys.version_info < (3, 12):
        pytest.skip("Optional updated OpenFE reference source is not installed")
    monkeypatch.syspath_prepend(str(source))
    service = pytest.importorskip("openfe_api.service")
    from fastapi.testclient import TestClient
    from openfe_api.campaign import Campaign
    from openfe_api.config import ServiceSettings
    import requests

    protein, ligand = tmp_path / "protein.pdb", tmp_path / "reference.mol2"
    protein.write_bytes(b"protein coordinates")
    ligand.write_bytes(b"ligand coordinates")
    campaign = {"name": "roundtrip", "protocol": protocol, "reference": "ref",
                "protein": {"path": str(protein)},
                "ligands": [{"name": "ref", "smiles": "CCO", "path": str(ligand)},
                            {"name": "candidate", "smiles": "CCCO"}],
                "network": {"method": "radial", "central_ligand": "ref"}}
    tool = make_tool()
    root = tmp_path / "campaigns"
    with TestClient(service.create_app(ServiceSettings(root=root))) as client:
        def send(method, url, **kwargs):
            prepared = requests.Request(method, url, headers=kwargs["headers"],
                                        data=kwargs["data"], files=kwargs["files"]).prepare()
            return client.request(method, "/campaigns/upload", headers=dict(prepared.headers), content=prepared.body)
        tool.session.request.side_effect = send
        assert tool.upload_campaign(campaign)["name"] == "roundtrip"
    stored = Campaign.open(root / "roundtrip").manifest.request
    assert stored.protocol == protocol
    assert stored.protein.path.read_bytes() == protein.read_bytes()
    assert stored.reference_ligand().path.read_bytes() == ligand.read_bytes()
    assert stored.ligands[1].smiles == "CCCO"


def test_gathered_fit_waits_for_all_repeats_and_refreshes_status():
    tool = make_tool()
    tool.status = Mock(side_effect=[status("running"), status("running"), status("running"), status()])
    tool.network = Mock(return_value=network_result())
    tool._pause = Mock()
    assert tool.wait_for_network("series", protocol="rbfe") == network_result()
    assert tool.network.call_count == 2
    assert tool.status.call_count == 4
    tool._pause.assert_called_once()


@pytest.mark.parametrize("summary,match", [
    (status(protocol="septop"), "protocol"),
    (status(state="failed"), "runs failed"),
    (status(repeats=1), "fewer repeats"),
    ({**status(), "runs": []}, "no network edges"),
    ({**status(), "runs": [{"name": "missing", "state": "done", "repeats": 3}]}, "every planned edge"),
])
def test_network_completion_rejects_invalid_campaign(summary, match):
    tool = make_tool()
    tool.status = Mock(return_value=summary)
    tool.network = Mock(return_value=network_result())
    with pytest.raises(OpenFEError, match=match):
        tool.wait_for_network("series", protocol="rbfe")


@pytest.mark.parametrize("collection,field,value", [
    ("edges", "ddg", float("nan")), ("edges", "uncertainty", -1),
    ("ligands", "dg_relative", float("inf")), ("ligands", "uncertainty", None),
    ("edges", "ligand_b", "ref"),
])
def test_network_rejects_invalid_values(collection, field, value):
    network = network_result()
    network[collection][0][field] = value
    with pytest.raises(OpenFEError):
        OpenFETool._validate_network_values(network)


def test_network_rejects_duplicate_fit_names():
    network = network_result()
    network["ligands"].append(deepcopy(network["ligands"][0]))
    with pytest.raises(OpenFEError, match="duplicate"):
        OpenFETool._validate_network_values(network)


@pytest.mark.parametrize("failure", ["quality", "checks", "dropped", "disconnected"])
def test_no_silent_partial_or_failed_quality_batch(failure):
    tool = make_tool()
    network = network_result()
    if failure == "quality":
        network["edges"][0]["quality"] = "fail"
    elif failure == "checks":
        network["checks"][0]["status"] = "fail"
    elif failure == "dropped":
        network["ligands"].pop()
        network["unreachable"] = ["candidate"]
    else:
        network["edges"] = []
    tool.wait_for_network = Mock(return_value=network)
    tool.submit_campaign = Mock()
    with pytest.raises(OpenFEError, match="quality|disconnected"):
        tool.characterize(request_for(resume=True, allow_failed_quality=False))
    tool.submit_campaign.assert_not_called()


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_failed_quality_error_identifies_edges_checks_and_resume(protocol):
    tool = make_tool()
    network = network_result()
    network["edges"][0].update(quality="fail", note="repeat spread exceeds threshold")
    network["checks"] = [{"name": "cycle_closure", "status": "fail", "detail": "closure is 4 kcal/mol"}]
    tool.wait_for_network = Mock(return_value=network)
    tool.submit_campaign = Mock()
    with pytest.raises(OpenFEError) as caught:
        tool.characterize(request_for(protocol, resume=True, allow_failed_quality=False))
    message = str(caught.value)
    for detail in ("after execution completed", "ref_candidate", "repeat spread exceeds threshold",
                   "cycle_closure", "closure is 4 kcal/mol", "/campaigns/series/network",
                   "resume=true", "allow_failed_quality=true"):
        assert detail in message
    tool.submit_campaign.assert_not_called()


def test_failed_edge_without_details_does_not_invent_cause():
    network = network_result()
    network["edges"][0]["quality"] = "fail"
    assert OpenFETool._network_quality_failures(network) == [
        "edge 'ref_candidate' failed quality (service did not provide per-edge check details)"
    ]


def test_optional_edge_checks_include_failed_details_only():
    network = network_result()
    network["edges"][0]["checks"] = [
        {"name": "mbar_overlap", "status": "fail", "detail": "overlap 0.002"},
        {"name": "repeat_spread", "status": "pass", "detail": "spread 0.1"},
    ]
    assert OpenFETool._network_quality_failures(network) == [
        "edge 'ref_candidate' failed quality (mbar_overlap: overlap 0.002)"
    ]


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_failed_quality_override_preserves_verdict(protocol):
    tool = make_tool()
    network = network_result()
    network["edges"][0]["quality"] = "fail"
    network["checks"] = [{"name": "cycle_closure", "status": "fail", "detail": "closure is 4 kcal/mol"}]
    original = deepcopy(network)
    tool.wait_for_network = Mock(return_value=network)
    tool.submit_campaign = Mock()
    result = tool.characterize(request_for(protocol, resume=True, allow_failed_quality=True))
    assert result.metadata["network"] == original
    assert len(result.metadata["quality_warnings"]) == 2
    assert "ref_candidate" in result.metadata["quality_warnings"][0]
    assert "cycle_closure" in result.metadata["quality_warnings"][1]
    assert result.results["candidate"][OPENFE_PROPERTIES[protocol]] == -2.0
    tool.submit_campaign.assert_not_called()


@pytest.mark.parametrize("quality", ["pass", "unknown"])
def test_acceptable_network_does_not_add_quality_warnings(quality):
    tool = make_tool()
    network = network_result()
    network["edges"][0]["quality"] = quality
    network["edges"][0]["checks"] = None
    tool.wait_for_network = Mock(return_value=network)
    result = tool.characterize(request_for(resume=True))
    assert result.metadata["quality_warnings"] == []


@pytest.mark.parametrize("failure", ["disconnected", "invalid", "incomplete", "execution"])
def test_quality_override_does_not_bypass_other_validation(failure):
    tool = make_tool()
    network = network_result()
    network["edges"][0]["quality"] = "fail"
    summary = status()
    if failure == "disconnected":
        network["unreachable"] = ["candidate"]
    elif failure == "invalid":
        network["edges"][0]["ddg"] = float("nan")
    elif failure == "incomplete":
        summary = status(repeats=1)
    else:
        summary = status(state="failed")
    tool.status = Mock(return_value=summary)
    tool.network = Mock(return_value=network)
    with pytest.raises(OpenFEError, match="disconnected|invalid|fewer repeats|runs failed"):
        tool.characterize(request_for(resume=True, allow_failed_quality=True))


@pytest.mark.parametrize("failed_quality", [False, True])
def test_workflow_preserves_quality_warnings_and_emits_warning(failed_quality, monkeypatch):
    import nodes.characterize_molecules as node

    request = request_for(resume=True, allow_failed_quality=True)
    tool = make_tool()
    network = network_result()
    if failed_quality:
        network["edges"][0]["quality"] = "fail"
    tool.wait_for_network = Mock(return_value=network)
    state = WorkflowState(
        user_prompt="RBFE", current_bo_recommendations=["candidate"],
        search_space={"candidate": "CCCO"},
        targets=[TargetProperty(name=OPENFE_PROPERTIES["rbfe"])],
        characterization_config={"tool_ids": ["openfe"]},
    )
    monkeypatch.setattr(node, "_build_characterization_request", Mock(return_value=request))
    monkeypatch.setattr(node, "_create_characterization_tool", Mock(return_value=tool))
    node.characterize_molecules_node(state)
    record = next(run for run in state.tool_runs if run.tool_id == "openfe")
    assert record.status == "completed"
    warnings = record.metadata["result_metadata"]["quality_warnings"]
    assert bool(warnings) == failed_quality
    events = [entry["data"] for entry in state.logs if entry["action"] == "openfe_quality_warning"]
    assert len(events) == int(failed_quality)
    if failed_quality:
        assert events[0]["severity"] == "warning"
        assert events[0]["data"]["campaign_name"] == "series"
        assert events[0]["data"]["warnings"] == warnings


def test_upload_series_traverses_all_inputs_and_deduplicates(tmp_path):
    paths = {}
    for name in ["protein.pdb", "reference.mol2", "candidate.sdf", "cofactor.mol"]:
        paths[name] = tmp_path / name
        paths[name].write_bytes(name.encode())
    campaign = {"name": "series", "protocol": "septop", "reference": "ref",
                "protein": {"path": str(paths["protein.pdb"])},
                "cofactors": [{"path": str(paths["cofactor.mol"])}],
                "ligands": [{"name": "ref", "path": str(paths["reference.mol2"])},
                            {"name": "candidate", "path": str(paths["candidate.sdf"])},
                            {"name": "third", "structure": str(paths["protein.pdb"])}]}
    original = deepcopy(campaign)
    handles = []

    def receive(method, url, **kwargs):
        payload = json.loads(kwargs["data"]["campaign"])
        filenames = {part[1][0] for part in kwargs["files"]}
        referenced = {payload["protein"]["path"], payload["cofactors"][0]["path"],
                      *(ligand.get("path") or ligand["structure"] for ligand in payload["ligands"])}
        assert filenames == referenced and len(filenames) == 4
        assert any(name.endswith(".mol2") for name in filenames)
        handles.extend(part[1][1] for part in kwargs["files"])
        return Mock(status_code=201, json=Mock(return_value={"name": "series"}))

    tool = make_tool()
    tool.session.request.side_effect = receive
    tool.upload_campaign(campaign)
    assert all(handle.closed for handle in handles)
    assert campaign == original


@pytest.mark.parametrize("prompt,protocol", [
    ("ABFE", "abfe"), ("absolute binding free energy", "abfe"),
    ("RBFE", "rbfe"), ("relative binding free energy", "rbfe"),
    ("SepTop relative binding free energy", "septop"), ("separated topologies", "septop"),
    ("sep-top", "septop"), ("openfe_septop", "septop"),
])
def test_prompt_protocol_keywords(prompt, protocol):
    assert prompt_protocol(prompt) == protocol
    targets = route_openfe_targets([
        {"property_name": "binding_affinity"}, {"property_name": "openfe_absolute_binding_free_energy"},
        {"property_name": "qed", "optimization_mode": "MAX"},
    ], prompt)
    assert [target["property_name"] for target in targets] == ["qed", OPENFE_PROPERTIES[protocol]]
    assert targets[-1]["optimization_mode"] == "MIN"


@pytest.mark.parametrize("protocol", ["abfe", "rbfe", "septop"])
@pytest.mark.parametrize("use_llm", [False, True])
def test_hybrid_routes_protocol_with_or_without_llm(protocol, use_llm):
    from nodes.argument_extraction.hybrid import HybridArgumentExtractor

    extractor = HybridArgumentExtractor()
    extractor._resolve_molecule_name = Mock(return_value=None)
    extractor.extract_with_llm = Mock(return_value={
        "starting_molecules": ["CCCO"], "confidence_score": 0.95,
        "target_properties": [{"property_name": "binding_affinity", "optimization_mode": "MAX"},
                              {"property_name": "qed", "optimization_mode": "MAX"}],
    } if use_llm else None)
    result = extractor.extract_dict(f"Optimize CCCO using {protocol} and maximize QED")
    names = {target["property_name"] for target in result["target_properties"]}
    assert names == {OPENFE_PROPERTIES[protocol], "qed"}


def test_explicit_separate_affinity_and_conflicting_protocols():
    targets = route_openfe_targets([{"property_name": "binding_affinity"}], "SepTop and binding affinity")
    assert len(targets) == 2
    with pytest.raises(ValueError, match="one OpenFE protocol"):
        prompt_protocol("ABFE and RBFE")


def build(state, smiles):
    from nodes.characterize_molecules import _build_characterization_request
    return _build_characterization_request("openfe", state, list(smiles), smiles, smiles, [], {})


def test_reference_is_stable_across_batches_and_starting_aliases():
    state = WorkflowState(user_prompt="RBFE", starting_molecules=["CCO"],
                          search_space={"library_ref": "CCO"},
                          targets=[TargetProperty(name="openfe_relative_binding_free_energy")])
    first = build(state, {"candidate": "CCCO", "baseline": "CCO"})
    assert first.tool_options["reference"] == "baseline"
    second = build(state, {"next": "CCCCO"})
    assert second.tool_options["reference"] == "baseline"
    assert second.search_space == {"next": "CCCCO", "baseline": "CCO"}
    alias = build(state, {"library_ref": "CCO"})
    assert alias.molecule_ids == ["baseline"]
    assert alias.tool_options["reference_aliases"] == ["library_ref"]


def test_reference_only_uses_zero_without_boltz_or_remote_work():
    from nodes.characterize_molecules import _run_openfe_with_boltz
    state = WorkflowState(user_prompt="RBFE", starting_molecules=["CCO"],
                          targets=[TargetProperty(name="openfe_relative_binding_free_energy")])
    request = build(state, {"ref": "CCO"})
    tool, registry = make_tool(), Mock()
    result = _run_openfe_with_boltz(state, request, registry, tool)
    assert result.results == {"ref": {"openfe_relative_binding_free_energy": 0.0}}
    assert result.metadata["reference_only"] is True
    registry.get.assert_not_called()
    tool.session.request.assert_not_called()


def test_series_rejects_out_of_batch_edges_before_pose_generation():
    state = WorkflowState(user_prompt="RBFE", starting_molecules=["CCO"],
                          targets=[TargetProperty(name="openfe_relative_binding_free_energy")],
                          characterization_config={"openfe": {"network": {
                              "method": "explicit", "edges": [["candidate", "absent"]]}}})
    with pytest.raises(OpenFEError, match="current batch"):
        build(state, {"ref": "CCO", "candidate": "CCCO"})


@pytest.mark.parametrize("alignment", [{}, {"n_conformers": 25}, {"pose": "keep", "n_conformers": 25}])
@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
def test_boltz_reference_pose_is_reused(protocol, alignment, tmp_path, monkeypatch):
    import nodes.characterize_molecules as node
    from schemas.state import ProteinTarget

    state = WorkflowState(user_prompt=protocol, starting_molecules=["CCO"],
                          targets=[TargetProperty(name=OPENFE_PROPERTIES[protocol])],
                          characterization_config={"openfe": {"alignment": deepcopy(alignment)}},
                          protein_targets=[ProteinTarget(chain_id="A", sequence="AAAA")])
    tool = make_tool()
    tool.ensure_upload_support = Mock()
    tool.characterize = Mock(return_value=CharacterizationResult(results={}))
    monkeypatch.setattr(node, "_create_characterization_tool", Mock())
    monkeypatch.setattr(node, "_boltz_tool_options", Mock(return_value={}))
    runner = Mock(return_value=CharacterizationResult(results={}))
    monkeypatch.setattr(node, "_run_characterization_tool", runner)

    def complexes(request, result):
        built = {}
        for name in request.molecule_ids:
            path = tmp_path / f"{name}.cif"
            path.write_text("data_test")
            built[name] = {"name": name, "structure": str(path), "protein": {"chains": ["A"]},
                           "ligand": {"smiles": request.search_space[name], "selector": {"chain": "B"}},
                           "extra_ligand_copies": "drop"}
        return built

    tool.complexes_from_boltz = Mock(side_effect=complexes)
    node._run_openfe_with_boltz(state, build(state, {"ref": "CCO", "candidate": "CCCO"}), Mock(), tool)
    reference_path = state.characterization_config["openfe"]["reference_complex"]["structure"]
    node._run_openfe_with_boltz(state, build(state, {"next": "CCCCO"}), Mock(), tool)
    assert runner.call_args.args[2].molecule_ids == ["next"]
    options = tool.characterize.call_args.args[0].tool_options
    assert options["alignment"] == {"pose": "mcs", **alignment}
    assert state.characterization_config["openfe"]["alignment"] == alignment
    assert options["protein"] == {"chains": ["A"]}
    assert next(ligand for ligand in options["ligands"] if ligand["name"] == "ref")["structure"] == reference_path
    assert "extra_ligand_copies" not in options["ligands"][0]


def test_network_boltz_resume_does_not_regenerate_poses():
    from nodes.characterize_molecules import _run_openfe_with_boltz
    state = WorkflowState(user_prompt="RBFE", starting_molecules=["CCO"],
                          targets=[TargetProperty(name="openfe_relative_binding_free_energy")],
                          characterization_config={"openfe": {"resume": True, "campaign_name": "series"}})
    tool, registry = make_tool(), Mock()
    tool.wait_for_network = Mock(return_value=network_result())
    result = _run_openfe_with_boltz(state, build(state, {"ref": "CCO", "candidate": "CCCO"}), registry, tool)
    assert result.results["candidate"]["openfe_relative_binding_free_energy"] == -2.0
    registry.get.assert_not_called()
    tool.session.request.assert_not_called()


@pytest.mark.parametrize("prompt,protocol,expected", [
    ("binding free energy", "rbfe", "rbfe"), ("SepTop", "septop", "septop"),
    ("relative binding free energy", "septop", "septop"),
    ("RBFE", None, "rbfe"), ("SepTop", None, "septop"), ("ABFE", None, "abfe"),
    ("ABFE", "rbfe", None),
])
def test_configured_protocol_reaches_workflow_targets(prompt, protocol, expected, monkeypatch):
    import nodes.argument_extraction.hybrid as hybrid
    from schemas.tool_schemas import ArgumentExtractionResult

    monkeypatch.setattr(hybrid, "_create_argument_extractor_tool", Mock())
    monkeypatch.setattr(hybrid, "_run_argument_extractor_tool", Mock(return_value=ArgumentExtractionResult(
        parsed_arguments={"proteins": [{"chain_id": "A", "sequence": "AAAA"}],
                          "target_properties": [{"property_name": "openfe_absolute_binding_free_energy",
                                                 "optimization_mode": "MIN"}]},
    )))
    state = WorkflowState(user_prompt=prompt, characterization_config={"openfe": {"protocol": protocol}})
    if expected is None:
        with pytest.raises(ValueError, match="must agree"):
            hybrid.extract_arguments_node(state)
    else:
        hybrid.extract_arguments_node(state)
        assert [target.name for target in state.targets] == [OPENFE_PROPERTIES[expected]]


@pytest.mark.parametrize("protocol,options", [("rbfe", {"screening": {}}),
                                               ("septop", {"charges": {}}),
                                               ("rbfe", {"protocol": "abfe"})])
def test_protocol_options_must_agree(protocol, options):
    with pytest.raises(OpenFEError):
        make_tool().characterize(request_for(protocol, **options))


def partial_request(protocol="rbfe", **options):
    request = request_for(protocol, resume=True, **options)
    request.molecule_ids.append("dropped")
    request.search_space["dropped"] = "CCCCO"
    request.tool_options["ligands"].append({"name": "dropped", "smiles": "CCCCO"})
    return request


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
@pytest.mark.parametrize("failure", ["missing", "disconnected", "unreachable"])
def test_partial_network_preserves_valid_results_and_reports_exclusions(protocol, failure):
    tool = make_tool()
    network = network_result()
    if failure != "missing":
        network["ligands"].append({"ligand": "dropped", "dg_relative": -100.0, "uncertainty": 0.1})
    if failure == "unreachable":
        network["unreachable"] = ["dropped"]
    tool.wait_for_network = Mock(return_value=network)
    tool.submit_campaign = Mock()
    result = tool.characterize(partial_request(protocol, reference_aliases=["alias"]))
    assert result.failed_molecules == ["dropped"]
    assert "reference 'ref'" in result.metadata["failed_molecules"]["dropped"]
    assert result.results == {"ref": {OPENFE_PROPERTIES[protocol]: 0.0},
                              "candidate": {OPENFE_PROPERTIES[protocol]: -2.0},
                              "alias": {OPENFE_PROPERTIES[protocol]: 0.0}}
    assert "dropped" not in result.metadata["openfe"]
    assert result.metadata["network"] == network
    tool.submit_campaign.assert_not_called()


@pytest.mark.parametrize("failure", ["missing", "unreachable"])
def test_partial_network_requires_reference(failure):
    tool = make_tool()
    network = network_result()
    if failure == "missing":
        network["ligands"].pop(0)
    else:
        network["unreachable"] = ["ref"]
    tool.wait_for_network = Mock(return_value=network)
    with pytest.raises(OpenFEError, match="reference 'ref'.*missing or disconnected"):
        tool.characterize(partial_request())


@pytest.mark.parametrize("protocol", ["rbfe", "septop"])
@pytest.mark.parametrize("include_failed_edge", [False, True])
def test_failed_network_run_does_not_discard_completed_edges(protocol, include_failed_edge):
    tool = make_tool()
    summary = status(protocol=protocol)
    summary["runs"].append({"name": "ref_dropped", "state": "failed", "repeats": 3})
    network = network_result()
    if include_failed_edge:
        network["edges"].append({"edge": "ref_dropped", "ligand_a": "ref", "ligand_b": "dropped",
                                 "ddg": None, "uncertainty": None, "quality": "fail"})
    original = deepcopy(network)
    tool.status = Mock(return_value=summary)
    tool.network = Mock(return_value=network)
    result = tool.characterize(partial_request(protocol))
    assert result.failed_molecules == ["dropped"]
    assert result.results["candidate"][OPENFE_PROPERTIES[protocol]] == -2.0
    assert result.metadata["network"]["failed_runs"] == ["ref_dropped"]
    assert len(result.metadata["network"]["edges"]) == 1
    assert network == original


def test_partial_batch_still_waits_for_other_running_edges():
    tool = make_tool()
    running = status(state="running")
    running["runs"].append({"name": "ref_dropped", "state": "failed", "repeats": 3})
    done = deepcopy(running)
    done["runs"][0]["state"] = "done"
    tool.status = Mock(side_effect=[running, running, running, done])
    tool.network = Mock(return_value=network_result())
    tool._pause = Mock()
    result = tool.characterize(partial_request())
    assert result.failed_molecules == ["dropped"]
    tool._pause.assert_called_once()


def test_failed_edge_partial_estimate_cannot_contaminate_accepted_fit():
    tool = make_tool()
    summary = status()
    summary["runs"].append({"name": "ref_dropped", "state": "failed", "repeats": 3})
    network = network_result()
    network["edges"].append({"edge": "ref_dropped", "ligand_a": "ref", "ligand_b": "dropped",
                             "ddg": -100.0, "uncertainty": 0.1})
    tool.status = Mock(return_value=summary)
    tool.network = Mock(return_value=network)
    with pytest.raises(OpenFEError, match="partial estimates"):
        tool.characterize(partial_request())


def test_campaign_operation_failure_is_still_fatal():
    tool = make_tool()
    summary = status()
    summary["last_operation"]["status"] = "failed"
    tool.status = Mock(return_value=summary)
    with pytest.raises(OpenFEError, match="submit failed"):
        tool.characterize(partial_request())


@pytest.mark.parametrize("tool_order", [["openfe", "rdkit"], ["rdkit", "openfe"]])
def test_partial_workflow_excludes_descriptor_only_rows_and_future_recommendations(tool_order, monkeypatch):
    import nodes.characterize_molecules as node
    import nodes.bo_iteration as bo
    from schemas.tool_schemas import BORecommendationResult

    request = partial_request()
    tool = make_tool()
    tool.wait_for_network = Mock(return_value=network_result())
    openfe_result = tool.characterize(request)
    descriptor_result = CharacterizationResult(results={"candidate": {"qed": 0.5}, "dropped": {"qed": 0.9}})
    state = WorkflowState(
        user_prompt="RBFE and QED", current_bo_recommendations=["candidate", "dropped"],
        search_space={"candidate": "CCCO", "dropped": "CCCCO", "next": "CCCCCO", "duplicate": "CCCCO"},
        targets=[TargetProperty(name=OPENFE_PROPERTIES["rbfe"]), TargetProperty(name="qed")],
        characterization_config={"tool_ids": tool_order},
    )
    monkeypatch.setattr(node, "_build_characterization_request", Mock(return_value=request))
    monkeypatch.setattr(node, "_create_characterization_tool", Mock())
    monkeypatch.setattr(node, "_run_characterization_tool",
                        lambda spec, tool, request: openfe_result if spec.id == "openfe" else descriptor_result)
    node.characterize_molecules_node(state)
    assert [row.molecule_id for row in state.experimental_results] == ["candidate"]
    assert state.experimental_results[0].properties["qed"] == 0.5
    assert state.current_bo_recommendations == ["candidate"]
    record = next(run for run in state.tool_runs if run.tool_id == "openfe")
    assert record.outputs["failed_molecule_count"] == 1
    assert record.metadata["result_metadata"]["failed_molecules"]["dropped"]
    events = [entry["data"] for entry in state.logs if entry["action"] == "openfe_partial_batch"]
    assert len(events) == 1
    assert events[0]["severity"] == "warning"
    assert events[0]["data"]["failed_molecules"]["dropped"]["campaign_name"] == "series"

    state = WorkflowState.model_validate(state.model_dump())
    optimizer = Mock(return_value=BORecommendationResult(recommended_ids=["dropped", "duplicate", "next"]))
    monkeypatch.setattr(bo, "_run_optimizer_tool", optimizer)
    bo.bo_iteration_node(state)
    assert optimizer.call_args.args[2].search_space == {"candidate": "CCCO", "next": "CCCCCO"}
    assert state.current_bo_recommendations == ["next"]
    assert "dropped" in state.search_space


def test_bo_finishes_when_every_molecule_was_excluded(monkeypatch):
    import nodes.bo_iteration as bo

    state = WorkflowState(user_prompt="RBFE", search_space={"dropped": "CCCCO"},
                          targets=[TargetProperty(name=OPENFE_PROPERTIES["rbfe"])],
                          profiling={"openfe_failed_molecules": {"dropped": {"smiles": "CCCCO"}}})
    optimizer = Mock()
    monkeypatch.setattr(bo, "_run_optimizer_tool", optimizer)
    bo.bo_iteration_node(state)
    optimizer.assert_not_called()
    assert state.status == "completed"
