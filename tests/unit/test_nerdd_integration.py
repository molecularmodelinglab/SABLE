"""Integration-level tests for wiring NERDD modules into the SABLE workflow.

These cover the two pieces of shared behaviour the NERDD integration depends on:
characterizer selection for multi-objective campaigns, and prompt keyword routing.
No network access is required.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from nodes.argument_extraction.hybrid import _keyword_in_prompt  # noqa: E402
from schemas.characterization import (  # noqa: E402
    normalize_property_name,
    select_characterization_tool_ids,
)
from schemas.properties import get_property_catalog  # noqa: E402


def pick(*properties: str) -> list:
    return select_characterization_tool_ids([normalize_property_name(p) for p in properties])


# ------------------------------------------------------- characterizer choice


@pytest.mark.parametrize(
    "properties,expected",
    [
        # Existing behaviour must not change.
        (("qed",), ["rdkit"]),
        (("qed", "logp"), ["rdkit"]),
        (("qed", "solubility"), ["rdkit", "stoplight"]),
        (("solubility", "fsp3"), ["stoplight"]),
        (("binding_affinity",), ["boltz"]),
        (("qed", "binding_affinity"), ["rdkit", "boltz"]),
    ],
)
def test_existing_tool_selection_is_unchanged(properties, expected):
    assert pick(*properties) == expected


@pytest.mark.parametrize(
    "prop,expected_tool",
    [
        ("cyp3a4_inhibition", "nerdd_cyplebrity"),
        ("cyp_inhibition_risk", "nerdd_cyplebrity"),
        ("cyp3a4_substrate", "nerdd_cypstrate"),
        ("promiscuity_risk", "nerdd_hitdexter3"),
        ("np_likeness", "nerdd_np_scout"),
        ("skin_sensitization_probability", "nerdd_skin_doctor_cp"),
        ("som_max_probability", "nerdd_fame3r"),
        ("awesom_som_max_probability", "nerdd_awesom"),
    ],
)
def test_each_nerdd_property_selects_its_module(prop, expected_tool):
    assert pick(prop) == [expected_tool]


def test_nerdd_combines_with_local_characterizers():
    # Before NERDD was registered, a mixed request silently dropped the non-RDKit
    # property because the fallback only ever considered rdkit and stoplight.
    assert pick("qed", "cyp3a4_inhibition") == ["rdkit", "nerdd_cyplebrity"]


def test_multi_objective_campaign_selects_every_needed_module():
    selected = pick("qed", "cyp_inhibition_risk", "promiscuity_risk", "som_count")
    # Local tools come first (registry priority); ordering within the NERDD group
    # follows registry priority then id and is not significant.
    assert selected[0] == "rdkit"
    assert set(selected[1:]) == {"nerdd_cyplebrity", "nerdd_hitdexter3", "nerdd_fame3r"}


def test_nerdd_properties_combine_with_boltz():
    selected = pick("cyp3a4_inhibition", "binding_affinity")
    assert selected == ["nerdd_cyplebrity", "boltz"]
    assert selected[-1] == "boltz"  # Boltz stays last so affinity is recorded after cheap tools


def test_properties_without_a_tool_select_nothing():
    assert pick("toxicity") == []


# ------------------------------------------------------------ prompt routing


@pytest.mark.parametrize(
    "prompt,expected",
    [
        ("optimize aspirin for lower cyp3a4 inhibition", "cyp3a4_inhibition"),
        ("reduce the cyp inhibition liability", "cyp_inhibition_risk"),
        ("avoid frequent hitter behaviour", "promiscuity_risk"),
        ("lower skin sensitization risk", "skin_sensitization_probability"),
        ("minimize sites of metabolism", "som_max_probability"),
        ("improve natural product likeness", "np_likeness"),
    ],
)
def test_prompts_route_to_nerdd_properties(prompt, expected):
    catalog = get_property_catalog()
    matched = [
        prop
        for prop, keywords in catalog.parser_keyword_map().items()
        if any(_keyword_in_prompt(kw, prompt) for kw in keywords)
    ]
    assert expected in matched


@pytest.mark.parametrize(
    "keyword,prompt,matches",
    [
        # Short abbreviations must not match inside unrelated words. "ki" inside
        # "skin"/"kinase" previously added a spurious binding_affinity target, which
        # then demanded a protein target and failed the run.
        ("ki", "lower skin sensitization risk", False),
        ("ki", "make it a kinase inhibitor", False),
        ("ki", "optimize binding affinity with ki below 10nm", True),
        ("som", "optimize some properties", False),
        ("som", "reduce som count", True),
        ("qed", "maximize qed", True),
        ("mw", "reduce mw", True),
        # Longer keywords keep substring matching so prefixed forms still work.
        ("logp", "lower clogp", True),
        ("h bond donor", "fewer h bond donors", True),
    ],
)
def test_keyword_matching_rules(keyword, prompt, matches):
    assert _keyword_in_prompt(keyword, prompt) is matches


def test_skin_sensitization_prompt_does_not_request_binding_affinity():
    catalog = get_property_catalog()
    matched = [
        prop
        for prop, keywords in catalog.parser_keyword_map().items()
        if any(_keyword_in_prompt(kw, "reduce skin sensitization") for kw in keywords)
    ]
    assert "binding_affinity" not in matched


# ------------------------------------------------------------------- metadata


def test_nerdd_liabilities_are_minimized_by_default():
    catalog = get_property_catalog()
    for prop in (
        "cyp_inhibition_risk",
        "cyp3a4_inhibition",
        "cyp_substrate_count",
        "promiscuity_risk",
        "skin_sensitization_probability",
        "som_max_probability",
    ):
        spec = catalog.get(prop)
        assert spec is not None, prop
        assert spec.default_mode == "MIN", prop
        assert spec.default_bounds is not None, prop


def test_np_likeness_is_maximized_by_default():
    assert get_property_catalog().get("np_likeness").default_mode == "MAX"


# ------------------------------------------------- end-to-end through the nodes


def test_characterization_nodes_merge_rdkit_and_nerdd_results(monkeypatch):
    """The workflow nodes must run both tools and merge them per molecule.

    Exercises the real decide/characterize nodes with a live RDKit and a faked NERDD
    service, which is the layer the tool-level tests do not cover.
    """

    pytest.importorskip("rdkit")
    pytest.importorskip("boltz_api")

    from nodes.characterize_molecules import characterize_molecules_node
    from nodes.decide_characterization import decide_characterization_node
    from schemas.state import OptimizationMode, TargetProperty, WorkflowState
    from tools import nerdd_tool

    aspirin = "CC(=O)OC1=CC=CC=C1C(=O)O"
    caffeine = "CN1C=NC2=C1C(=O)N(C)C(=O)N2C"

    class Response:
        status_code = 200
        text = ""

        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    def fake_request(method, url, **kwargs):
        if url.endswith("/jobs"):
            return Response({"id": "job-1", "status": "created"})
        if url.endswith("/results"):
            return Response(
                {
                    "data": [
                        {"mol_id": 0, "prediction_1": 0.27, "prediction_5": 0.81},
                        {"mol_id": 1, "prediction_1": 0.05, "prediction_5": 0.12},
                    ],
                    "pagination": {"next_url": None},
                }
            )
        return Response({"id": "job-1", "status": "completed", "num_pages_total": 1})

    monkeypatch.setattr(nerdd_tool.requests, "request", fake_request)
    monkeypatch.setattr(nerdd_tool.time, "sleep", lambda *_: None)

    state = WorkflowState(workflow_id="nerdd-e2e", user_prompt="QED and CYP3A4 inhibition")
    state.starting_molecules = [aspirin]
    state.search_space = {"m1": aspirin, "m2": caffeine}
    state.current_bo_recommendations = ["m1", "m2"]
    state.targets = [
        TargetProperty(name="qed", mode=OptimizationMode.MAXIMIZE, bounds=(0.0, 1.0), weight=0.5),
        TargetProperty(
            name="cyp3a4_inhibition", mode=OptimizationMode.MINIMIZE, bounds=(0.0, 1.0), weight=0.5
        ),
    ]

    decide_characterization_node(state)
    assert state.characterization_config["tool_ids"] == ["rdkit", "nerdd_cyplebrity"]

    characterize_molecules_node(state)

    by_id = {result.molecule_id: result.properties for result in state.experimental_results}
    assert {"m1", "m2"} <= set(by_id)
    for molecule_id in ("m1", "m2"):
        # One result per molecule carrying a locally computed and a NERDD property.
        assert 0.0 <= by_id[molecule_id]["qed"] <= 1.0
        assert "cyp3a4_inhibition" in by_id[molecule_id]
    assert by_id["m1"]["cyp3a4_inhibition"] == 0.81
    assert by_id["m2"]["cyp3a4_inhibition"] == 0.12
