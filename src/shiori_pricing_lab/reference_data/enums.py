"""Controlled vocabulary for Bond Reference Data (docs/20 §4/§11).

``docs/20_bli_bond_reference_data_preflight.md`` §4/§11 leaves two
vocabulary decisions open for the implementation slice: whether
``bond_type`` and ``status`` need their own controlled enums, and how
floating-rate / amortizing / convertible / inflation-linked / perpetual /
structured-note bonds are excluded from MVP pricing given that Annex B
§B.5 has no dedicated flag for any of them. This module resolves both,
explicitly, as follows.

``BondType`` -- resolves the exclusion-mechanism open item via
`docs/20` §5 option (a): a controlled vocabulary that names each
non-plain-vanilla category so the reference-data object can still record
"what the bond actually is" (a floating-rate note is valid reference
data), while a single, explicit MVP-eligibility rule
(``bond_type is BondType.FIXED_COUPON_BULLET``) drives exclusion, per
``reference_data.eligibility``. This is deliberately not option (b) (a
vocabulary so narrow that only plain-vanilla bonds can be constructed at
all) -- reference data must be able to describe an FRN or a convertible
even though the MVP pricing pool cannot yet use one.

``BondStatus`` -- resolves the same open item for ``status`` with the
minimal ``ACTIVE``/``INACTIVE`` pair ``docs/20`` §4 suggested as an
acceptable starting vocabulary, since Annex B does not enumerate allowed
``status`` values in the text available to this repo.

Reuses ``Currency``, ``Frequency``, ``DayCount``, ``BusinessDayConvention``,
and ``BondYieldConvention`` directly from
``shiori_pricing_lab.products.enums`` -- no new members are added to those
enums here, matching `docs/20` §13's "no new enum values added silently"
rule.
"""

from __future__ import annotations

from enum import StrEnum


class BondType(StrEnum):
    """Bond structural category (docs/20 §4/§5/§11).

    Only ``FIXED_COUPON_BULLET`` is MVP-pricing-eligible
    (``reference_data.eligibility.is_mvp_pricing_eligible``). The other
    members exist so non-plain-vanilla bonds can still be recorded as
    valid reference data and explicitly rejected at the eligibility layer,
    rather than being unrepresentable or silently misclassified.
    """

    FIXED_COUPON_BULLET = "FIXED_COUPON_BULLET"
    FLOATING_RATE_NOTE = "FLOATING_RATE_NOTE"
    AMORTIZING = "AMORTIZING"
    CONVERTIBLE = "CONVERTIBLE"
    INFLATION_LINKED = "INFLATION_LINKED"
    PERPETUAL = "PERPETUAL"
    STRUCTURED_NOTE = "STRUCTURED_NOTE"


class BondStatus(StrEnum):
    """Bond Master record status (docs/20 §4/§11).

    A minimal starting vocabulary; not derived from Annex B, which does
    not enumerate allowed ``status`` values in the text available here.
    """

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"
