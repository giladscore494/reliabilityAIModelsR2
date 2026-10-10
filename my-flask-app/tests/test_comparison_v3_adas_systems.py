# -*- coding: utf-8 -*-
"""Comparison V3: every driver-assistance flag that counts is visible. The driver-assistance row's chevron lists, per
car, exactly which of the 19 government systems it has (Hebrew names); a nice-to-have feature counts only when that
row exists, so its flag is always on screen."""

import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.comparison_v3.buyer_profile import FEATURE_LABELS_HE, FEATURE_SOURCES
from app.services.comparison_v3.labels import ADAS_LABELS_HE
from app.services.comparison_v3.pipeline import collect_result, run_comparison_v3

from comparison_v3_fakes import KEYS, LIVE_TRIO, OCTAVIA, build_v3_deps, gov, vkey

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "compare_v3.js"
node_required = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
SLOTS = ("car_1", "car_2", "car_3")
NICE = {"mode": "personalized", "main_use": "family",
        "nice_to_have_features": ["adaptive_cruise_control", "reverse_camera", "blind_spot_monitoring",
                                  "lane_keeping_assist"],
        "priorities": {"purchase_price": 2, "safety": 2, "performance": 2, "efficiency_environment": 2,
                       "practicality": 2, "ev_convenience": 2}}


def _run(keys, profile, records=None):
    body = {"cars": [{"variant_identity_key": k} for k in keys], "buyer_profile": profile}
    result = collect_result(run_comparison_v3(body, build_v3_deps(records=records)))
    assert result["type"] == "result", result
    return result["data"]


def _trio(profile=NICE):
    return _run([KEYS[k] for k in ("live_bmw", "live_audi", "live_alfa")], profile)


def _adas_row(data):
    return next(r for s in data["table"]["sections"] for r in s["rows"] if r["row_id"] == "adas_systems_count")


def _expected(record):
    flags = [k[len("adas."):] for k, f in record["facts"].items() if k.startswith("adas.") and f["value"]]
    return [ADAS_LABELS_HE[f] for f in ADAS_LABELS_HE if f in flags]       # the registry order


def test_the_19_systems_have_hebrew_names():
    assert len(ADAS_LABELS_HE) == 19 and all(ADAS_LABELS_HE.values())
    # the four nice-to-have / must-have features are four of the 19, with the same Hebrew names
    for feature, (_, flag) in FEATURE_SOURCES.items():
        assert ADAS_LABELS_HE[flag] == FEATURE_LABELS_HE[feature]


def test_trio_chevron_lists_exactly_each_cars_systems():
    data = _trio()
    row = _adas_row(data)
    for slot, record in zip(SLOTS, LIVE_TRIO):
        cell = row["cells"][slot]
        assert cell["systems_he"] == _expected(record)
        assert cell["text"] == f"{len(cell['systems_he'])} מתוך 19"           # the count is the listed systems
    assert [len(row["cells"][s]["systems_he"]) for s in SLOTS] == [9, 13, 7]
    audi = row["cells"]["car_2"]["systems_he"]
    assert "בקרת שיוט אדפטיבית" in audi and "שמירה אקטיבית על נתיב" in audi
    assert "בקרת שיוט אדפטיבית" not in row["cells"]["car_1"]["systems_he"]


def test_every_nice_to_have_flag_that_counts_is_listed():
    data = _trio()
    row = _adas_row(data)
    signals = [s for p in data["decision_trace"]["overall_composition"]["pairs"].values()
               for s in p["dimensions"]["safety"]["signals"] if s["group"].startswith("feature:")]
    assert signals                                                          # the features did count
    for sig in signals:
        feature = sig["group"].split(":", 1)[1]
        flag = FEATURE_SOURCES[feature][1]
        for slot, record in zip(SLOTS, LIVE_TRIO):
            has = record["facts"][f"adas.{flag}"]["value"]
            assert (ADAS_LABELS_HE[flag] in row["cells"][slot]["systems_he"]) is has


def test_a_nice_to_have_flag_never_counts_without_the_row():
    other = copy.deepcopy(OCTAVIA)
    other["variant_identity_key"] = vkey("octavia-extra-flag")
    other["facts"]["adas.zihuy_tamrurey_tnua_ind"] = gov(True)             # a different reported set: no count row
    data = _run([OCTAVIA["variant_identity_key"], other["variant_identity_key"]],
                {**NICE, "nice_to_have_features": ["blind_spot_monitoring"]}, records=[OCTAVIA, other])
    assert not [r for s in data["table"]["sections"] for r in s["rows"] if r["row_id"] == "adas_systems_count"]
    whole = json.dumps(data["decision_trace"]["overall_composition"], ensure_ascii=False)
    assert "feature:" not in whole


@node_required
def test_the_chevron_renders_each_car_and_its_systems(tmp_path):
    data = _trio()
    (tmp_path / "r.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    html = subprocess.run(["node", "-e", f"""
        const api = require({json.dumps(str(JS))});
        const r = JSON.parse(require('fs').readFileSync({json.dumps(str(tmp_path / 'r.json'))}, 'utf8'));
        process.stdout.write(api.buildTableHtml(r));"""], capture_output=True, text=True, check=True).stdout
    panel = html.split('data-row="adas_systems_count"', 1)[1].split('data-row="', 1)[0]
    assert panel.count("data-v3-systems") == 1
    for slot, record in zip(SLOTS, LIVE_TRIO):
        item = panel.split(f'<li data-slot="{slot}">', 1)[1].split("</li>", 1)[0]
        assert data["cars"][slot]["display_name"] in item
        listed = item.split(":</strong> ", 1)[1].split(", ")
        assert listed == _expected(record)
    assert "data-v3-systems" not in html.split('data-row="adas_systems_count"', 1)[0]   # only on this row
