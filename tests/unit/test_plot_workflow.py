from utils.plot_workflow import get_opt_directions


def test_plot_directions_prefer_resolved_platform_target():
    raw = {
        "targets": [
            {"name": "boltz_optimization_score", "mode": "MAX"},
        ],
        "parsed_arguments": {
            "target_properties": [
                {"property_name": "binding_affinity", "optimization_mode": "MIN"},
            ],
        },
    }

    assert get_opt_directions(raw) == {"boltz_optimization_score": "max"}


def test_plot_directions_fall_back_to_parsed_arguments():
    raw = {
        "parsed_arguments": {
            "target_properties": [
                {"property_name": "binding_affinity", "optimization_mode": "MIN"},
            ],
        },
    }

    assert get_opt_directions(raw) == {"binding_affinity": "min"}