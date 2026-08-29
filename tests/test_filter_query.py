"""Tests for custom_metrics.filter_query (P0-9 record filtering)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.custom_metrics.const import FieldType
from custom_components.custom_metrics.filter_query import (
    FilterError,
    compile_record_filter,
)
from custom_components.custom_metrics.models import FieldDefinition, RecordType

RECORD_TYPE = RecordType(
    id="widgets",
    name="Widgets",
    fields=[
        FieldDefinition(key="count", label="Count", type=FieldType.NUMBER),
        FieldDefinition(key="name", label="Name", type=FieldType.TEXT),
        FieldDefinition(key="notes", label="Notes", type=FieldType.LONG_TEXT),
        FieldDefinition(key="active", label="Active", type=FieldType.BOOLEAN),
        FieldDefinition(key="seen_at", label="Seen At", type=FieldType.DATETIME),
        FieldDefinition(
            key="mood",
            label="Mood",
            type=FieldType.SINGLE_SELECT,
            options=["happy", "sad"],
        ),
        FieldDefinition(
            key="tags",
            label="Tags",
            type=FieldType.MULTI_SELECT,
            options=["a", "b", "c"],
        ),
        FieldDefinition(key="photo", label="Photo", type=FieldType.IMAGE),
    ],
)


def test_no_filter_configured_returns_none() -> None:
    """A falsy filter_list (None, [], omitted) means 'no filtering'."""
    assert compile_record_filter(RECORD_TYPE, None) is None
    assert compile_record_filter(RECORD_TYPE, []) is None


def test_native_scalar_value_implies_equals() -> None:
    """A non-string YAML value (int/float/bool) is used directly with implied '=='."""
    predicate = compile_record_filter(RECORD_TYPE, [{"count": 5}])
    assert predicate({"count": 5}) is True
    assert predicate({"count": 6}) is False


def test_string_value_without_operator_implies_equals() -> None:
    """A plain string value (no operator prefix) means '=='."""
    predicate = compile_record_filter(RECORD_TYPE, [{"name": "Max"}])
    assert predicate({"name": "Max"}) is True
    assert predicate({"name": "John"}) is False


@pytest.mark.parametrize(
    ("raw_value", "count", "expected"),
    [
        ("== 5", 5, True),
        ("== 5", 6, False),
        ("!= 5", 6, True),
        ("!= 5", 5, False),
        ("> 5", 6, True),
        ("> 5", 5, False),
        (">= 5", 5, True),
        (">= 5", 4, False),
        ("< 5", 4, True),
        ("< 5", 5, False),
        ("<= 5", 5, True),
        ("<= 5", 6, False),
    ],
)
def test_number_operators(raw_value: str, count: int, *, expected: bool) -> None:
    """Every comparison operator works correctly against a NUMBER field."""
    predicate = compile_record_filter(RECORD_TYPE, [{"count": raw_value}])
    assert predicate({"count": count}) is expected


def test_operator_prefix_is_longest_match_first() -> None:
    """'>=' must not be mis-split into '>' plus a leftover '=value'."""
    predicate = compile_record_filter(RECORD_TYPE, [{"count": ">=30"}])
    assert predicate({"count": 30}) is True
    predicate_gt = compile_record_filter(RECORD_TYPE, [{"count": ">30"}])
    assert predicate_gt({"count": 30}) is False
    assert predicate_gt({"count": 31}) is True


def test_multiple_items_are_and_combined() -> None:
    """Every list item must match (AND-combined)."""
    predicate = compile_record_filter(RECORD_TYPE, [{"count": "> 10"}, {"name": "Max"}])
    assert predicate({"count": 20, "name": "Max"}) is True
    assert predicate({"count": 5, "name": "Max"}) is False
    assert predicate({"count": 20, "name": "John"}) is False


def test_missing_field_never_matches_any_operator() -> None:
    """A record missing an optional field fails any condition on it, incl. '!='."""
    eq_predicate = compile_record_filter(RECORD_TYPE, [{"count": 5}])
    ne_predicate = compile_record_filter(RECORD_TYPE, [{"count": "!= 5"}])
    assert eq_predicate({}) is False
    assert ne_predicate({}) is False


def test_text_field_only_supports_eq_ne() -> None:
    """TEXT fields reject ordering operators."""
    predicate = compile_record_filter(RECORD_TYPE, [{"name": "!= Max"}])
    assert predicate({"name": "John"}) is True
    assert predicate({"name": "Max"}) is False

    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"name": "> Max"}])
    assert exc_info.value.code == "unsupported_filter_operator"


def test_boolean_field_only_supports_eq_ne() -> None:
    """BOOLEAN fields support '==' / '!=' with true/false values."""
    predicate = compile_record_filter(RECORD_TYPE, [{"active": "== true"}])
    assert predicate({"active": True}) is True
    assert predicate({"active": False}) is False

    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"active": "> true"}])
    assert exc_info.value.code == "unsupported_filter_operator"


def test_single_select_field_only_supports_eq_ne() -> None:
    """SINGLE_SELECT fields support '==' / '!=' against one of its options."""
    predicate = compile_record_filter(RECORD_TYPE, [{"mood": "happy"}])
    assert predicate({"mood": "happy"}) is True
    assert predicate({"mood": "sad"}) is False

    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"mood": ">= happy"}])
    assert exc_info.value.code == "unsupported_filter_operator"


def test_multi_select_equals_means_membership() -> None:
    """MULTI_SELECT '==' checks the stored list CONTAINS the value."""
    predicate = compile_record_filter(RECORD_TYPE, [{"tags": "a"}])
    assert predicate({"tags": ["a", "b"]}) is True
    assert predicate({"tags": ["b", "c"]}) is False
    assert predicate({"tags": []}) is False


def test_multi_select_not_equals_means_non_membership() -> None:
    """MULTI_SELECT '!=' checks the stored list does NOT contain the value."""
    predicate = compile_record_filter(RECORD_TYPE, [{"tags": "!= a"}])
    assert predicate({"tags": ["b", "c"]}) is True
    assert predicate({"tags": ["a", "b"]}) is False


def test_multi_select_rejects_ordering_operators() -> None:
    """MULTI_SELECT does not support '>'/'>='/'<'/'<='."""
    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"tags": "> a"}])
    assert exc_info.value.code == "unsupported_filter_operator"


def test_datetime_ordering_and_str_vs_object_normalization() -> None:
    """
    DATETIME comparisons work whether the stored value is a str or a datetime.

    Covers the pre-existing quirk: a fresh (never round-tripped) record has a
    Python datetime object in its field data, while a reloaded-from-disk
    record has an ISO string instead - both must compare correctly. Uses
    fully-qualified (timezone-aware) ISO strings throughout to sidestep a
    separate, unrelated naive-vs-aware datetime comparison pitfall.
    """
    predicate = compile_record_filter(
        RECORD_TYPE, [{"seen_at": "> 2026-01-01T00:00:00+00:00"}]
    )
    later_dt = datetime(2026, 6, 1, tzinfo=UTC)
    later_str = "2026-06-01T00:00:00+00:00"
    earlier_dt = datetime(2025, 1, 1, tzinfo=UTC)

    assert predicate({"seen_at": later_dt}) is True
    assert predicate({"seen_at": later_str}) is True
    assert predicate({"seen_at": earlier_dt}) is False


def test_unknown_field_error() -> None:
    """Filtering on a field that doesn't exist on the record type errors clearly."""
    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"nope": 1}])
    assert exc_info.value.code == "unknown_filter_field"


def test_image_field_is_unsupported() -> None:
    """IMAGE fields can never be filtered on (internal reference object)."""
    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"photo": "x"}])
    assert exc_info.value.code == "unsupported_filter_field"


def test_invalid_value_coercion_error() -> None:
    """A filter value that can't be coerced to the field's type errors clearly."""
    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"count": "> notanumber"}])
    assert exc_info.value.code == "invalid_filter_value"


def test_non_list_filter_is_rejected() -> None:
    """`filter` must be a list, not e.g. a bare dict."""
    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, {"count": 5})
    assert exc_info.value.code == "invalid_filter_item"


def test_multi_key_item_is_rejected() -> None:
    """A list item with more than one key is a hard validation error."""
    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, [{"count": 1, "name": "Max"}])
    assert exc_info.value.code == "invalid_filter_item"


def test_non_dict_item_is_rejected() -> None:
    """A list item that isn't a mapping at all is a hard validation error."""
    with pytest.raises(FilterError) as exc_info:
        compile_record_filter(RECORD_TYPE, ["count"])
    assert exc_info.value.code == "invalid_filter_item"
