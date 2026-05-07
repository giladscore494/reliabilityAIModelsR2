# -*- coding: utf-8 -*-

from app.services.comparison.normalization import (
    build_display_name,
    infer_compare_segment,
    map_cars_to_slots,
)
from app.services.comparison.scoring import (
    compute_overall_score,
    determine_winner,
    score_ordinal_negative,
    score_ordinal_positive,
)


def test_scoring_helpers_from_split_module():
    assert score_ordinal_negative("low") == 100.0
    assert score_ordinal_negative("high", confidence=0.5) == 10.0
    assert score_ordinal_positive("high") == 100.0
    assert compute_overall_score(
        {
            "reliability_risk": 100,
            "ownership_cost": 60,
            "practicality_comfort": 60,
            "driving_performance": 20,
        }
    ) == 70.0
    assert determine_winner({"car_1": 80, "car_2": 70}) == "car_1"
    assert determine_winner({"car_1": 80, "car_2": 77}) == "tie"


def test_normalization_helpers_from_split_module():
    car = {"make": "Toyota", "model": "Corolla", "year": 2020}
    assert build_display_name(car) == "Toyota Corolla 2020"
    assert map_cars_to_slots([car]) == {
        "car_1": {**car, "display_name": "Toyota Corolla 2020"}
    }
    assert (
        infer_compare_segment(
            {"make": "Toyota", "model": "Aygo", "display_name": "Toyota Aygo"},
            {"facts": {"body_type": "city"}},
        )
        == "city_mini"
    )
