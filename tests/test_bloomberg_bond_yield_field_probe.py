"""Tests for ``tools/bloomberg_bond_yield_field_probe.py`` (Issue #196 §A).

The probe's whole reason to exist is that the Bloomberg Yield field must be
*confirmed*, never guessed. So the things held down here are: it refuses a
malformed mnemonic before sending anything, it fires one historical request
per operator-named candidate and never generates one of its own, it reports
shape without ever writing a Bloomberg value to a file, and -- the one that
matters most -- when two candidates both return a series it says AMBIGUOUS
and stops, rather than picking the closest-looking one.

Every value below is made up. No network access and no real ``blpapi``: the
historical pass is driven through ``probe_historical_field``'s own
``send_request`` injection seam.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT / "tools"))

import bloomberg_bond_yield_field_probe as module  # noqa: E402
from bloomberg_bond_yield_field_probe import (  # noqa: E402
    HistoricalFieldEvidence,
    YieldFieldProbeReport,
    _validate_field_mnemonic,
    build_report,
    build_verdict,
    probe_historical_field,
    render_markdown,
    unresolved_reason,
    write_report,
)

_FIELD_A = "SYNTHETIC_TEST_YIELD_A"
_FIELD_B = "SYNTHETIC_TEST_YIELD_B"
_IDENTIFIER = "/isin/US0000000000"


# --- input hygiene ------------------------------------------------------------


@pytest.mark.parametrize("malformed", ["px last", "PX-LAST", "px_last", "", "   ", "YLD;DROP"])
def test_rejects_a_malformed_mnemonic(malformed):
    with pytest.raises(ValueError, match="--field"):
        _validate_field_mnemonic(malformed)


def test_accepts_a_well_formed_mnemonic():
    assert _validate_field_mnemonic("  SOME_FIELD_9  ") == "SOME_FIELD_9"


# --- the verdict never chooses ------------------------------------------------


def _evidence(field, *, status="returned", count=0, valued=None, **overrides):
    """One candidate's evidence, defaulting to a well-formed single-security answer.

    `security_data_record_count`/`distinct_securities` matter: a response with
    no securityData, or one naming several securities, is refused by the
    loader and so is unresolved here. A hand-built fixture has to say it is a
    normal one-security answer, exactly as `probe_historical_field` reports.
    """

    fields = {
        "field": field,
        "status": status,
        "observation_count": count,
        "observations_with_a_value": count if valued is None else valued,
        "security_data_record_count": 1,
        "distinct_securities": 1,
    }
    fields.update(overrides)
    return HistoricalFieldEvidence(**fields)


def test_two_usable_candidates_are_reported_as_ambiguous_never_resolved():
    verdict = build_verdict((_evidence(_FIELD_A, count=250), _evidence(_FIELD_B, count=250)))

    assert verdict.startswith("AMBIGUOUS")
    assert _FIELD_A in verdict and _FIELD_B in verdict
    assert "stop condition 1" in verdict


def test_one_usable_candidate_is_reported_without_being_called_confirmed():
    verdict = build_verdict((_evidence(_FIELD_A, count=250), _evidence(_FIELD_B, status="empty")))

    assert verdict.startswith("ONE CANDIDATE RETURNED DATA")
    assert _FIELD_A in verdict
    # A count is evidence of availability, never of meaning.
    assert "not a meaning" in verdict


def test_no_usable_candidate_never_becomes_a_recommendation():
    verdict = build_verdict((_evidence(_FIELD_A, status="field_exception"),))

    assert verdict.startswith("NO USABLE SERIES")
    assert "Do not pick one anyway" in verdict


def test_no_candidate_probed_is_its_own_answer():
    assert build_verdict(()).startswith("NO CANDIDATE PROBED")


# --- the historical pass ------------------------------------------------------


class _FakeBlpapiException(Exception):
    pass


class _FakeBlpapiExceptionNamespace:
    Exception = _FakeBlpapiException


@pytest.fixture()
def fake_blpapi(monkeypatch):
    fake = type(sys)("blpapi")
    fake.exception = _FakeBlpapiExceptionNamespace
    monkeypatch.setitem(sys.modules, "blpapi", fake)
    return fake


class _Element:
    def __init__(self, sub=None, values=None, text=None, datatype=None, is_null=False):
        self._sub = sub or {}
        self._values = values
        self._text = text
        self._datatype = datatype
        self._is_null = is_null

    def isNull(self):
        return self._is_null

    def hasElement(self, name, exclude_null_elements=False):
        """Mirrors ``blpapi.Element.hasElement``'s own two-argument signature."""

        if name not in self._sub:
            return False
        return not (exclude_null_elements and self._sub[name].isNull())

    def getElement(self, name):
        return self._sub[name]

    def getElementAsString(self, name):
        return self._sub[name]._text

    def numValues(self):
        return len(self._values or [])

    def getValueAsElement(self, index):
        return self._values[index]

    def datatype(self):
        return self._datatype

    def __str__(self):
        return self._text or "<element>"


# Bloomberg's own null element: present, reported null, and raising when read
# as a string -- which is how NIL_VALUE arrives on a day with no observation.
_NULL = object()


class _NullValueElement(_Element):
    def __init__(self):
        super().__init__(is_null=True, datatype="FLOAT64")


# An element that is present and non-null but cannot be converted to a string,
# which is what blpapi raises on for a malformed value.
_UNREADABLE = object()


class _UnreadableElement(_Element):
    pass


def _row(observation_date, value=None):
    if observation_date is None:
        sub = {}
    elif observation_date is _UNREADABLE:
        sub = {"date": _UnreadableElement()}
    else:
        sub = {"date": _Element(text=observation_date)}
    if value is _NULL:
        sub[_FIELD_A] = _NullValueElement()
    elif value is _UNREADABLE:
        sub[_FIELD_A] = _UnreadableElement()
    elif value is not None:
        sub[_FIELD_A] = _Element(text=value, datatype="FLOAT64")
    return _RaisingOnNullRow(sub=sub)


class _RaisingOnNullRow(_Element):
    """A row whose string read of a null element raises, exactly as blpapi's does."""

    def getElementAsString(self, name):
        element = self._sub[name]
        if isinstance(element, _UnreadableElement):
            raise _FakeBlpapiException("cannot convert this element to a string")
        if element.isNull():
            raise _FakeBlpapiException("cannot convert a null element to a string")
        return element._text


def _security_data(
    rows=(), *, security="SYNTHETIC TEST Corp", security_error=None, exceptions=None
):
    security_element = (
        _UnreadableElement() if security is _UNREADABLE else _Element(text=security)
    )
    sub = {"security": security_element, "fieldData": _Element(values=list(rows))}
    if security_error is not None:
        sub["securityError"] = _Element(text=security_error)
    if exceptions is not None:
        sub["fieldExceptions"] = _Element(values=[_Element(text=e) for e in exceptions])
    return _RaisingOnNullRow(sub=sub)


def _sender(security_data, recorder=None):
    def _send(*, service_uri, request_name, configure, collect, context):
        request = _RecordingRequest()
        configure(request)
        if recorder is not None:
            recorder.append((service_uri, request_name, request))
        collect(_Element(sub={"securityData": security_data}))

    return _send


class _RecordingRequest:
    def __init__(self):
        self.securities: list[str] = []
        self.fields: list[str] = []
        self.options: dict[str, str] = {}

    def append(self, name, value):
        getattr(self, name).append(value)

    def set(self, name, value):
        self.options[name] = value


def test_sends_one_historical_request_pinned_like_the_production_loader(fake_blpapi):
    recorder: list = []
    probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 5),
        end=date(2026, 3, 9),
        sample_rows=3,
        send_request=_sender(_security_data([_row("2026-01-06", "4.0")]), recorder),
    )

    (service_uri, request_name, request) = recorder[0]
    assert service_uri == "//blp/refdata"
    assert request_name == "HistoricalDataRequest"
    assert request.securities == [_IDENTIFIER]
    assert request.fields == [_FIELD_A]
    assert request.options["startDate"] == "20260105"
    assert request.options["endDate"] == "20260309"
    assert request.options["periodicitySelection"] == "DAILY"
    assert request.options["periodicityAdjustment"] == "ACTUAL"
    assert request.options["nonTradingDayFillOption"] == "ACTIVE_DAYS_ONLY"
    assert request.options["nonTradingDayFillMethod"] == "NIL_VALUE"


def test_reports_shape_and_keeps_the_value_sample_separate(fake_blpapi):
    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=2,
        send_request=_sender(
            _security_data(
                [
                    _row("2026-01-08", "4.0"),
                    _row("2026-01-06", "4.12"),
                    _row("2026-01-07"),
                    _row("2026-01-09", "4.4"),
                ]
            )
        ),
    )

    assert evidence.status == "returned"
    assert evidence.observation_count == 4
    assert evidence.observations_with_a_value == 3
    assert evidence.rows_with_no_value == 1
    # First/last are the range's own bounds, not the response's arrival order.
    assert evidence.first_observation_date == "2026-01-06"
    assert evidence.last_observation_date == "2026-01-09"
    assert evidence.value_datatype == "FLOAT64"
    assert evidence.resolved_security == "SYNTHETIC TEST Corp"
    assert sample == (("2026-01-08", "4.0"), ("2026-01-06", "4.12"))


def test_an_empty_series_is_reported_as_empty_not_as_an_error(fake_blpapi):
    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=3,
        send_request=_sender(_security_data([])),
    )

    assert evidence.status == "empty"
    assert evidence.observation_count == 0
    assert sample == ()


def test_a_field_exception_is_reported_as_such(fake_blpapi):
    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=3,
        send_request=_sender(_security_data([], exceptions=["BAD_FLD"])),
    )

    assert evidence.status == "field_exception"
    assert "BAD_FLD" in evidence.detail
    assert sample == ()


def test_a_security_error_is_reported_as_such(fake_blpapi):
    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=3,
        send_request=_sender(_security_data([], security_error="BAD_SEC")),
    )

    assert evidence.status == "security_error"
    assert "BAD_SEC" in evidence.detail


def test_a_transport_failure_is_recorded_against_that_field_only(fake_blpapi):
    def _failing(**kwargs):
        raise RuntimeError("Bloomberg DAPI session failed to start")

    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=3,
        send_request=_failing,
    )

    assert evidence.status == "error"
    assert "failed to start" in evidence.detail
    assert sample == ()


# --- the written report never carries a Bloomberg value -----------------------


def _report() -> YieldFieldProbeReport:
    return YieldFieldProbeReport(
        generated_at="2026-08-31T14:05:00+00:00",
        identifier=_IDENTIFIER,
        start_date="2026-01-01",
        end_date="2026-01-31",
        search_terms=("yield",),
        search_attempts=(),
        search_error=None,
        descriptions=(),
        historical=(
            HistoricalFieldEvidence(
                field=_FIELD_A,
                status="returned",
                observation_count=3,
                rows_with_no_value=1,
                first_observation_date="2026-01-06",
                last_observation_date="2026-01-09",
                value_datatype="FLOAT64",
                resolved_security="SYNTHETIC TEST Corp",
            ),
        ),
        verdict="ONE CANDIDATE RETURNED DATA",
    )


def test_the_report_records_shape_and_no_value(tmp_path):
    data = build_report(_report())
    markdown_path, json_path = write_report(data, tmp_path)

    written = markdown_path.read_text(encoding="utf-8") + json_path.read_text(encoding="utf-8")
    assert "4.12" not in written
    assert "2026-01-06" in written
    assert "FLOAT64" in written
    assert "carries no Bloomberg value" in written


def test_the_markdown_leads_with_the_verdict():
    rendered = render_markdown(build_report(_report()))

    assert "**Verdict:** ONE CANDIDATE RETURNED DATA" in rendered


def test_the_default_search_terms_are_terms_not_mnemonics():
    # Nothing in the default search vocabulary may look like a field this repo
    # is quietly proposing.
    for term in module.DEFAULT_SEARCH_TERMS:
        assert term == term.lower()
        assert "_" not in term


def test_a_probe_that_never_reached_bloomberg_is_not_evidence_about_any_field():
    verdict = build_verdict(
        (
            _evidence(_FIELD_A, status="error"),
            _evidence(_FIELD_B, status="error"),
        )
    )

    assert verdict.startswith("PROBE COULD NOT RUN")
    assert "Nothing here is evidence about any field" in verdict


def test_a_missing_blpapi_is_reported_rather_than_raised(monkeypatch):
    monkeypatch.setitem(sys.modules, "blpapi", None)

    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=3,
        send_request=_sender(_security_data([_row("2026-01-06", "4.0")])),
    )

    assert evidence.status == "error"
    assert "blpapi is not installed" in evidence.detail
    assert sample == ()


def test_a_candidate_that_never_reached_bloomberg_stops_the_verdict(monkeypatch):
    """A field that was never asked has not been ruled out (Codex review, PR #198).

    Without this, a transient session failure on one candidate would let the
    other one be reported as "the one that returned data" -- promoting a
    mnemonic against an opponent Bloomberg never answered for.
    """

    verdict = build_verdict(
        (_evidence(_FIELD_A, count=250), _evidence(_FIELD_B, status="error"))
    )

    assert verdict.startswith("INCONCLUSIVE")
    assert _FIELD_B in verdict
    assert "has not been ruled out" in verdict
    assert "ONE CANDIDATE" not in verdict


def test_an_unprobed_candidate_stops_an_ambiguous_verdict_too(monkeypatch):
    verdict = build_verdict(
        (
            _evidence(_FIELD_A, count=250),
            _evidence(_FIELD_B, count=250),
            _evidence("SYNTHETIC_TEST_YIELD_C", status="error"),
        )
    )

    assert verdict.startswith("INCONCLUSIVE")
    assert "AMBIGUOUS" not in verdict


def test_rows_carrying_no_value_are_not_a_usable_series():
    """A field that answers with dated rows but no Yield has supplied no Yield."""

    verdict = build_verdict((_evidence(_FIELD_A, count=250, valued=0),))

    assert verdict.startswith("NO USABLE SERIES")
    assert "Do not pick one anyway" in verdict


def test_a_valueless_candidate_never_makes_a_comparison_ambiguous():
    verdict = build_verdict(
        (_evidence(_FIELD_A, count=250, valued=250), _evidence(_FIELD_B, count=250, valued=0))
    )

    assert verdict.startswith("ONE CANDIDATE RETURNED DATA")
    assert _FIELD_A in verdict
    assert "250 observations carrying a value" in verdict


def test_a_bloomberg_null_row_is_counted_as_a_hole_not_a_crash(fake_blpapi):
    """A bare hasElement reports a null element present, and reading it raises.

    In the probe that would abort the whole run and lose every other
    candidate's evidence with it (Codex review, PR #198).
    """

    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_sender(
            _security_data(
                [
                    _row("2026-01-06", "4.0"),
                    _row("2026-01-07", _NULL),
                    _row("2026-01-08", "4.4"),
                ]
            )
        ),
    )

    assert evidence.status == "returned"
    assert evidence.observation_count == 3
    assert evidence.observations_with_a_value == 2
    assert evidence.rows_with_no_value == 1
    assert sample == (("2026-01-06", "4.0"), ("2026-01-08", "4.4"))


@pytest.mark.parametrize("unusable", ["nan", "NaN", "inf", "-inf", "N.A.", "4.0X", "#N/A N/A"])
def test_a_value_the_loader_would_refuse_is_not_a_usable_observation(fake_blpapi, unusable):
    """The probe must predict the workbench, not flatter a candidate.

    A non-blank but non-numeric or non-finite value is refused by the
    canonical loader's `_parse_finite_float`; counting it here would let
    `build_verdict` recommend -- or call ambiguous -- a field the workbench
    cannot load at all (Codex review, PR #198).
    """

    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_sender(
            _security_data([_row("2026-01-06", "4.0"), _row("2026-01-07", unusable)])
        ),
    )

    assert evidence.observation_count == 2
    assert evidence.observations_with_a_value == 1
    assert evidence.rows_with_an_unusable_value == 1
    # Counted apart from a genuine hole: they mean different things.
    assert evidence.rows_with_no_value == 0
    assert sample == (("2026-01-06", "4.0"),)


def test_a_field_answering_only_in_unloadable_values_is_not_a_usable_series(fake_blpapi):
    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_sender(
            _security_data([_row("2026-01-06", "N.A."), _row("2026-01-07", "N.A.")])
        ),
    )

    assert evidence.observation_count == 2
    assert evidence.observations_with_a_value == 0
    # Not "no usable series" -- that would read as "this field has no history".
    # It answered; the workbench just cannot load what it answered with.
    verdict = build_verdict((evidence,))
    assert verdict.startswith("INCONCLUSIVE")
    assert "non-numeric or non-finite" in verdict


def test_the_probe_applies_the_canonical_loaders_own_value_rule():
    """Imported, not restated -- so the two cannot drift apart."""

    from shiori_pricing_lab.data.bloomberg_bond_quote import _parse_finite_float

    assert module._parse_finite_float is _parse_finite_float


# --- a series the loader would refuse is never an endorsement -----------------


def test_a_duplicate_observation_date_is_counted(fake_blpapi):
    """The loader refuses the whole series over one; the probe must see it."""

    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_sender(
            _security_data([_row("2026-01-06", "4.0"), _row("2026-01-06", "4.5")])
        ),
    )

    assert evidence.duplicate_observation_dates == 1
    assert unresolved_reason(evidence) is not None
    assert "duplicate observation" in unresolved_reason(evidence)


def test_a_duplicate_date_across_two_records_is_counted(fake_blpapi):
    """Caught across records, exactly as the loader catches it across messages."""

    def _send(*, service_uri, request_name, configure, collect, context):
        configure(_RecordingRequest())
        collect(_Element(sub={"securityData": _security_data([_row("2026-01-06", "4.0")])}))
        collect(_Element(sub={"securityData": _security_data([_row("2026-01-06", "4.5")])}))

    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_send,
    )

    assert evidence.duplicate_observation_dates == 1


def test_a_row_with_no_date_is_counted_not_placeheld(fake_blpapi):
    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_sender(
            _security_data([_row("2026-01-06", "4.0"), _row(None, "4.5")])
        ),
    )

    assert evidence.rows_with_no_date == 1
    assert evidence.observation_count == 1
    assert evidence.first_observation_date == "2026-01-06"
    assert "no date" in unresolved_reason(evidence)


def test_one_unusable_value_beside_good_ones_still_blocks_an_endorsement():
    """The loader aborts the whole series on it -- it keeps no valid subset.

    So `observations_with_a_value > 0` is not enough to endorse a candidate
    (Codex review, PR #198): 250 good rows and one sentinel is a field the
    workbench cannot load over this range.
    """

    good = _evidence(_FIELD_A, count=250, valued=250)
    mixed = _evidence(_FIELD_B, count=250, valued=249, rows_with_an_unusable_value=1)

    verdict = build_verdict((good, mixed))

    assert verdict.startswith("INCONCLUSIVE")
    assert _FIELD_B in verdict
    assert "ONE CANDIDATE" not in verdict


def test_a_duplicate_date_blocks_an_endorsement_too():
    good = _evidence(_FIELD_A, count=250, valued=250)
    duplicated = _evidence(_FIELD_B, count=250, duplicate_observation_dates=1)

    verdict = build_verdict((good, duplicated))

    assert verdict.startswith("INCONCLUSIVE")
    assert "duplicate observation" in verdict


def test_an_empty_or_excepted_candidate_is_a_real_answer_not_an_unresolved_one():
    """"This field has no history" and "this field does not exist" are results."""

    assert unresolved_reason(_evidence(_FIELD_B, status="empty")) is None
    assert unresolved_reason(_evidence(_FIELD_B, status="field_exception")) is None

    verdict = build_verdict((_evidence(_FIELD_A, count=250, valued=250),
                             _evidence(_FIELD_B, status="empty")))
    assert verdict.startswith("ONE CANDIDATE RETURNED DATA")


def test_a_clean_candidate_is_not_made_unresolved_by_a_plain_hole():
    """A row Bloomberg returned with no value is a gap, not a refusal."""

    clean = _evidence(_FIELD_A, count=250, valued=249, rows_with_no_value=1)

    assert unresolved_reason(clean) is None
    assert build_verdict((clean,)).startswith("ONE CANDIDATE RETURNED DATA")


# --- the probe refuses everything the loader refuses ---------------------------
# Five review rounds arrived one refusal condition at a time (PR #198). This
# table is the whole of the canonical loader's own fail-closed list, exercised
# end-to-end through `probe_historical_field`, so a condition the loader
# refuses can never again reach `build_verdict` as a working candidate.


def _one_record(rows, **kwargs):
    return _sender(_security_data(rows, **kwargs))


def _two_records(first, second):
    def _send(*, service_uri, request_name, configure, collect, context):
        configure(_RecordingRequest())
        collect(_Element(sub={"securityData": first}))
        collect(_Element(sub={"securityData": second}))

    return _send


_LOADER_REFUSALS = {
    "no securityData at all": (
        lambda **kw: None,
        "no securityData",
    ),
    "securityError": (
        lambda: _one_record([], security_error="BAD_SEC"),
        "securityError",
    ),
    "two different securities": (
        lambda: _two_records(
            _security_data([_row("2026-01-06", "4.0")], security="A Corp"),
            _security_data([_row("2026-01-07", "4.1")], security="B Corp"),
        ),
        "different securities",
    ),
    # The same envelope defect with no rows at all: it reads as an empty
    # series, so a check placed after the `empty` early-out never sees it
    # (Codex review, PR #198). Every envelope-level condition needs its
    # rowless shape here, since that is the one an early-out can hide.
    "two different securities, neither carrying rows": (
        lambda: _two_records(
            _security_data([], security="A Corp"),
            _security_data([], security="B Corp"),
        ),
        "different securities",
    ),
    # An early return on the first record must still see the envelope the
    # later records make up. These are the shapes where a per-record verdict
    # is reached before the whole envelope has been read (Codex review,
    # PR #198).
    "field exception first, a different security after": (
        lambda: _two_records(
            _security_data([], security="A Corp", exceptions=["BAD_FLD"]),
            _security_data([_row("2026-01-06", "4.0")], security="B Corp"),
        ),
        "different securities",
    ),
    # This one is already unresolved by its securityError, which is checked
    # first; it is here so the envelope evidence beside it is still exercised
    # (asserted separately below).
    "security error first, a different security after": (
        lambda: _two_records(
            _security_data([], security="A Corp", security_error="BAD_SEC"),
            _security_data([_row("2026-01-06", "4.0")], security="B Corp"),
        ),
        "securityError",
    ),
    "an unreadable security element": (
        lambda: _one_record([_row("2026-01-06", "4.0")], security=_UNREADABLE),
        "could not be read as a string",
    ),
    "an unreadable date element": (
        lambda: _one_record([_row("2026-01-06", "4.0"), _row(_UNREADABLE, "4.1")]),
        "could not be read as a string",
    ),
    "an unreadable value element": (
        lambda: _one_record([_row("2026-01-06", "4.0"), _row("2026-01-07", _UNREADABLE)]),
        "could not be read as a string",
    ),
    "row with no date": (
        lambda: _one_record([_row("2026-01-06", "4.0"), _row(None, "4.1")]),
        "no date",
    ),
    "malformed date": (
        lambda: _one_record([_row("2026-01-06", "4.0"), _row("06/01/2026", "4.1")]),
        "strict YYYY-MM-DD",
    ),
    "date outside the requested window": (
        lambda: _one_record([_row("2026-01-06", "4.0"), _row("2026-03-09", "4.1")]),
        "outside the requested window",
    ),
    "duplicate observation date": (
        lambda: _one_record([_row("2026-01-06", "4.0"), _row("2026-01-06", "4.1")]),
        "duplicate observation",
    ),
    "non-finite value": (
        lambda: _one_record([_row("2026-01-06", "4.0"), _row("2026-01-07", "nan")]),
        "non-numeric or non-finite",
    ),
}


@pytest.mark.parametrize("condition", sorted(_LOADER_REFUSALS))
def test_every_loader_refusal_makes_the_candidate_unresolved(fake_blpapi, condition):
    make_sender, expected_phrase = _LOADER_REFUSALS[condition]
    sender = make_sender()
    if sender is None:  # "no securityData at all": the message carries none

        def sender(*, service_uri, request_name, configure, collect, context):
            configure(_RecordingRequest())
            collect(_Element(sub={}))

    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=sender,
    )

    reason = unresolved_reason(evidence)
    assert reason is not None, f"{condition} was not treated as unresolved"
    assert expected_phrase in reason
    # And it can never be endorsed, nor make a rival win by default.
    assert build_verdict((evidence,)).startswith("INCONCLUSIVE")
    rival = _evidence(_FIELD_B, count=250, valued=250)
    assert build_verdict((evidence, rival)).startswith("INCONCLUSIVE")


def test_a_well_formed_answer_is_not_caught_by_any_of_those(fake_blpapi):
    """The counterpart: the table above must not refuse a loadable series."""

    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_one_record(
            [_row("2026-01-06", "4.0"), _row("2026-01-07", _NULL), _row("2026-01-08", "4.4")]
        ),
    )

    assert unresolved_reason(evidence) is None
    assert build_verdict((evidence,)).startswith("ONE CANDIDATE RETURNED DATA")


@pytest.mark.parametrize(
    "first_record",
    [
        _security_data([], security="A Corp", exceptions=["BAD_FLD"]),
        _security_data([], security="A Corp", security_error="BAD_SEC"),
    ],
    ids=["field exception", "security error"],
)
def test_an_early_return_still_reports_the_whole_envelope(fake_blpapi, first_record):
    """Envelope evidence is gathered from every record, not the ones read so far.

    A per-record verdict reached on the first record used to carry envelope
    counts collected only up to that point, so a later record naming a
    different security was invisible (Codex review, PR #198). The loader
    settles the envelope question before reading any row; so does this now.
    """

    def _send(*, service_uri, request_name, configure, collect, context):
        configure(_RecordingRequest())
        collect(_Element(sub={"securityData": first_record}))
        collect(
            _Element(
                sub={
                    "securityData": _security_data(
                        [_row("2026-01-06", "4.0")], security="B Corp"
                    )
                }
            )
        )

    evidence, _ = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_send,
    )

    assert evidence.security_data_record_count == 2
    assert evidence.distinct_securities == 2
    assert unresolved_reason(evidence) is not None


def test_an_unreadable_element_never_aborts_the_probe(fake_blpapi):
    """It becomes evidence, not an exception (Codex review, PR #198).

    A native blpapi failure escaping `probe_historical_field` would take the
    other candidates' evidence and the written report down with it -- which
    defeats the point of a diagnostic that exists to report on every
    candidate.
    """

    evidence, sample = probe_historical_field(
        field=_FIELD_A,
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        sample_rows=5,
        send_request=_one_record(
            [_row("2026-01-06", "4.0"), _row("2026-01-07", _UNREADABLE)]
        ),
    )

    assert evidence.unreadable_elements == 1
    # The readable row beside it is still counted -- this is evidence, not a crash.
    assert evidence.observations_with_a_value == 1
    assert sample == (("2026-01-06", "4.0"),)
    assert "could not be read as a string" in unresolved_reason(evidence)


def test_one_candidate_blowing_up_costs_only_its_own_evidence(monkeypatch):
    """The run-loop backstop: every other candidate is still probed and reported."""

    calls: list[str] = []

    def _explode(*, field, **kwargs):
        calls.append(field)
        if field == _FIELD_A:
            raise RuntimeError("something native went wrong")
        return (
            _evidence(field, count=250, valued=250),
            (),
        )

    monkeypatch.setattr(module, "probe_historical_field", _explode)

    report, samples = module.run_probe(
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        fields=(_FIELD_A, _FIELD_B),
        search_terms=(),
        sample_rows=5,
    )

    assert calls == [_FIELD_A, _FIELD_B]
    by_field = {evidence.field: evidence for evidence in report.historical}
    assert by_field[_FIELD_A].status == "error"
    assert "something native went wrong" in by_field[_FIELD_A].detail
    assert by_field[_FIELD_B].observations_with_a_value == 250
    # And the run stays honest about what it could not establish.
    assert report.verdict.startswith("INCONCLUSIVE")
    # A report can still be written, which is the whole point.
    assert build_report(report)["historical_availability"]


# --- an operator slip must not manufacture a stop condition -------------------


def test_a_repeated_field_is_refused_rather_than_probed_twice():
    """Probed twice, it would be AMBIGUOUS against itself (Codex review, PR #198)."""

    with pytest.raises(ValueError, match="more than once"):
        module._validate_field_mnemonics([_FIELD_A, _FIELD_B, _FIELD_A])


def test_a_repeated_field_is_caught_after_normalisation():
    with pytest.raises(ValueError, match="more than once"):
        module._validate_field_mnemonics([_FIELD_A, f"  {_FIELD_A}  "])


def test_distinct_fields_keep_the_order_they_were_named_in():
    assert module._validate_field_mnemonics([_FIELD_B, _FIELD_A]) == (_FIELD_B, _FIELD_A)


def test_the_cli_refuses_a_repeated_field_before_probing_anything(capsys):
    exit_code = module.main(
        [
            "--identifier",
            _IDENTIFIER,
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-31",
            "--field",
            _FIELD_A,
            "--field",
            _FIELD_A,
        ]
    )

    assert exit_code == 2
    assert "more than once" in capsys.readouterr().err


# --- no pass may take the run down with it ------------------------------------


def test_a_native_failure_in_the_documentation_pass_becomes_per_field_evidence(monkeypatch):
    """It runs before the historical pass, so an escape costs everything after it."""

    class _NativeFailure(Exception):
        pass

    def _explode(fields):
        raise _NativeFailure("unreadable fieldInfo element")

    monkeypatch.setattr(module, "describe_fields", _explode)
    monkeypatch.setattr(module, "search_yield_field_catalogue", lambda terms: ((), None))
    monkeypatch.setattr(
        module,
        "probe_historical_field",
        lambda **kwargs: (_evidence(kwargs["field"], count=250, valued=250), ()),
    )

    report, _ = module.run_probe(
        identifier=_IDENTIFIER,
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        fields=(_FIELD_A,),
        search_terms=(),
        sample_rows=5,
    )

    assert [d.status for d in report.descriptions] == ["field_error"]
    assert "unreadable fieldInfo element" in report.descriptions[0].detail
    # The pass after it still ran, and a report can still be built.
    assert report.historical[0].observations_with_a_value == 250
    assert build_report(report)["historical_availability"]


def test_a_native_failure_in_the_catalogue_search_costs_only_the_search(monkeypatch):
    class _NativeFailure(Exception):
        pass

    def _explode(uri):
        raise _NativeFailure("apiflds introspection blew up")

    monkeypatch.setattr(module, "discover_service", _explode)

    attempts, error = module.search_yield_field_catalogue(("yield",))

    assert attempts == ()
    assert "apiflds introspection blew up" in error
