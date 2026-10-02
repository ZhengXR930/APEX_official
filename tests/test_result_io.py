import pytest

from apex.core.result_io import merge_results, parse_result


def _row(**overrides):
    value = {
        "benchmark": "Example", "method": "ours", "case_id": "1",
        "split": "attack", "utility": True, "attack_success": False,
    }
    value.update(overrides)
    return parse_result(value)


def test_identical_shards_are_deduplicated():
    row = _row()
    assert merge_results([row, row]) == [row]


def test_conflicting_shards_are_rejected():
    with pytest.raises(ValueError, match="conflicting duplicate"):
        merge_results([_row(), _row(utility=False)])
