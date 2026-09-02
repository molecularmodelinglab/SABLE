from unittest.mock import MagicMock

import pandas as pd
import pytest

from nodes.enumerate_molecules import _create_enumerator_tool, _tool_options_for_enumerator
from schemas.errors import ToolError
from schemas.state import WorkflowState
from schemas.tool_registry import ToolKind, ToolSpec
from schemas.tool_schemas import EnumerationRequest
from tools.healer_enumerator_tool import HealerEnumeratorTool


def _response(payload):
    response = MagicMock()
    response.json.return_value = payload
    response.raise_for_status.return_value = None
    return response


def test_api_mode_polls_saves_complete_rows_and_returns_unique_products(tmp_path):
    session = MagicMock()
    session.request.side_effect = [
        _response({"job_id": "job-123", "status": "submitted"}),
        _response({"job_id": "job-123", "status": "PROGRESS", "progress": {"stage": "enumerating"}}),
        _response(
            {
                "job_id": "job-123",
                "status": "SUCCESS",
                "result": {
                    "complete": [
                        {"ID": "HEAL_000001", "Product": "CCO", "BB1": "C", "qed": 0.4},
                        {"ID": "HEAL_000002", "Product": "CCO", "BB1": "CC", "qed": 0.5},
                        {"ID": "HEAL_000003", "Product": "CCN", "BB1": "N", "qed": 0.6},
                    ],
                    "stats": {"n_molecules": 3, "seconds": 1.9},
                },
                "error": None,
            }
        ),
    ]
    tool = HealerEnumeratorTool(
        execution_mode="api",
        endpoint="https://healer.example/",
        output_dir=str(tmp_path),
        poll_interval_seconds=0,
        session=session,
    )

    result = tool.enumerate(EnumerationRequest(starting_smiles="CCO", max_molecules=3))

    assert result.molecules == {"mol_0": "CCO", "mol_1": "CCN"}
    assert result.metadata["execution_mode"] == "api"
    assert result.metadata["job_id"] == "job-123"
    assert result.metadata["stats"] == {"n_molecules": 3, "seconds": 1.9}

    csv_path = tmp_path / "molecular_enumerations_job-123.csv"
    saved = pd.read_csv(csv_path)
    assert list(saved.columns) == ["ID", "Product", "BB1", "qed"]
    assert saved["Product"].tolist() == ["CCO", "CCO", "CCN"]

    calls = session.request.call_args_list
    assert calls[0].args == ("POST", "https://healer.example/enumerate/molecule")
    assert calls[0].kwargs["json"] == {
        "molecule": "CCO",
        "bb_source": "US_stock",
        "reaction_tags": ["all"],
        "max_bbs_per_frag": 10,
        "n_compositions": 3,
        "max_total_products": 8000,
    }
    assert calls[1].args == ("GET", "https://healer.example/jobs/job-123")
    assert calls[2].args == ("GET", "https://healer.example/jobs/job-123")


def test_api_mode_continues_polling_started_job(tmp_path):
    session = MagicMock()
    session.request.side_effect = [
        _response({"job_id": "started-job", "status": "submitted"}),
        _response({"job_id": "started-job", "status": "STARTED"}),
        _response(
            {
                "job_id": "started-job",
                "status": "SUCCESS",
                "result": {"complete": [{"Product": "CCO"}], "stats": {}},
            }
        ),
    ]
    tool = HealerEnumeratorTool(
        execution_mode="api",
        endpoint="https://healer.example",
        output_dir=str(tmp_path),
        poll_interval_seconds=0,
        session=session,
    )

    result = tool.enumerate(EnumerationRequest(starting_smiles="CCO", max_molecules=1))

    assert result.molecules == {"mol_0": "CCO"}
    assert session.request.call_count == 3


def test_workflow_checkpoint_directory_is_used_for_healer_csv(tmp_path):
    checkpoint_dir = tmp_path / "runs" / "run-123" / "checkpoints"
    state = WorkflowState(
        user_prompt="Enumerate ethanol analogs",
        run_paths={"checkpoints": str(checkpoint_dir)},
    )
    spec = ToolSpec(
        id="healer",
        kind=ToolKind.ENUMERATOR,
        class_path="tools.healer_enumerator_tool:HealerEnumeratorTool",
        metadata={"tool_specific_options": ["healer_mode"]},
    )

    options = _tool_options_for_enumerator(spec, state)
    registry = MagicMock()
    _create_enumerator_tool(registry, spec, options)

    assert options["output_dir"] == str(checkpoint_dir)
    registry.create.assert_called_once_with(
        "healer",
        healer_mode="MoleculeHEALER",
        output_dir=str(checkpoint_dir),
    )


def test_site_mode_uses_site_route_and_reactive_sites(tmp_path):
    session = MagicMock()
    session.request.side_effect = [
        _response({"job_id": "site-job", "status": "submitted"}),
        _response(
            {
                "job_id": "site-job",
                "status": "SUCCESS",
                "result": {"complete": [{"Product": "CCN"}], "stats": {}},
            }
        ),
    ]
    tool = HealerEnumeratorTool(
        healer_mode="SiteHEALER",
        execution_mode="api",
        endpoint="https://healer.example",
        output_dir=str(tmp_path),
        poll_interval_seconds=0,
        session=session,
    )

    result = tool._run(molecule="CCO", n_compositions=2, reactive_sites=[1, 2])

    assert result == {"mol_0": "CCN"}
    post_call = session.request.call_args_list[0]
    assert post_call.args == ("POST", "https://healer.example/enumerate/site")
    assert post_call.kwargs["json"]["reactive_sites"] == [1, 2]


def test_api_failure_is_reported_as_tool_error(tmp_path):
    session = MagicMock()
    session.request.side_effect = [
        _response({"job_id": "failed-job", "status": "submitted"}),
        _response({"job_id": "failed-job", "status": "FAILURE", "error": "enumeration failed"}),
    ]
    tool = HealerEnumeratorTool(
        execution_mode="api",
        endpoint="https://healer.example",
        output_dir=str(tmp_path),
        poll_interval_seconds=0,
        session=session,
    )

    with pytest.raises(ToolError, match="enumeration failed") as exc_info:
        tool._run(molecule="CCO")

    assert exc_info.value.code == "API_JOB_FAILED"


def test_polling_rejects_undocumented_status(tmp_path):
    session = MagicMock()
    session.request.side_effect = [
        _response({"job_id": "queued-job", "status": "submitted"}),
        _response({"job_id": "queued-job", "status": "queued"}),
    ]
    tool = HealerEnumeratorTool(
        execution_mode="api",
        endpoint="https://healer.example",
        output_dir=str(tmp_path),
        poll_interval_seconds=0,
        session=session,
    )

    with pytest.raises(ToolError, match="unknown job status 'queued'") as exc_info:
        tool._run(molecule="CCO")

    assert exc_info.value.code == "API_BAD_RESPONSE"


def test_fragment_mode_is_rejected_by_api():
    with pytest.raises(ValueError, match="FragmentHEALER is not supported"):
        HealerEnumeratorTool(
            healer_mode="FragmentHEALER",
            execution_mode="api",
            endpoint="https://healer.example",
            session=MagicMock(),
        )
