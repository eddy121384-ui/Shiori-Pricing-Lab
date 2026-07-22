"""Bounded, read-only context extraction for the manual workbench prototype
(PR #136 correctness follow-up).

Scope: one small, pure function -- :func:`extract_standalone_option_case_context`
-- that reads a fixed, small set of already-present fields off a base
standalone-option-case mapping and returns them verbatim in a flat dict. No
new schema/dataclass, no pricing, no market/derived value of any kind: every
returned value is either a direct top-level case field, a direct field on
the one ``bond_reference_data_universe`` record whose ``isin`` matches
``bond_option.underlying_isin``, or a direct field on ``bond_quote``. A field
the case does not carry at all (e.g. a CUSIP -- this schema has no such
field) is simply never a key in this module's output; rendering "Not
available" for an absent key is the HTML/JS layer's job, never this
module's.

This exists because the prototype's instrument header, date fields, and
Instrument Details panel previously showed hardcoded reference-image
placeholder text (a fictitious US Treasury) while manual pricing actually
ran against the bundled synthetic bond -- a screenshot showing a real
premium next to a fake instrument identity is misleading. This function
makes the real case's own identity/date/quote fields available to the page,
with zero pricing or derivation logic added.
"""

from __future__ import annotations

_TOP_LEVEL_FIELDS = (
    "valuation_date",
    "forward_settlement_date",
    "option_settlement_date",
    "source_system",
    "as_of_timestamp",
    "snapshot_id",
)

_REFERENCE_DATA_FIELDS = (
    "issuer",
    "currency",
    "coupon",
    "maturity_date",
    "coupon_frequency",
    "day_count",
    "bond_type",
)

_BOND_QUOTE_FIELDS = (
    "quote_side",
    "clean_price_per_100",
    "accrued_interest_per_100",
    "yield_value",
)


def extract_standalone_option_case_context(case: dict) -> dict:
    """Return a bounded, verbatim, read-only context dict describing ``case``.

    Raises ``ValueError`` if ``case`` is not a mapping, or if
    ``bond_reference_data_universe`` does not contain exactly one record
    whose ``isin`` matches ``bond_option.underlying_isin`` -- this module
    never guesses which reference-data record describes the traded
    instrument.
    """

    if not isinstance(case, dict):
        raise ValueError(f"case must be a mapping, got {type(case).__name__}")

    bond_option = case["bond_option"]
    underlying_isin = bond_option["underlying_isin"]

    universe = case["bond_reference_data_universe"]
    matches = [record for record in universe if record.get("isin") == underlying_isin]
    if len(matches) != 1:
        raise ValueError(
            "expected exactly one bond_reference_data_universe record matching "
            f"underlying_isin {underlying_isin!r}, found {len(matches)}"
        )
    reference_data = matches[0]
    bond_quote = case["bond_quote"]

    context: dict = {
        "underlying_isin": underlying_isin,
        "expiry_date": bond_option["expiry_date"],
    }
    for field in _REFERENCE_DATA_FIELDS:
        context[field] = reference_data[field]
    for field in _BOND_QUOTE_FIELDS:
        context[field] = bond_quote[field]
    for field in _TOP_LEVEL_FIELDS:
        context[field] = case[field]

    return context
