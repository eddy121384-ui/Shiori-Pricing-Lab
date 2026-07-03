"""BondLinkedStructuredProduct wrapper schema: the BLI product-level object.

Scope, per ``docs/19_bli_wrapper_schema_preflight.md``: this wrapper binds
exactly one ``DepositLeg`` and exactly one ``BondOption`` and owns the
*relationship* between them — currency consistency, the derived
``participation_ratio``, and the date/settlement guardrails that neither
component can check on its own. It is the first BLI **product-level**
object; ``DepositLeg`` and ``BondOption`` remain components consumed by it.

It does not own anything already owned by either component (no duplicated
notional, rate, or date fields), and it does not carry market data, a
resolved rate, or any pricing output (``docs/19`` §3, §9). No pricing,
payoff calculation, QuantLib, market-data snapshot, or input bundle is
implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from shiori_pricing_lab.products._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.products.bond_option import BondOption
from shiori_pricing_lab.products.deposit_leg import DepositLeg
from shiori_pricing_lab.products.enums import PrincipalRepaymentRule, SettlementType


@dataclass(frozen=True)
class BondLinkedStructuredProduct:
    """BLI wrapper: exactly one ``DepositLeg`` + exactly one ``BondOption``.

    ``participation_ratio`` is a **derived-only** property
    (``bond_option.notional / deposit_leg.deposit_notional``) — it is
    deliberately not a constructor field, so no independently stored value
    can ever contradict the two notionals it is computed from
    (``docs/19`` §6, ``docs/15`` §3.3). An optional, tolerance-validated
    input field remains a possible future addition if a concrete
    term-sheet/Excel-reconciliation consumer needs one, but is not added by
    this schema.
    """

    product_id: str
    deposit_leg: DepositLeg
    bond_option: BondOption
    # Fixed discriminator: not a constructor argument, so a caller cannot
    # build a BLI-shaped trade that serializes as anything other than
    # "BOND_LINKED_STRUCTURED_PRODUCT".
    product_type: str = field(init=False, default="BOND_LINKED_STRUCTURED_PRODUCT")

    def __post_init__(self) -> None:
        _require_non_blank(self.product_id, "product_id")

        if not isinstance(self.deposit_leg, DepositLeg):
            raise TypeError("deposit_leg must be a DepositLeg")
        if not isinstance(self.bond_option, BondOption):
            raise TypeError("bond_option must be a BondOption")

        # No silent cross-currency BLI wrapper in MVP (docs/19 §7): an
        # FX/quanto dimension is out of scope for the smallest usable MVP.
        if self.deposit_leg.currency is not self.bond_option.currency:
            raise ValueError(
                "deposit_leg.currency "
                f"({self.deposit_leg.currency.value}) must match "
                f"bond_option.currency ({self.bond_option.currency.value})"
            )

        # MVP narrows BondOption (which remains general) to CASH settlement
        # at the wrapper level; physical delivery is out of MVP scope
        # (docs/19 §8).
        if self.bond_option.settlement_type is not SettlementType.CASH:
            raise ValueError(
                "bond_option.settlement_type must be CASH for a "
                f"BondLinkedStructuredProduct (got {self.bond_option.settlement_type.value})"
            )

        # Only PrincipalRepaymentRule member today; kept explicit so this
        # check does not silently pass if the vocabulary is ever widened
        # without also reviewing the wrapper's payoff-combination logic
        # (docs/19 §8).
        if (
            self.deposit_leg.principal_repayment_rule
            is not PrincipalRepaymentRule.FULL_PRINCIPAL_AT_MATURITY
        ):
            raise ValueError(
                "deposit_leg.principal_repayment_rule must be "
                "FULL_PRINCIPAL_AT_MATURITY for a BondLinkedStructuredProduct "
                f"(got {self.deposit_leg.principal_repayment_rule.value})"
            )

        deposit_start = _parse_iso_date(self.deposit_leg.start_date, "deposit_leg.start_date")
        deposit_maturity = _parse_iso_date(
            self.deposit_leg.maturity_date, "deposit_leg.maturity_date"
        )
        option_expiry = _parse_iso_date(self.bond_option.expiry_date, "bond_option.expiry_date")

        # Conservative MVP rule (docs/19 §7, resolved by this implementation
        # slice): an option that expires before the funding deposit even
        # begins is economically confusing and rejected outright.
        if option_expiry < deposit_start:
            raise ValueError(
                "bond_option.expiry_date "
                f"({self.bond_option.expiry_date}) must be on or after "
                f"deposit_leg.start_date ({self.deposit_leg.start_date})"
            )

        # Mandatory MVP guardrail (docs/19 §7): the option's *effective*
        # settlement date, not just its expiry date, must fall on or before
        # the deposit's maturity. This is a plain calendar-day
        # approximation -- no business-day rolling, no holiday calendar, no
        # calendar engine -- and must not be skipped or deferred.
        effective_settlement = option_expiry + timedelta(
            days=self.bond_option.settlement_lag_days
        )
        if effective_settlement > deposit_maturity:
            raise ValueError(
                "bond_option effective settlement date "
                f"({effective_settlement.isoformat()}, from expiry_date "
                f"{self.bond_option.expiry_date} + settlement_lag_days "
                f"{self.bond_option.settlement_lag_days}) must be on or "
                f"before deposit_leg.maturity_date ({self.deposit_leg.maturity_date})"
            )

    @property
    def participation_ratio(self) -> float:
        """Derived only: ``bond_option.notional / deposit_leg.deposit_notional``.

        Never a stored field, so it can never disagree with the two
        notionals it is computed from (docs/19 §6).
        """

        return self.bond_option.notional / self.deposit_leg.deposit_notional
