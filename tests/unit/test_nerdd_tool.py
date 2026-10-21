"""Unit tests for the NERDD characterization tool and its registry wiring.

All NERDD HTTP traffic is faked, so these tests never touch the network.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from schemas.tool_schemas import CharacterizationRequest  # noqa: E402
from tools import nerdd_tool  # noqa: E402
from tools.nerdd_tool import NERDD_MODULES, NerddTool, NerddToolError  # noqa: E402
from tools.registry import load_tool_registry  # noqa: E402

ASPIRIN = "CC(=O)OC1=CC=CC=C1C(=O)O"
CAFFEINE = "CN1C=NC2=C1C(=O)N(C)C(=O)N2C"


class FakeResponse:
    def __init__(self, payload: Any, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def json(self) -> Any:
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FakeNerdd:
    """Minimal stand-in for the NERDD job API."""

    def __init__(
        self,
        rows: List[Dict[str, Any]],
        page_size: int = 10,
        statuses: Optional[List[str]] = None,
        create_status: int = 200,
    ):
        self.rows = rows
        self.page_size = page_size
        self.statuses = statuses or ["completed"]
        self.create_status = create_status
        self.calls: List[Dict[str, Any]] = []
        self._status_index = 0

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        self.calls.append({"method": method, "url": url, **kwargs})

        if url.endswith("/jobs"):
            if self.create_status >= 400:
                return FakeResponse({"detail": "nope"}, status_code=self.create_status)
            return FakeResponse({"id": "job-1", "status": "created"})

        if url.endswith("/results"):
            page = int(kwargs.get("params", {}).get("page", 1))
            start = (page - 1) * self.page_size
            chunk = self.rows[start : start + self.page_size]
            has_next = start + self.page_size < len(self.rows)
            return FakeResponse(
                {
                    "data": chunk,
                    "pagination": {"page": page, "next_url": "next" if has_next else None},
                }
            )

        status = self.statuses[min(self._status_index, len(self.statuses) - 1)]
        self._status_index += 1
        num_pages = max(1, -(-len(self.rows) // self.page_size))
        return FakeResponse({"id": "job-1", "status": status, "num_pages_total": num_pages})


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(nerdd_tool.time, "sleep", lambda *_: None)


def install(monkeypatch, fake: FakeNerdd) -> FakeNerdd:
    monkeypatch.setattr(nerdd_tool.requests, "request", fake.request)
    return fake


# --------------------------------------------------------------------- modules


def test_every_registered_tool_matches_a_known_module():
    registry = load_tool_registry()
    nerdd_specs = [spec for spec in registry.list(kind="characterizer") if spec.id.startswith("nerdd_")]

    assert len(nerdd_specs) == len(NERDD_MODULES)
    for spec in nerdd_specs:
        module = spec.config["module"]
        assert module in NERDD_MODULES
        # Everything the registry advertises must actually be produced by the module.
        assert set(spec.provides) == set(NERDD_MODULES[module].properties)


def test_registry_instantiates_each_nerdd_tool(monkeypatch, no_sleep):
    registry = load_tool_registry()
    install(monkeypatch, FakeNerdd([{"mol_id": 0, "probability": 0.5}]))

    for spec in registry.list(kind="characterizer"):
        if not spec.id.startswith("nerdd_"):
            continue
        tool = registry.create(spec.id)
        assert isinstance(tool, NerddTool)
        assert tool.module == spec.config["module"]
        assert set(tool.provided_properties) == set(spec.provides)


def test_results_page_202_is_retried_then_reported(monkeypatch, no_sleep):
    class AlwaysIncomplete(FakeNerdd):
        def request(self, method, url, **kwargs):
            if url.endswith("/results"):
                self.calls.append({"method": method, "url": url, **kwargs})
                return FakeResponse({"detail": "Results not yet available"}, status_code=202)
            return super().request(method, url, **kwargs)

    fake = install(monkeypatch, AlwaysIncomplete([{"mol_id": 0, "probability": 0.5}]))

    with pytest.raises(NerddToolError, match="202"):
        NerddTool(module="np_scout").predict({"m1": ASPIRIN})

    # The page must be retried rather than silently treated as an empty result set.
    assert sum(1 for call in fake.calls if call["url"].endswith("/results")) > 1


def test_unknown_module_is_rejected():
    with pytest.raises(NerddToolError):
        NerddTool(module="not_a_module").predict({"m1": ASPIRIN})


# ------------------------------------------------------------------ extraction


def test_cyplebrity_returns_probabilities_and_aggregate(monkeypatch, no_sleep):
    install(
        monkeypatch,
        FakeNerdd([
            {
                "mol_id": 0,
                "prediction_1": 0.27,
                "prediction_2": 0.24,
                "prediction_3": 0.11,
                "prediction_4": 0.05,
                "prediction_5": 0.81,
            }
        ]),
    )

    results = NerddTool(module="cyplebrity")._run(search_space={"m1": ASPIRIN}, ids_to_process=["m1"])

    assert results["m1"]["cyp1a2_inhibition"] == 0.27
    assert results["m1"]["cyp3a4_inhibition"] == 0.81
    assert results["m1"]["cyp_inhibition_risk"] == 0.81


def test_cypstrate_encodes_labels_and_omits_abstentions(monkeypatch, no_sleep):
    install(
        monkeypatch,
        FakeNerdd([
            {
                "mol_id": 0,
                "prediction_1a2": "Non-substrate",
                "prediction_2a6": "Substrate",
                "prediction_2d6": "No prediction",
                "prediction_3a4": "Substrate",
            }
        ]),
    )

    properties = NerddTool(module="cypstrate")._run(
        search_space={"m1": ASPIRIN}, ids_to_process=["m1"]
    )["m1"]

    assert properties["cyp1a2_substrate"] == 0.0
    assert properties["cyp2a6_substrate"] == 1.0
    assert properties["cyp3a4_substrate"] == 1.0
    # "No prediction" must not be scored as a negative result.
    assert "cyp2d6_substrate" not in properties
    # Not every isozyme resolved, so the minimized aggregate must be withheld rather
    # than making an uncertain molecule look clean.
    assert "cyp_substrate_count" not in properties
    assert properties["cyp_substrate_coverage"] == 3.0


def test_cypstrate_aggregate_is_reported_when_every_isozyme_resolves(monkeypatch, no_sleep):
    row = {"mol_id": 0}
    for isozyme in ("1a2", "2a6", "2b6", "2c8", "2c9", "2c19", "2d6", "2e1", "3a4"):
        row[f"prediction_{isozyme}"] = "Non-substrate"
    row["prediction_3a4"] = "Substrate"
    install(monkeypatch, FakeNerdd([row]))

    properties = NerddTool(module="cypstrate")._run(
        search_space={"m1": ASPIRIN}, ids_to_process=["m1"]
    )["m1"]

    assert properties["cyp_substrate_coverage"] == 9.0
    assert properties["cyp_substrate_count"] == 1.0


def test_atom_level_rows_are_aggregated_per_molecule(monkeypatch, no_sleep):
    install(
        monkeypatch,
        FakeNerdd([
            {"mol_id": 0, "atom_id": 0, "prediction": 0.10, "prediction_binary": False},
            {"mol_id": 0, "atom_id": 1, "prediction": 0.90, "prediction_binary": True},
            {"mol_id": 0, "atom_id": 2, "prediction": 0.50, "prediction_binary": True},
        ]),
    )

    properties = NerddTool(module="fame3r")._run(
        search_space={"m1": ASPIRIN}, ids_to_process=["m1"]
    )["m1"]

    assert properties["som_max_probability"] == 0.90
    assert properties["som_count"] == 2.0
    assert properties["som_mean_probability"] == pytest.approx(0.5)


def test_awesom_averages_uncertainty(monkeypatch, no_sleep):
    install(
        monkeypatch,
        FakeNerdd([
            {"mol_id": 0, "prediction": 0.2, "prediction_binary": False, "total_uncertainty": 0.4},
            {"mol_id": 0, "prediction": 0.8, "prediction_binary": True, "total_uncertainty": 0.6},
        ]),
    )

    properties = NerddTool(module="awesom")._run(
        search_space={"m1": ASPIRIN}, ids_to_process=["m1"]
    )["m1"]

    assert properties["awesom_som_max_probability"] == 0.8
    assert properties["awesom_total_uncertainty"] == pytest.approx(0.5)


def test_non_finite_and_missing_values_are_dropped(monkeypatch, no_sleep):
    install(
        monkeypatch,
        FakeNerdd([{"mol_id": 0, "probability": None}, {"mol_id": 1, "probability": float("nan")}]),
    )

    result = NerddTool(module="np_scout").predict({"m1": ASPIRIN, "m2": CAFFEINE})

    assert result.results == {}
    assert sorted(result.failed_molecules) == ["m1", "m2"]


# -------------------------------------------------------------------- plumbing


def test_rows_map_to_the_submitted_molecule_ids(monkeypatch, no_sleep):
    install(
        monkeypatch,
        FakeNerdd([
            {"mol_id": 1, "probability": 0.8},
            {"mol_id": 0, "probability": 0.2},
        ]),
    )

    results = NerddTool(module="np_scout")._run(
        search_space={"m1": ASPIRIN, "m2": CAFFEINE}, ids_to_process=["m1", "m2"]
    )

    assert results["m1"]["np_likeness"] == 0.2
    assert results["m2"]["np_likeness"] == 0.8


def test_rows_fall_back_to_the_raw_echoed_input_when_mol_id_is_unusable(monkeypatch, no_sleep):
    # NERDD echoes the submitted string as input_text and an RDKit-canonicalized form
    # as input_smiles, so only input_text can be matched against what we submitted.
    install(
        monkeypatch,
        FakeNerdd([
            {
                "mol_id": None,
                "input_text": CAFFEINE,
                "input_smiles": "Cn1c(=O)c2c(ncn2C)n(C)c1=O",  # canonical, differs
                "probability": 0.7,
            }
        ]),
    )

    results = NerddTool(module="np_scout")._run(
        search_space={"m1": ASPIRIN, "m2": CAFFEINE}, ids_to_process=["m1", "m2"]
    )

    assert results["m2"]["np_likeness"] == 0.7
    assert "m1" not in results


def test_results_are_read_across_pages(monkeypatch, no_sleep):
    rows = [{"mol_id": i, "probability": i / 10} for i in range(12)]
    fake = install(monkeypatch, FakeNerdd(rows, page_size=5))

    smiles = {f"m{i}": f"C{'C' * i}" for i in range(12)}
    results = NerddTool(module="np_scout").predict(smiles).results

    assert len(results) == 12
    assert results["m11"]["np_likeness"] == pytest.approx(1.1)
    assert sum(1 for call in fake.calls if call["url"].endswith("/results")) == 3


def test_large_requests_are_split_into_several_jobs(monkeypatch, no_sleep):
    fake = install(monkeypatch, FakeNerdd([{"mol_id": 0, "probability": 0.5}]))

    smiles = {f"m{i}": f"C{'C' * i}" for i in range(7)}
    NerddTool(module="np_scout", batch_size=3).predict(smiles)

    created = [call for call in fake.calls if call["url"].endswith("/jobs") and call["method"] == "POST"]
    assert len(created) == 3  # 3 + 3 + 1


def test_client_errors_are_not_retried_as_a_different_method(monkeypatch, no_sleep):
    # 400/422 describe a genuinely bad request; silently resending as GET would burn
    # quota and hide the real cause.
    fake = install(monkeypatch, FakeNerdd([], create_status=422))

    with pytest.raises(NerddToolError, match="422"):
        NerddTool(module="np_scout").predict({"m1": ASPIRIN})

    assert {call["method"] for call in fake.calls if call["url"].endswith("/jobs")} == {"POST"}


def test_quota_errors_explain_themselves(monkeypatch, no_sleep):
    install(monkeypatch, FakeNerdd([], create_status=403))

    with pytest.raises(NerddToolError, match="quota"):
        NerddTool(module="np_scout").predict({"m1": ASPIRIN})


def test_job_creation_falls_back_to_the_get_route(monkeypatch, no_sleep):
    fake = install(monkeypatch, FakeNerdd([{"mol_id": 0, "probability": 0.5}], create_status=405))

    # The POST route is rejected, so the tool must retry via GET; with the fake always
    # returning 405 the call ultimately fails, but both routes must have been tried.
    with pytest.raises(NerddToolError):
        NerddTool(module="np_scout").predict({"m1": ASPIRIN})

    methods = {call["method"] for call in fake.calls if call["url"].endswith("/jobs")}
    assert methods == {"POST", "GET"}


def test_failed_jobs_raise(monkeypatch, no_sleep):
    install(monkeypatch, FakeNerdd([], statuses=["processing", "failed"]))

    with pytest.raises(NerddToolError, match="failed"):
        NerddTool(module="cyplebrity").predict({"m1": ASPIRIN})


def test_polling_times_out_instead_of_hanging(monkeypatch, no_sleep):
    install(monkeypatch, FakeNerdd([], statuses=["processing"]))

    with pytest.raises(NerddToolError, match="did not finish"):
        NerddTool(module="cyplebrity", max_wait_seconds=0, poll_interval=0).predict({"m1": ASPIRIN})


def test_module_parameters_are_sent_with_the_job(monkeypatch, no_sleep):
    fake = install(monkeypatch, FakeNerdd([{"mol_id": 0, "prediction_1a2": "Substrate"}]))

    NerddTool(module="cypstrate", job_params={"prediction_mode": "full_coverage"}).predict(
        {"m1": ASPIRIN}
    )

    create = next(call for call in fake.calls if call["url"].endswith("/jobs"))
    assert create["data"]["prediction_mode"] == "full_coverage"
    assert create["data"]["inputs"] == [ASPIRIN]


@pytest.mark.parametrize(
    "module, endpoint",
    [("np_scout", "np-scout"), ("skin_doctor_cp", "skin-doctor-cp"), ("cypstrate", "cypstrate")],
)
def test_jobs_are_sent_to_the_nerdd_module_id(monkeypatch, no_sleep, module, endpoint):
    monkeypatch.delenv("NERDD_BASE_URL", raising=False)
    fake = install(monkeypatch, FakeNerdd([{"mol_id": 0}]))

    NerddTool(module=module).predict({"m1": ASPIRIN})

    create = next(call for call in fake.calls if call["url"].endswith("/jobs"))
    assert create["url"] == f"{nerdd_tool.DEFAULT_BASE_URL}/{endpoint}/jobs"


@pytest.mark.parametrize(
    "module, required",
    [
        ("np_scout", {"contour_lines": False}),
        ("fame3r", {"fame_score": False, "shannon_entropy": False}),
    ],
)
def test_required_boolean_parameters_are_always_sent(monkeypatch, no_sleep, module, required):
    fake = install(monkeypatch, FakeNerdd([{"mol_id": 0}]))

    NerddTool(module=module).predict({"m1": ASPIRIN})

    create = next(call for call in fake.calls if call["url"].endswith("/jobs"))
    for name, value in required.items():
        assert create["data"][name] is value


def test_characterize_accepts_a_characterization_request(monkeypatch, no_sleep):
    install(monkeypatch, FakeNerdd([{"mol_id": 0, "probability": 0.42}]))

    request = CharacterizationRequest(
        smiles={"m1": ASPIRIN},
        properties=["np_likeness"],
        molecule_ids=["m1"],
        search_space={"m1": ASPIRIN},
    )
    result = NerddTool(module="np_scout").characterize(request)

    assert result.results["m1"]["np_likeness"] == 0.42
    assert result.metadata["nerdd_module"] == "np_scout"


def test_empty_input_is_rejected():
    with pytest.raises(NerddToolError):
        NerddTool(module="np_scout").predict({"m1": ""})


def test_pagination_terminates_when_the_server_always_advertises_a_next_page(
    monkeypatch, no_sleep
):
    """NERDD reports an unknown entry count as infinite and then always sends next_url."""

    class EndlessPages(FakeNerdd):
        def request(self, method, url, **kwargs):
            if url.endswith("/results"):
                self.calls.append({"method": method, "url": url, **kwargs})
                return FakeResponse(
                    {
                        "data": [{"mol_id": 0, "probability": 0.5}],
                        "pagination": {"next_url": "always-another-page"},
                    }
                )
            if url.endswith("/jobs"):
                return FakeResponse({"id": "job-1", "status": "created"})
            # num_pages_total omitted: the server does not know the entry count yet.
            return FakeResponse({"id": "job-1", "status": "completed"})

    fake = install(monkeypatch, EndlessPages([]))

    result = NerddTool(module="np_scout").predict({"m1": ASPIRIN, "m2": CAFFEINE})

    pages = sum(1 for call in fake.calls if call["url"].endswith("/results"))
    assert pages <= 2 + 5  # bounded by molecule count, not by the server's hint
    assert result.results["m1"]["np_likeness"] == 0.5
