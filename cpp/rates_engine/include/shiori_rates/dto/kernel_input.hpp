#pragma once
//
// The no-I/O kernel boundary: `RATES_KERNEL_INPUT_V1` (docs/33 Issue #225 section 5.1) plus the
// direct payloads it embeds.
//
// IMPLEMENTED FULLY (bounded, fully specified by the approved contracts):
//   * ValuationProduct / CurveSelection / ValuationContext, including the exact V1 applicability
//     matrix for `OIS` vs `SWAPTION`.
//   * ExerciseTerms (section 10.2) and SettlementTerms (section 11.2), including the CASH vs
//     PHYSICAL applicability rules and the `EUROPEAN`-exactly-one-date rule.
//   * RatesKernelInput itself: every cross-object invariant of section 5.1 (valuation-date
//     agreement, single-currency agreement, curve-role/index resolution, product applicability).
//
// DOCUMENTED STRUCTURAL PLACEHOLDERS (shape owned by a later contract implementation; carried here
// as a byte-exact canonical payload whose declared fingerprint is recomputed and verified):
//   * `ResolvedSwap.resolved` — every per-period schedule, accrual, observation, payment-date and
//     principal-exchange structure of #224 section 2.3, whose RED-02 values are unresolved and
//     whose field-level shape #224 does not publish. The retained normalized trade economics ARE
//     typed below, because #224 section 2.3 names them exactly.
//   * `ModelInput.payload` — the section 9.6 model-input content. #227 section 4 authorises
//     "model/calibration envelopes to the extent required for schema/round-trip support", and
//     #227 must not invent a model-parameter shape while RED-225-M* values are open.
//
// DELIBERATE ABSENCE: there is no `resolved_swap_id`. #225 section 10.2 states explicitly that
// `ResolvedSwap` has no id field because #224 does not define one, so a ResolvedSwap is referenced
// by its `product_id` and identified by its content fingerprint.
//
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/market.hpp"
#include "shiori_rates/dto/numeric.hpp"
#include "shiori_rates/dto/time.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"
#include "shiori_rates/dto/version.hpp"

namespace Shiori::rates::dto {

// ---------------------------------------------------------------------------------------------
// ResolvedSwap (#224; typed economics + preserved resolved payload)
// ---------------------------------------------------------------------------------------------
struct ResolvedSwap {
  std::string product_id;
  ConventionSetId convention_set_id = ConventionSetId::kUsdSofrOisV1;
  Currency currency = Currency::kUsd;
  NumericWithUnit notional;     // unit must be CURRENCY_AMOUNT
  PayReceive pay_receive = PayReceive::kPay;
  NumericWithUnit fixed_rate;   // unit must be DECIMAL_ANNUAL
  NumericWithUnit spread;       // unit must be DECIMAL_ANNUAL
  FloatingIndex floating_index = FloatingIndex::kUsdSofr;
  CanonicalValue resolved;      // #224 section 2.3 resolved structure (placeholder, see header)
  SourceProvenance provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ResolvedSwap from_canonical(const CanonicalValue& node,
                                                   const std::string& pointer);
};

// `ResolvedSwap` has no id field (#225 section 10.2), so its only derived identity is its content
// fingerprint: the canonical payload with that one field excluded.
[[nodiscard]] CanonicalValue resolved_swap_fingerprint_preimage(const ResolvedSwap& swap);
[[nodiscard]] std::string resolved_swap_content_fingerprint(const ResolvedSwap& swap);
void verify_resolved_swap_identities(const ResolvedSwap& swap, const std::string& pointer);
void assign_derived_identities(ResolvedSwap& swap);

// ---------------------------------------------------------------------------------------------
// ExerciseTerms (docs/33 section 10.2)
// ---------------------------------------------------------------------------------------------
// The underlying-start rule values are UNRESOLVED-RED and are carried as open methodology text.
struct UnderlyingReference {
  std::string underlying_product_id;
  std::string underlying_start_rule;          // UNRESOLVED (RED)
  std::string underlying_start_rule_version;  // UNRESOLVED (RED)

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static UnderlyingReference from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer);
};

struct ExerciseTerms {
  std::string exercise_terms_id;
  ExerciseStyleV1 exercise_style = ExerciseStyleV1::kEuropean;
  std::vector<Date> exercise_dates;
  ValueOrReason<std::vector<Date>> notice_dates;
  UnderlyingReference underlying_reference;
  ValueOrReason<std::string> calendar_ref;
  ValueOrReason<std::string> business_day_convention;
  ValueOrReason<std::string> timezone;
  ValueOrReason<std::string> expiry_time;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ExerciseTerms from_canonical(const CanonicalValue& node,
                                                    const std::string& pointer);
};

[[nodiscard]] CanonicalValue exercise_terms_identity_preimage(const ExerciseTerms& terms);
[[nodiscard]] CanonicalValue exercise_terms_fingerprint_preimage(const ExerciseTerms& terms,
                                                                 const std::string& terms_id);
[[nodiscard]] std::string exercise_terms_content_fingerprint(const ExerciseTerms& terms);
void verify_exercise_terms_identities(const ExerciseTerms& terms, const std::string& pointer);
void assign_derived_identities(ExerciseTerms& terms);

// ---------------------------------------------------------------------------------------------
// SettlementTerms (docs/33 section 11.2)
// ---------------------------------------------------------------------------------------------
// Every member of the cash settlement methodology is UNRESOLVED-RED (RED-225-S1); the shape exists so
// the choice is recordable, never so a default can be smuggled in.
struct CashSettlementMethodology {
  std::string methodology_id;           // UNRESOLVED
  std::string methodology_version;      // UNRESOLVED
  std::string settlement_rate_source;   // UNRESOLVED
  std::string settlement_date_rule;     // UNRESOLVED

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CashSettlementMethodology from_canonical(const CanonicalValue& node,
                                                                const std::string& pointer);
};

struct SettlementProvenance {
  MarketSource source = MarketSource::kSyntheticFixture;
  std::vector<std::string> evidence_refs;
  std::string methodology_id;       // UNRESOLVED
  std::string methodology_version;  // UNRESOLVED

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static SettlementProvenance from_canonical(const CanonicalValue& node,
                                                           const std::string& pointer);
};

struct SettlementTerms {
  std::string settlement_terms_id;
  SettlementType settlement_type = SettlementType::kCash;
  ValueOrReason<std::string> settlement_method;
  ValueOrReason<std::string> settlement_method_version;
  ValueOrReason<CashSettlementMethodology> cash_settlement_methodology;
  ValueOrReason<Date> settlement_date;
  Currency settlement_currency = Currency::kUsd;
  SettlementProvenance source_methodology_provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static SettlementTerms from_canonical(const CanonicalValue& node,
                                                      const std::string& pointer);
};

[[nodiscard]] CanonicalValue settlement_terms_identity_preimage(const SettlementTerms& terms);
[[nodiscard]] CanonicalValue settlement_terms_fingerprint_preimage(const SettlementTerms& terms,
                                                                   const std::string& terms_id);
[[nodiscard]] std::string settlement_terms_content_fingerprint(const SettlementTerms& terms);
void verify_settlement_terms_identities(const SettlementTerms& terms, const std::string& pointer);
void assign_derived_identities(SettlementTerms& terms);

// ---------------------------------------------------------------------------------------------
// ModelInput (docs/33 section 9.6 / 14.1) — STRUCTURAL PLACEHOLDER envelope
// ---------------------------------------------------------------------------------------------
struct ModelInput {
  std::string model_input_id;
  ModelId model_id = ModelId::kBlack76;
  std::string model_version;  // UNRESOLVED-RED until locked
  CanonicalValue payload;     // section 9.6 content (placeholder, see header)
  SourceProvenance provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ModelInput from_canonical(const CanonicalValue& node,
                                                 const std::string& pointer);
};

[[nodiscard]] CanonicalValue model_input_identity_preimage(const ModelInput& input);
[[nodiscard]] CanonicalValue model_input_fingerprint_preimage(const ModelInput& input,
                                                              const std::string& input_id);
[[nodiscard]] std::string model_input_content_fingerprint(const ModelInput& input);
void verify_model_input_identities(const ModelInput& input, const std::string& pointer);
void assign_derived_identities(ModelInput& input);

// ---------------------------------------------------------------------------------------------
// ValuationContext / ValuationProduct / CurveSelection
// ---------------------------------------------------------------------------------------------
struct ValuationContext {
  std::string valuation_context_id;
  Date valuation_date;
  Currency reporting_currency = Currency::kUsd;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ValuationContext from_canonical(const CanonicalValue& node,
                                                       const std::string& pointer);
};

// The #225 section 5.1 identity preimage is exactly {valuation_date, reporting_currency}; the id
// itself is excluded. Curve selection, product, snapshot, exercise, settlement and model are NOT
// part of valuation-context identity because each already participates in the kernel-input identity.
[[nodiscard]] std::string compute_valuation_context_id(const ValuationContext& context);

struct ValuationProduct {
  std::string product_id;
  ProductType product_type = ProductType::kOis;
  ValueOrReason<std::string> underlying_product_id;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ValuationProduct from_canonical(const CanonicalValue& node,
                                                       const std::string& pointer);
};

struct CurveSelection {
  std::string discount_curve_id;
  std::map<std::string, std::string> forecast_curve_by_index;  // FloatingIndex token -> curve_id

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CurveSelection from_canonical(const CanonicalValue& node,
                                                     const std::string& pointer);
};

// ---------------------------------------------------------------------------------------------
// RatesKernelInput (docs/33 section 5.1)
// ---------------------------------------------------------------------------------------------
struct RatesKernelInput {
  ValuationProduct valuation_product;
  ResolvedSwap resolved_swap;
  MarketSnapshot market_snapshot;
  CurveSelection curve_selection;
  ValueOrReason<ExerciseTerms> exercise_terms;
  ValueOrReason<SettlementTerms> settlement_terms;
  ValueOrReason<ModelInput> model_input;
  ValuationContext valuation_context;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static RatesKernelInput from_canonical(const CanonicalValue& node,
                                                       const std::string& pointer);
  void validate_invariants(const std::string& pointer) const;
};

// `RatesKernelInput` has no id field either (section 5.1 declares only `content_fingerprint`), so
// its identity is the fingerprint of its canonical payload with that one field excluded.
[[nodiscard]] CanonicalValue kernel_input_fingerprint_preimage(const RatesKernelInput& input);
[[nodiscard]] std::string kernel_input_content_fingerprint(const RatesKernelInput& input);
void verify_kernel_input_identities(const RatesKernelInput& input, const std::string& pointer);
void assign_derived_identities(RatesKernelInput& input);

}  // namespace Shiori::rates::dto
