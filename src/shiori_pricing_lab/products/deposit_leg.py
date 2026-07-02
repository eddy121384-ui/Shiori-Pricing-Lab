"""DepositLeg schema: the deposit / funding component of a future BLI wrapper.

Scope, per ``docs/18_deposit_leg_schema_preflight.md``: ``DepositLeg`` is a
deal-term schema component consumed by a future ``BondLinkedStructuredProduct``
wrapper, not a standalone tradeable product (docs/18 §1) — the same pattern as
``CrossCurrencyLeg`` embedded in ``CrossCurrencySwap``.

``deposit_rate_mode`` (docs/18 §4) fixes exactly which of three mutually
exclusive rate-source fields is populated:

- ``FIXED_RATE``: ``fixed_deposit_rate`` is a frozen contractual trade term.
- ``TREASURY_FTP_REFERENCE``: ``ftp_rate_selector`` is a stable selector
  (currency/tenor/quote_side) only. The resolved rate and its applicable
  business date are **not** schema fields — they belong to a future
  ``MarketDataSnapshot`` / MVP input bundle / audit trail, resolved per
  pricing run (docs/18 §4.2, §8).
- ``MANUAL_VERIFIED_RATE``: ``manual_input_reference`` is an opaque marker
  only. The actual manually supplied rate and its provenance (source,
  as-of, entered-by, run id) are **not** schema fields — they belong to the
  input-bundle / audit-provenance layer (docs/18 §4.3).

``DepositLeg`` therefore never carries a ``business_date``, a resolved
market rate, or manual-rate audit metadata. No Treasury FTP parser,
ingestion, ``MarketDataSnapshot``, pricing engine, or wrapper is implemented
here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from shiori_pricing_lab.products._validation import _parse_iso_date, _require_non_blank
from shiori_pricing_lab.products.enums import (
    Currency,
    DepositRateMode,
    PrincipalRepaymentRule,
    TreasuryFTPQuoteSide,
    TreasuryFTPTenor,
    coerce_enum,
)


def _require_finite_number(value: object, field_name: str) -> None:
    """Reject anything that is not a real, finite ``int``/``float``.

    ``bool`` is an ``int`` subclass in Python, so it is excluded explicitly.
    Mirrors ``bond_option._require_finite_number`` (PR #50's Codex-P2 fix);
    duplicated locally rather than imported since it is a small,
    product-module-local helper, matching the existing repo convention of
    keeping per-product validation in its own module.
    """

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number, got {value!r}")


@dataclass(frozen=True)
class TreasuryFTPRateSelector:
    """A stable Treasury FTP lookup key: currency/tenor/quote_side only.

    Deliberately excludes ``business_date``, ``as_of_timestamp``,
    ``source_file_name``, ``loaded_at``, and any resolved rate
    (``rate_percent`` / ``rate_decimal``) — those describe a *specific dated
    record* from the Treasury FTP rate matrix, which is market/funding data
    resolved per pricing run, not a stable product-schema selector
    (docs/18 §2.1, §4.2).
    """

    currency: Currency
    tenor: TreasuryFTPTenor
    quote_side: TreasuryFTPQuoteSide

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        object.__setattr__(self, "tenor", coerce_enum(self.tenor, TreasuryFTPTenor, "tenor"))
        object.__setattr__(
            self,
            "quote_side",
            coerce_enum(self.quote_side, TreasuryFTPQuoteSide, "quote_side"),
        )


@dataclass(frozen=True)
class DepositLeg:
    """Deposit leg: pure deal terms and rate-source selectors only.

    Exactly one of ``fixed_deposit_rate`` / ``ftp_rate_selector`` /
    ``manual_input_reference`` is populated, matching ``deposit_rate_mode``;
    the other two must be ``None`` (docs/18 §4, §11).
    """

    deposit_leg_id: str
    deposit_notional: float
    currency: Currency
    start_date: str
    maturity_date: str
    deposit_rate_mode: DepositRateMode
    principal_repayment_rule: PrincipalRepaymentRule
    tenor: TreasuryFTPTenor | None = None
    fixed_deposit_rate: float | None = None
    ftp_rate_selector: TreasuryFTPRateSelector | None = None
    manual_input_reference: str | None = None
    # Fixed discriminator: not a standalone product_type, since DepositLeg is
    # a leg/component consumed by a future wrapper, not a tradeable product
    # in its own right (docs/18 §1).
    leg_type: str = field(init=False, default="DEPOSIT_LEG")

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", coerce_enum(self.currency, Currency, "currency"))
        object.__setattr__(
            self,
            "deposit_rate_mode",
            coerce_enum(self.deposit_rate_mode, DepositRateMode, "deposit_rate_mode"),
        )
        object.__setattr__(
            self,
            "principal_repayment_rule",
            coerce_enum(
                self.principal_repayment_rule,
                PrincipalRepaymentRule,
                "principal_repayment_rule",
            ),
        )
        if self.tenor is not None:
            object.__setattr__(self, "tenor", coerce_enum(self.tenor, TreasuryFTPTenor, "tenor"))

        _require_non_blank(self.deposit_leg_id, "deposit_leg_id")

        _require_finite_number(self.deposit_notional, "deposit_notional")
        if not self.deposit_notional > 0:
            raise ValueError(f"deposit_notional must be positive, got {self.deposit_notional}")

        start = _parse_iso_date(self.start_date, "start_date")
        maturity = _parse_iso_date(self.maturity_date, "maturity_date")
        if maturity <= start:
            raise ValueError(
                f"maturity_date ({self.maturity_date}) must be after start_date ({self.start_date})"
            )

        if self.deposit_rate_mode is DepositRateMode.FIXED_RATE:
            self._validate_fixed_rate_mode()
        elif self.deposit_rate_mode is DepositRateMode.TREASURY_FTP_REFERENCE:
            self._validate_treasury_ftp_reference_mode()
        else:  # DepositRateMode.MANUAL_VERIFIED_RATE
            self._validate_manual_verified_rate_mode()

    def _validate_fixed_rate_mode(self) -> None:
        if self.ftp_rate_selector is not None:
            raise ValueError("ftp_rate_selector must be None when deposit_rate_mode is FIXED_RATE")
        if self.manual_input_reference is not None:
            raise ValueError(
                "manual_input_reference must be None when deposit_rate_mode is FIXED_RATE"
            )
        if self.fixed_deposit_rate is None:
            raise ValueError("fixed_deposit_rate is required when deposit_rate_mode is FIXED_RATE")
        # Deposit/funding rates may legitimately be zero or negative, so only
        # a finiteness/type check applies here, deliberately no sign check
        # (same reasoning as BondOption.strike_yield, PR #50).
        _require_finite_number(self.fixed_deposit_rate, "fixed_deposit_rate")

    def _validate_treasury_ftp_reference_mode(self) -> None:
        if self.fixed_deposit_rate is not None:
            raise ValueError(
                "fixed_deposit_rate must be None when deposit_rate_mode is TREASURY_FTP_REFERENCE"
            )
        if self.manual_input_reference is not None:
            raise ValueError(
                "manual_input_reference must be None when deposit_rate_mode is "
                "TREASURY_FTP_REFERENCE"
            )
        if self.ftp_rate_selector is None:
            raise ValueError(
                "ftp_rate_selector is required when deposit_rate_mode is TREASURY_FTP_REFERENCE"
            )
        if not isinstance(self.ftp_rate_selector, TreasuryFTPRateSelector):
            raise TypeError("ftp_rate_selector must be a TreasuryFTPRateSelector")

        if self.ftp_rate_selector.currency is not self.currency:
            raise ValueError(
                "ftp_rate_selector.currency "
                f"({self.ftp_rate_selector.currency.value}) must match "
                f"DepositLeg.currency ({self.currency.value})"
            )
        if self.tenor is not None and self.ftp_rate_selector.tenor is not self.tenor:
            raise ValueError(
                "ftp_rate_selector.tenor "
                f"({self.ftp_rate_selector.tenor.value}) must match "
                f"DepositLeg.tenor ({self.tenor.value})"
            )

    def _validate_manual_verified_rate_mode(self) -> None:
        if self.fixed_deposit_rate is not None:
            raise ValueError(
                "fixed_deposit_rate must be None when deposit_rate_mode is MANUAL_VERIFIED_RATE"
            )
        if self.ftp_rate_selector is not None:
            raise ValueError(
                "ftp_rate_selector must be None when deposit_rate_mode is MANUAL_VERIFIED_RATE"
            )
        if self.manual_input_reference is None:
            raise ValueError(
                "manual_input_reference is required when deposit_rate_mode is "
                "MANUAL_VERIFIED_RATE"
            )
        _require_non_blank(self.manual_input_reference, "manual_input_reference")
