// Kernel-input curve-selection conformance for RATES_KERNEL_INPUT_V1 (docs/33 Issue #225 section 5.1).
//
// WHAT THIS SUITE PROVES:
//   * ACCEPT — a contract-valid OIS invocation whose `curve_selection` names an explicit discount
//     curve and an explicit forecast curve for the embedded `resolved_swap.floating_index`, where both
//     selected curves carry a pricing-usable `value_type`, is accepted, and its identities are stable
//     across the decode (declared kernel fingerprint still verifies, canonical bytes are reproduced).
//   * REFUSE — a curve selected for valuation whose `value_type` is
//     `PAR_RATE_DISPLAY_ONLY_NEVER_PRICE` is refused for BOTH valuation roles (discount selection and
//     forecast selection), at the offending curve's own locator inside the embedded curve set.
//     Contract: docs/33 line 485 (section 7.2) — "A curve whose `value_type` is
//     `PAR_RATE_DISPLAY_ONLY_NEVER_PRICE` (the `USOSFR*` adapter output) is display-only by contract
//     and can never be consumed as a pricing input; the kernel refuses it as `INVALID_PRODUCT` /
//     `MISSING_MARKET_DATA` with the reason named." The same file restates it in the value-type table
//     (line 517 (section 7.4): "Adapter output only; kernel refuses as pricing input") and names the
//     case under `INVALID_PRODUCT` (line 1266: "display-only curve as pricing input"). The
//     prohibition is unconditional for a CONSUMED curve, so it is role-independent and is enforced
//     before any discount/forecast role-compatibility rule could look satisfied.
//   * DO NOT OVER-REFUSE — a display-only curve that is merely EMBEDDED in the curve set and never
//     selected for valuation is still accepted: the contract forbids consuming that representation as
//     a pricing input, not carrying it.
//   * RE-HASH — neither refusal is a stale-fingerprint artifact. Every negative fixture is re-hashed
//     through `assign_derived_identities`, so its declared `content_fingerprint` equals the digest of
//     its own canonical preimage exactly, and it is still refused for the contract reason. Re-hashing
//     cannot buy acceptance.
//   * REQUIRE THE SELECTION — a selected OIS/swaption whose `forecast_curve_by_index` does not name the
//     embedded `resolved_swap.floating_index` (including the empty map) is refused as a missing
//     required field at that exact map key. Contract: docs/33 line 289 keys the map by the "required
//     floating index -> explicit selected forecast curve", and docs/33 line 309 forbids the kernel from
//     choosing the first eligible curve, inferring selection from array position, or deriving a
//     preferred curve internally. V1 values exactly two product shapes, and each prices a floating leg
//     whose index is `resolved_swap.floating_index` (the vanilla swap itself, or the underlying swap of
//     a swaption), so an absent entry cannot be completed later without the inference the contract
//     forbids. Only the ENTRY is required: the referenced curve still has to pass the role/index and
//     display-only rules, and an unselected curve elsewhere in the curve set is still accepted.
//
// FIXTURE POLICY: everything below is a CONTRACT-ONLY synthetic fixture (`SYNTHETIC_FIXTURE` source):
// curves carry the contract's own `UNRESOLVED` methodology tokens, per-period schedules and fixing
// facts are absent rather than invented, and the two numeric pillar ordinates and the unit notional are
// placeholders that no pricing path reads (#227 implements no pricing). No market data, no curve
// construction value, no fixing value, and no convention value is asserted or implied.
#include <gtest/gtest.h>

#include <cstddef>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <variant>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/market.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"

namespace {

using Shiori::rates::dto::CanonicalMembers;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::ConventionSetId;
using Shiori::rates::dto::Curve;
using Shiori::rates::dto::CurveCommon;
using Shiori::rates::dto::CurvePillar;
using Shiori::rates::dto::CurveRole;
using Shiori::rates::dto::Currency;
using Shiori::rates::dto::Date;
using Shiori::rates::dto::DiscountCurve;
using Shiori::rates::dto::ExerciseTerms;
using Shiori::rates::dto::FloatingIndex;
using Shiori::rates::dto::ForwardCurve;
using Shiori::rates::dto::MarketSource;
using Shiori::rates::dto::MethodSpec;
using Shiori::rates::dto::ModelInput;
using Shiori::rates::dto::NumericWithUnit;
using Shiori::rates::dto::PayReceive;
using Shiori::rates::dto::PillarValueType;
using Shiori::rates::dto::ProductType;
using Shiori::rates::dto::RateRepresentation;
using Shiori::rates::dto::RatesKernelInput;
using Shiori::rates::dto::SettlementTerms;
using Shiori::rates::dto::Timestamp;
using Shiori::rates::dto::Unit;
using Shiori::rates::dto::ValueOrReason;
using Shiori::rates::dto::ValueState;
using Shiori::rates::dto::VolatilityInput;

// The contract's own token for a methodology value that is not yet approved (docs/33 section 7.0).
constexpr const char* kUnresolved = "UNRESOLVED";
// The two open REDs whose unresolved state forces these references to stay structured non-values.
constexpr const char* kRed02NotLocked = "RED_02_NOT_LOCKED";

constexpr const char* kValuationDate = "2026-06-10";
constexpr const char* kLaterPillarDate = "2027-06-10";
constexpr const char* kCapturedAt = "2026-06-10T21:00:00Z";
constexpr const char* kAssembledBy = "test_kernel_input_selection_conformance";

[[nodiscard]] Date iso_date(const char* text) { return Date(std::string(text)); }

[[nodiscard]] Timestamp iso_timestamp(const char* text) { return Timestamp(std::string(text)); }

// docs/33 section 7.5: interpolation/extrapolation are REQUIRED fields whose VALUES are UNRESOLVED, so
// the fixture carries the contract's token rather than an invented method.
[[nodiscard]] MethodSpec unresolved_method_spec() {
  MethodSpec spec;
  spec.method_id = kUnresolved;
  spec.method_version = kUnresolved;
  spec.parameters = std::nullopt;
  return spec;
}

// docs/33 section 7.2 `rate_representation`: every leaf is an open RED value.
[[nodiscard]] RateRepresentation unresolved_rate_representation() {
  RateRepresentation representation;
  representation.compounding = kUnresolved;
  representation.day_count = kUnresolved;
  representation.accrual_boundary = kUnresolved;
  return representation;
}

// The numeric ordinate is a placeholder: #227 has no pricing path that reads it, and this suite
// asserts nothing about curve construction or curve shape.
[[nodiscard]] CurvePillar synthetic_pillar(const char* pillar_date, double placeholder_ordinate) {
  CurvePillar pillar;
  pillar.pillar_date = iso_date(pillar_date);
  pillar.maturity_label = ValueOrReason<std::string>::not_applicable();
  pillar.value_state = ValueState::kResolved;
  pillar.value = placeholder_ordinate;
  // docs/33 section 7.4: a column-free synthetic pillar uses structured NOT_APPLICABLE, never an
  // invented source column name.
  pillar.source_column = ValueOrReason<std::string>::not_applicable();
  return pillar;
}

struct CurveFlavor {
  CurveRole role = CurveRole::kDiscount;
  bool forward = false;
  bool display_only = false;
  FloatingIndex index = FloatingIndex::kUsdSofr;
};

[[nodiscard]] CurveCommon synthetic_curve_common(const CurveFlavor& flavor) {
  CurveCommon common;
  common.curve_id = std::string();  // content-derived; never supplied by the fixture
  common.currency = Currency::kUsd;
  common.curve_role = flavor.role;
  // docs/33 section 7.2: a DISCOUNT curve requires index_id NOT_APPLICABLE; a FORECAST curve requires
  // a PRESENT index association (section 7.3).
  if (flavor.forward) {
    common.index_id = ValueOrReason<FloatingIndex>::of(flavor.index);
  } else {
    common.index_id = ValueOrReason<FloatingIndex>::not_applicable();
  }
  common.valuation_date = iso_date(kValuationDate);
  common.reference_date = iso_date(kValuationDate);
  common.reference_date_reason = ValueOrReason<std::string>::not_applicable();
  // docs/33 section 7.4 pairing. The display-only flavor is exactly the adapter representation the
  // contract forbids consuming; the pricing-usable flavor is unitless DISCOUNT_FACTOR / RATIO, which
  // selects no rate convention.
  if (flavor.display_only) {
    common.value_type = PillarValueType::kParRateDisplayOnlyNeverPrice;
    common.value_unit = Unit::kPercentRawUnconverted;
  } else {
    common.value_type = PillarValueType::kDiscountFactor;
    common.value_unit = Unit::kRatio;
  }
  common.pillars = {synthetic_pillar(kValuationDate, 1.0),
                    synthetic_pillar(kLaterPillarDate, 0.99)};
  common.rate_representation = unresolved_rate_representation();
  common.interpolation = unresolved_method_spec();
  common.extrapolation = unresolved_method_spec();
  common.source_provenance.source = MarketSource::kSyntheticFixture;
  // One quote timestamp per pillar; both are structured NOT_APPLICABLE because this fixture has no
  // captured quote and must not invent one.
  common.source_provenance.quote_timestamps = {ValueOrReason<Timestamp>::not_applicable(),
                                               ValueOrReason<Timestamp>::not_applicable()};
  common.source_provenance.adapter_name = "synthetic_conformance_fixture";
  common.source_provenance.adapter_version = "test";
  common.construction_methodology_id = kUnresolved;
  common.construction_methodology_version = kUnresolved;
  common.content_fingerprint = std::string();  // content-derived
  return common;
}

[[nodiscard]] Curve synthetic_curve(const CurveFlavor& flavor) {
  Curve curve;
  if (flavor.forward) {
    ForwardCurve forward;
    forward.common = synthetic_curve_common(flavor);
    // USD_SOFR is an overnight index with no tenor dimension (docs/33 section 7.3).
    forward.index_tenor = ValueOrReason<std::string>::not_applicable();
    // Both references are unresolved open REDs (#224 D5 / RED-02), so they stay structured
    // UNRESOLVED_METHODOLOGY rather than a fabricated id.
    forward.fixing_calendar_ref = ValueOrReason<std::string>::none(
        Shiori::rates::dto::unresolved_methodology_reason(kRed02NotLocked));
    forward.observation_rules_ref = ValueOrReason<std::string>::none(
        Shiori::rates::dto::unresolved_methodology_reason(kRed02NotLocked));
    curve.body = std::move(forward);
  } else {
    DiscountCurve discount;
    discount.common = synthetic_curve_common(flavor);
    curve.body = std::move(discount);
  }
  return curve;
}

struct KernelInputOptions {
  bool display_only_discount = false;
  bool display_only_forecast = false;
  bool embed_unselected_display_only_curve = false;
};

// Builds a contract-valid RATES_KERNEL_INPUT_V1 OIS invocation. Curve order follows the canonical
// CurveSet order of docs/33 line 1226 — ascending (curve_role, index_id_sort_key, curve_id) — so the
// document is canonical without any decoder-side reordering:
// [0] the selected DISCOUNT curve, [1] the selected FORECAST curve for
// `resolved_swap.floating_index`, and optionally [2] a FORECAST display-only curve for a different
// index (USD_SOFR_TERM_3M) that is embedded but never selected.
[[nodiscard]] RatesKernelInput build_kernel_input(const KernelInputOptions& options) {
  RatesKernelInput input;

  // Section 5.1 applicability: OIS carries its own product identity and structured NOT_APPLICABLE for
  // every derivative-only member.
  input.valuation_product.product_id = "SYNTHETIC_OIS_PLACEHOLDER";
  input.valuation_product.product_type = ProductType::kOis;
  input.valuation_product.underlying_product_id = ValueOrReason<std::string>::not_applicable();

  input.resolved_swap.product_id = input.valuation_product.product_id;
  input.resolved_swap.convention_set_id = ConventionSetId::kUsdSofrOisV1;
  input.resolved_swap.currency = Currency::kUsd;
  input.resolved_swap.notional = NumericWithUnit(1.0, Unit::kCurrencyAmount);
  input.resolved_swap.pay_receive = PayReceive::kPay;
  input.resolved_swap.fixed_rate = NumericWithUnit(0.0, Unit::kDecimalAnnual);
  input.resolved_swap.spread = NumericWithUnit(0.0, Unit::kDecimalAnnual);
  input.resolved_swap.floating_index = FloatingIndex::kUsdSofr;
  // The #224 section 2.3 resolved schedule/accrual/observation structure is carried byte-exactly but
  // is not typed by #227 (kernel_input.hpp lines 16-19). An empty object is that placeholder: no
  // schedule, accrual, fixing or date is invented here.
  input.resolved_swap.resolved = CanonicalValue::make_object(CanonicalMembers{});
  input.resolved_swap.provenance.source = MarketSource::kSyntheticFixture;
  input.resolved_swap.provenance.adapter_name = "synthetic_conformance_fixture";
  input.resolved_swap.provenance.adapter_version = "test";

  input.market_snapshot.valuation_date = iso_date(kValuationDate);
  input.market_snapshot.captured_at = iso_timestamp(kCapturedAt);
  input.market_snapshot.source = MarketSource::kSyntheticFixture;
  input.market_snapshot.source_detail = "contract-conformance synthetic fixture";
  input.market_snapshot.provenance.assembled_by = kAssembledBy;
  input.market_snapshot.provenance.adapter_versions = {{"synthetic_conformance_fixture", "test"}};
  // Section 5.1: for an OIS the volatility member is structured NOT_APPLICABLE.
  input.market_snapshot.volatility_ref = ValueOrReason<std::string>::not_applicable();
  input.market_snapshot.volatility_input = ValueOrReason<VolatilityInput>::not_applicable();

  input.market_snapshot.curve_set.valuation_date = iso_date(kValuationDate);
  input.market_snapshot.curve_set.base_currency = Currency::kUsd;
  input.market_snapshot.curve_set.construction.construction_methodology_id = kUnresolved;
  input.market_snapshot.curve_set.construction.construction_methodology_version = kUnresolved;
  input.market_snapshot.curve_set.construction.construction_inputs_ref =
      ValueOrReason<std::string>::not_applicable();
  input.market_snapshot.curve_set.provenance.sources = {MarketSource::kSyntheticFixture};
  input.market_snapshot.curve_set.provenance.captured_at = iso_timestamp(kCapturedAt);

  CurveFlavor discount_flavor;
  discount_flavor.role = CurveRole::kDiscount;
  discount_flavor.display_only = options.display_only_discount;
  input.market_snapshot.curve_set.curves.push_back(synthetic_curve(discount_flavor));

  CurveFlavor forecast_flavor;
  forecast_flavor.role = CurveRole::kForecast;
  forecast_flavor.forward = true;
  forecast_flavor.display_only = options.display_only_forecast;
  input.market_snapshot.curve_set.curves.push_back(synthetic_curve(forecast_flavor));

  if (options.embed_unselected_display_only_curve) {
    // A different, tenor-dimensioned index keeps the canonical order deterministic
    // ("FORECAST"/"USD_SOFR" < "FORECAST"/"USD_SOFR_TERM_3M") and keeps this curve OUT of the
    // selection: it is carried in the curve set only.
    CurveFlavor embedded_flavor;
    embedded_flavor.role = CurveRole::kForecast;
    embedded_flavor.forward = true;
    embedded_flavor.display_only = true;
    embedded_flavor.index = FloatingIndex::kUsdSofrTerm3M;
    input.market_snapshot.curve_set.curves.push_back(synthetic_curve(embedded_flavor));
  }

  input.market_snapshot.fixing_store.valuation_date = iso_date(kValuationDate);
  // The same-day rule is RED-225-F1 (docs/33 section 8.5): entirely unresolved, so every leaf carries
  // the contract's token and no cutoff/timezone is invented.
  input.market_snapshot.fixing_store.same_day_rule.rule_id = kUnresolved;
  input.market_snapshot.fixing_store.same_day_rule.rule_version = kUnresolved;
  input.market_snapshot.fixing_store.same_day_rule.cutoff_time = kUnresolved;
  input.market_snapshot.fixing_store.same_day_rule.cutoff_time_unit = kUnresolved;
  input.market_snapshot.fixing_store.same_day_rule.timezone = kUnresolved;
  input.market_snapshot.fixing_store.provenance.assembled_by = kAssembledBy;
  input.market_snapshot.fixing_store.provenance.captured_at = iso_timestamp(kCapturedAt);
  // No fixing entries: supplying no fixing fact is data (docs/33 section 8.2 allows an empty source
  // union), and it is never permission to invent a fixing.

  input.exercise_terms = ValueOrReason<ExerciseTerms>::not_applicable();
  input.settlement_terms = ValueOrReason<SettlementTerms>::not_applicable();
  input.model_input = ValueOrReason<ModelInput>::not_applicable();

  input.valuation_context.valuation_date = iso_date(kValuationDate);
  input.valuation_context.reporting_currency = Currency::kUsd;
  input.valuation_context.valuation_context_id = std::string();  // content-derived

  // Curve ids are content-derived, so the explicit selection can only be expressed once the curves
  // hold their identity. First pass derives the curve ids; the selection is then written by
  // `curve_id` (never by array position); the second pass folds the completed selection into the
  // kernel fingerprint. Curve content does not change, so the ids derived in the first pass remain
  // exact.
  Shiori::rates::dto::assign_derived_identities(input);
  input.curve_selection.discount_curve_id =
      std::string(input.market_snapshot.curve_set.curves[0].curve_id());
  input.curve_selection.forecast_curve_by_index[std::string(
      Shiori::rates::dto::enum_to_token(input.resolved_swap.floating_index))] =
      std::string(input.market_snapshot.curve_set.curves[1].curve_id());
  Shiori::rates::dto::assign_derived_identities(input);
  return input;
}

// Decodes the fixture's own canonical document. Returns nullopt on refusal so an unexpected acceptance
// and an unexpected refusal are reported as the assertion failures they are.
[[nodiscard]] std::optional<RatesKernelInput> try_decode(const RatesKernelInput& input) {
  try {
    return RatesKernelInput::from_canonical(input.to_canonical(), "");
  } catch (const ContractViolation&) {
    return std::nullopt;
  }
}

// Every negative case below MUST be refused, so a missing refusal is reported as a test failure rather
// than folded into a sentinel that could be confused with a real refusal.
[[nodiscard]] ContractViolation refusal_for(const RatesKernelInput& input) {
  try {
    (void)RatesKernelInput::from_canonical(input.to_canonical(), "");
  } catch (const ContractViolation& violation) {
    return violation;
  }
  ADD_FAILURE() << "the document must be refused but was accepted";
  return ContractViolation(ContractViolationKind::kMissingRequiredField, "<no refusal>",
                           "no refusal was thrown");
}

}  // namespace

TEST(KernelInputSelectionConformance, AcceptsAnExplicitSelectionWithPricingUsableCurves) {
  const RatesKernelInput fixture = build_kernel_input(KernelInputOptions{});
  const std::optional<RatesKernelInput> decoded = try_decode(fixture);
  ASSERT_TRUE(decoded.has_value()) << "a contract-valid curve_selection must be accepted";

  // The explicit selection survives exactly: nothing is re-derived, reordered, defaulted or dropped.
  EXPECT_EQ(decoded->curve_selection.discount_curve_id, fixture.curve_selection.discount_curve_id);
  EXPECT_EQ(decoded->curve_selection.forecast_curve_by_index,
            fixture.curve_selection.forecast_curve_by_index);
  EXPECT_EQ(decoded->curve_selection.forecast_curve_by_index.size(), std::size_t{1});
  EXPECT_EQ(decoded->resolved_swap.floating_index, fixture.resolved_swap.floating_index);

  // The map key is the exact FloatingIndex wire token, and it names the selected forecast curve by
  // curve_id (not by array position).
  const std::string index_token =
      std::string(Shiori::rates::dto::enum_to_token(fixture.resolved_swap.floating_index));
  const auto selected = fixture.curve_selection.forecast_curve_by_index.find(index_token);
  ASSERT_NE(selected, fixture.curve_selection.forecast_curve_by_index.end());
  EXPECT_EQ(std::string(fixture.market_snapshot.curve_set.curves[1].curve_id()), selected->second);

  // Identities stay stable: the declared kernel fingerprint still equals the digest of the canonical
  // preimage (which includes curve_selection), and the child identities survive the round trip.
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(*decoded), decoded->content_fingerprint);
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(fixture), fixture.content_fingerprint);
  EXPECT_EQ(decoded->content_fingerprint, fixture.content_fingerprint);
  EXPECT_EQ(decoded->market_snapshot.snapshot_id, fixture.market_snapshot.snapshot_id);
  EXPECT_EQ(decoded->resolved_swap.content_fingerprint, fixture.resolved_swap.content_fingerprint);
  EXPECT_EQ(decoded->valuation_context.valuation_context_id,
            fixture.valuation_context.valuation_context_id);
  EXPECT_EQ(decoded->to_canonical().dump(), fixture.to_canonical().dump());
}

TEST(KernelInputSelectionConformance, RefusesAnOisWithoutAForecastSelectionForItsFloatingIndex) {
  RatesKernelInput fixture = build_kernel_input(KernelInputOptions{});
  // The empty map is the loophole the requirement closes: with no entry at all the per-entry loop has
  // nothing to validate and the document used to be accepted.
  fixture.curve_selection.forecast_curve_by_index.clear();
  // Re-hash, so the refusal that follows cannot be attributed to a stale declared fingerprint.
  Shiori::rates::dto::assign_derived_identities(fixture);
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(fixture), fixture.content_fingerprint);

  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kMissingRequiredField);
  // The pointer names the exact missing entry, so the refusal is attributable to one map key rather
  // than to the selection as a whole.
  const std::string index_token =
      std::string(Shiori::rates::dto::enum_to_token(fixture.resolved_swap.floating_index));
  EXPECT_EQ(refusal.pointer(), "/curve_selection/forecast_curve_by_index/" + index_token);
}

TEST(KernelInputSelectionConformance, RefusesADisplayOnlyCurveSelectedAsTheDiscountCurve) {
  KernelInputOptions options;
  options.display_only_discount = true;
  const RatesKernelInput fixture = build_kernel_input(options);

  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kInvariantViolation);
  // The refusal names the offending curve itself: entry 0 of the embedded curve set.
  EXPECT_EQ(refusal.pointer(), "/market_snapshot/curve_set/curves/0");
  EXPECT_NE(refusal.message().find("PAR_RATE_DISPLAY_ONLY_NEVER_PRICE"), std::string::npos);
}

TEST(KernelInputSelectionConformance, RefusesADisplayOnlyCurveSelectedAsTheForecastCurve) {
  KernelInputOptions options;
  options.display_only_forecast = true;
  const RatesKernelInput fixture = build_kernel_input(options);

  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kInvariantViolation);
  // Same prohibition, same kind: the role the curve was given does not change the answer.
  EXPECT_EQ(refusal.pointer(), "/market_snapshot/curve_set/curves/1");
  EXPECT_NE(refusal.message().find("PAR_RATE_DISPLAY_ONLY_NEVER_PRICE"), std::string::npos);
}

TEST(KernelInputSelectionConformance, KeepsAnEmbeddedDisplayOnlyCurveThatIsNotSelected) {
  KernelInputOptions options;
  options.embed_unselected_display_only_curve = true;
  const RatesKernelInput fixture = build_kernel_input(options);
  ASSERT_EQ(fixture.market_snapshot.curve_set.curves.size(), std::size_t{3});

  // The third curve is display-only, but it is neither the discount selection nor a forecast
  // selection: it is only carried in the curve set.
  const Curve& embedded = fixture.market_snapshot.curve_set.curves[2];
  EXPECT_EQ(embedded.common().value_type, PillarValueType::kParRateDisplayOnlyNeverPrice);
  const std::string embedded_id = std::string(embedded.curve_id());
  EXPECT_NE(fixture.curve_selection.discount_curve_id, embedded_id);
  EXPECT_EQ(fixture.curve_selection.forecast_curve_by_index.count(
                std::string(Shiori::rates::dto::enum_to_token(FloatingIndex::kUsdSofrTerm3M))),
            std::size_t{0});
  EXPECT_TRUE(try_decode(fixture).has_value())
      << "the prohibition is on consuming the representation, not on carrying it in the curve set";
}

TEST(KernelInputSelectionConformance, RehashedDocumentsCannotBypassTheDisplayOnlyRefusal) {
  KernelInputOptions options;
  options.display_only_forecast = true;
  const RatesKernelInput fixture = build_kernel_input(options);

  // Re-hashing is proven, not assumed: the declared fingerprint is the digest of this document's own
  // canonical preimage, so the refusal that follows cannot be attributed to a stale fingerprint.
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(fixture), fixture.content_fingerprint);

  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(refusal.pointer(), "/market_snapshot/curve_set/curves/1");

  // The only difference from the accepted fixture is the selected curve's representation, so the
  // refusal is caused by that representation and not by the surrounding document.
  EXPECT_TRUE(try_decode(build_kernel_input(KernelInputOptions{})).has_value());
}

// ---------------------------------------------------------------------------------------------
// CONFIRMED V1 convention binding (docs/32 section 6 lines 166-167)
//
// `USD_SOFR_OIS_V1` denotes `currency: USD` and `floating_index: USD_SOFR`; every other convention
// parameter remains UNRESOLVED (RED-02) and is not enforced here. The ConventionSet is authoritative
// (docs/32 section 2 R2), so a swap that claims it while carrying another currency or index is a
// contradicted claim: it is refused rather than coerced, and neither field is relabelled.
// ---------------------------------------------------------------------------------------------
TEST(KernelInputConventionBinding, AcceptsTheConfirmedUsdSofrBindingAndKeepsTheDocument) {
  const RatesKernelInput fixture = build_kernel_input(KernelInputOptions{});
  const std::optional<RatesKernelInput> decoded = try_decode(fixture);
  ASSERT_TRUE(decoded.has_value()) << "the confirmed USD + USD_SOFR binding must be accepted";
  EXPECT_EQ(decoded->resolved_swap.convention_set_id, ConventionSetId::kUsdSofrOisV1);
  EXPECT_EQ(decoded->resolved_swap.currency, Currency::kUsd);
  EXPECT_EQ(decoded->resolved_swap.floating_index, FloatingIndex::kUsdSofr);
  // Nothing is rewritten to match the convention set: the encoded document is reproduced byte-exactly.
  EXPECT_EQ(decoded->to_canonical().dump(), fixture.to_canonical().dump());
}

TEST(KernelInputConventionBinding, RefusesAUsdSofrOisConventionSetCarryingAnotherCurrency) {
  RatesKernelInput fixture = build_kernel_input(KernelInputOptions{});
  // The swap keeps the USD_SOFR_OIS_V1 convention set and claims EUR instead. Changing only the
  // embedded swap's currency leaves the rest of the document self-consistent, and re-hashing proves the
  // refusal cannot be attributed to a stale resolved_swap / kernel fingerprint.
  fixture.resolved_swap.currency = Currency::kEur;
  Shiori::rates::dto::assign_derived_identities(fixture);
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(fixture), fixture.content_fingerprint);

  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(refusal.pointer(), "/resolved_swap/currency");
  EXPECT_NE(refusal.message().find("USD_SOFR_OIS_V1"), std::string::npos) << refusal.message();
  EXPECT_NE(refusal.message().find("currency=USD"), std::string::npos) << refusal.message();
}

TEST(KernelInputConventionBinding, RefusesAUsdSofrOisConventionSetCarryingAnotherIndex) {
  RatesKernelInput fixture = build_kernel_input(KernelInputOptions{});
  fixture.resolved_swap.floating_index = FloatingIndex::kEurEstr;
  Shiori::rates::dto::assign_derived_identities(fixture);
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(fixture), fixture.content_fingerprint);

  // The refusal is the convention binding, not the curve-selection obligation that a different index
  // would also trigger later: it is reported at the swap's own field.
  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(refusal.pointer(), "/resolved_swap/floating_index");
  EXPECT_NE(refusal.message().find("floating_index=USD_SOFR"), std::string::npos) << refusal.message();
}

// ---------------------------------------------------------------------------------------------
// Derivative identity distinctness (docs/33 section 5.1 line 303)
//
// "For a derivative over that swap, `valuation_product.product_id` identifies the derivative
// trade/position, `underlying_product_id` MUST be PRESENT and equal `resolved_swap.product_id`, and the
// derivative id MUST NOT be inferred from or substituted by the underlying id." The vanilla-swap shape
// (`product_id == resolved_swap.product_id`) is therefore refused in the derivative branch.
// ---------------------------------------------------------------------------------------------
namespace {
// Re-shapes the contract-valid OIS fixture into the derivative branch of the section 5.1 applicability
// table WITHOUT inventing a swaption payload: only the product identity is changed and the underlying
// reference is made PRESENT and equal to the embedded swap id, exactly as line 303 requires. The
// document is then refused for the derivative payloads it does not carry, which keeps the identity rule
// separable from the payload rules.
[[nodiscard]] RatesKernelInput build_derivative_identity_probe(std::string derivative_id) {
  RatesKernelInput input = build_kernel_input(KernelInputOptions{});
  input.valuation_product.product_type = ProductType::kSwaption;
  input.valuation_product.product_id = std::move(derivative_id);
  input.valuation_product.underlying_product_id =
      ValueOrReason<std::string>::of(input.resolved_swap.product_id);
  Shiori::rates::dto::assign_derived_identities(input);
  return input;
}
}  // namespace

TEST(DerivativeIdentityConformance, RefusesADerivativeWhoseProductIdIsTheUnderlyingSwapId) {
  RatesKernelInput fixture = build_derivative_identity_probe("SYNTHETIC_OIS_PLACEHOLDER");
  ASSERT_EQ(fixture.valuation_product.product_id, fixture.resolved_swap.product_id);
  ASSERT_TRUE(fixture.valuation_product.underlying_product_id.present);
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(fixture), fixture.content_fingerprint);

  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kIdentityMismatch);
  EXPECT_EQ(refusal.pointer(), "/valuation_product/product_id");
  EXPECT_NE(refusal.message().find("distinct"), std::string::npos) << refusal.message();
}

TEST(DerivativeIdentityConformance, KeepsRequiringTheDerivativePayloadsForADistinctIdentity) {
  RatesKernelInput fixture =
      build_derivative_identity_probe("SYNTHETIC_SWAPTION_DERIVATIVE_PLACEHOLDER");
  ASSERT_NE(fixture.valuation_product.product_id, fixture.resolved_swap.product_id);
  EXPECT_EQ(Shiori::rates::dto::kernel_input_content_fingerprint(fixture), fixture.content_fingerprint);

  // A distinct derivative id passes the distinctness rule, so the NEXT rule in the derivative branch is
  // what refuses this document: the missing PRESENT exercise terms. That is the evidence that a
  // legitimate derivative identity is not rejected as if it were collapsed with the underlying one, and
  // that the derivative payloads stay mandatory.
  const ContractViolation refusal = refusal_for(fixture);
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kInvalidTaggedUnionState);
  EXPECT_EQ(refusal.pointer(), "/exercise_terms");
  EXPECT_NE(refusal.message().find("swaption requires PRESENT exercise terms"), std::string::npos)
      << refusal.message();
}

TEST(DerivativeIdentityConformance, KeepsTheExplicitUnderlyingIdAndTheOisEqualityRule) {
  // Retained behaviour, both halves of line 303: a derivative without a PRESENT underlying id is still
  // refused at that field, and a vanilla OIS whose product id differs from the embedded swap id keeps
  // its own equality refusal (the OIS branch is untouched by the distinctness rule).
  RatesKernelInput derivative =
      build_derivative_identity_probe("SYNTHETIC_SWAPTION_DERIVATIVE_PLACEHOLDER");
  derivative.valuation_product.underlying_product_id = ValueOrReason<std::string>::not_applicable();
  Shiori::rates::dto::assign_derived_identities(derivative);
  const ContractViolation missing_underlying = refusal_for(derivative);
  EXPECT_EQ(missing_underlying.kind(), ContractViolationKind::kInvalidTaggedUnionState);
  EXPECT_EQ(missing_underlying.pointer(), "/valuation_product/underlying_product_id");

  RatesKernelInput ois = build_kernel_input(KernelInputOptions{});
  ois.valuation_product.product_id = "SYNTHETIC_OTHER_OIS_ID";
  Shiori::rates::dto::assign_derived_identities(ois);
  const ContractViolation ois_equality = refusal_for(ois);
  EXPECT_EQ(ois_equality.kind(), ContractViolationKind::kIdentityMismatch);
  EXPECT_EQ(ois_equality.pointer(), "/valuation_product");
}
