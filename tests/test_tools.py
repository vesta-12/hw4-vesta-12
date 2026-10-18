import pytest
from tools import calculate, lookup

def test_lookup_current_2025_population():
    result = lookup("Brightwater", "population_2025")
    assert result["value"] == 44675
    assert result["sources"] == ["NR-05"]


def test_lookup_typo_suggests_correct_name():
    result = lookup("Ossbrige", "population_2025")
    assert "error" in result
    assert "Ossbridge" in result["error"]


def test_lookup_similar_district_names():
    assert lookup("Ossbridge", "population_2025")["value"] == 52904
    assert lookup("Ossford", "population_2025")["value"] == 30982
    ambiguous = lookup("Oss", "population_2025")
    assert "error" in ambiguous
    assert "Ossbridge" in ambiguous["error"] and "Ossford" in ambiguous["error"]


def test_lookup_ignores_archived_profile():
    result = lookup("Tarnvale", "population_2025")
    assert result["value"] == 71388
    assert result["sources"] == ["NR-03"]  # Never NR-13


def test_lookup_applies_correction_notice():
    result = lookup("Brightwater", "area_km2")
    assert result["value"] == 921.4
    assert result["sources"] == ["NR-05", "NR-16"]


def test_lookup_converts_cubic_metres_to_megalitres():
    result = lookup("Lindenmoor", "household_water_2025_megalitres")
    assert result["value"] == pytest.approx(2151.4)
    assert result["unit"] == "megalitres"


def test_lookup_returns_region_tariff():
    result = lookup("region", "water_tariff_2025_tenge_per_m3")
    assert result["value"] == pytest.approx(142.35)
    assert result["sources"] == ["NR-15"]


def test_lookup_unknown_field_error():
    assert "error" in lookup("Brightwater", "secret_field")


def test_calculate_correct_multiplication():
    assert calculate("48317 * 1.0947")["value"] == pytest.approx(52892.6199)


def test_calculate_density_difference():
    assert calculate("37203 / 758.6 - 44675 / 921.4")["value"] == pytest.approx(0.5556561156405735)


def test_calculate_division_by_zero():
    assert "error" in calculate("1 / 0")


def test_calculate_rejects_import_and_function_call():
    assert "error" in calculate("__import__('os').system('echo hi')")


def test_calculate_rejects_attribute_access():
    assert "error" in calculate("(1).__class__")


def test_calculate_rejects_exponentiation():
    assert "error" in calculate("9**9**9")


def test_calculate_rejects_large_number():
    assert "error" in calculate("999999999999999 * 2")


def test_calculate_rejects_non_string():
    assert "error" in calculate(None)
