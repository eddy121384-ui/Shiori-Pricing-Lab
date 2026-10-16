// Kernel-input identity conformance: the #225 section 15.4 non-recursive preimage rule at the two
// places where #227 publishes or re-checks a `RatesKernelInput` fingerprint.
//
// WHY THIS FILE EXISTS
// An object's own `content_fingerprint` is EXCLUDED from its own preimage (#225 section 15.4), so the
// declared fingerprint of a document is the digest of the canonical payload WITHOUT that one field.
// The full-document digest (the digest of the complete serialized document, which contains the
// fingerprint field itself) is therefore a DIFFERENT value, and two defects followed from confusing
// them:
//
//   1. `engine::canonicalize_kernel_input` compared the declared fingerprint against
//      `fingerprint_of_canonical_bytes(bytes)` over the complete re-serialized document. That
//      comparison can never succeed for a valid document, so the operation could not reach its
//      success path at all. It now compares against `dto::kernel_input_content_fingerprint(input)`,
//      the contract's own kernel-input preimage helper.
//   2. `dto::freeze_kernel_input` PUBLISHED the full-document digest of the canonical bytes instead
//      of the re-derived contract fingerprint. Cache keys and replay records consume that value as
//      the contract's `inputs_fingerprint` (#226 section 7.2 K1), so publishing a different digest
//      would give replay and the cache a value no peer can reproduce from the preimage rule.
//
// THE CONTRACT FINGERPRINT IS A DERIVED VALUE, NEVER AN ASSERTED ONE
// These tests pin all three identities that the contract requires to be the same value - the declared
// fingerprint of the document, the fingerprint `freeze_kernel_input` publishes, and the identity the
// cache layer stores as `inputs_fingerprint` - and they prove the published value is NOT the
// full-document digest. The refusal cases prove the fix is not a blanket acceptance: a document whose
// declared fingerprint IS the full-document digest, and a document with an arbitrary wrong
// fingerprint, are both refused with the fingerprint-mismatch kind.
#include <gtest/gtest.h>

#include <cstddef>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/immutable.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/market.hpp"
#include "shiori_rates/dto/time.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/engine/engine.hpp"

namespace {

using Shiori::rates::dto::CacheKey;
using Shiori::rates::dto::CacheKeyComponents;
using Shiori::rates::dto::CanonicalMembers;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::Currency;
using Shiori::rates::dto::Curve;
using Shiori::rates::dto::CurveCommon;
using Shiori::rates::dto::CurvePillar;
using Shiori::rates::dto::CurveRole;
using Shiori::rates::dto::CurveSelection;
using Shiori::rates::dto::CurveSet;
using Shiori::rates::dto::Date;
using Shiori::rates::dto::DiscountCurve;
using Shiori::rates::dto::FixingStore;
using Shiori::rates::dto::FloatingIndex;
using Shiori::rates::dto::ForwardCurve;
using Shiori::rates::dto::FrozenKernelInput;
using Shiori::rates::dto::MarketSnapshot;
using Shiori::rates::dto::MarketSource;
using Shiori::rates::dto::MethodSpec;
using Shiori::rates::dto::NumericWithUnit;
using Shiori::rates::dto::PayReceive;
using Shiori::rates::dto::PillarValueType;
using Shiori::rates::dto::ProductType;
using Shiori::rates::dto::PublishedKernelInput;
using Shiori::rates::dto::RateRepresentation;
using Shiori::rates::dto::RatesKernelInput;
using Shiori::rates::dto::ResolvedSwap;
using Shiori::rates::dto::Timestamp;
using Shiori::rates::dto::Unit;
using Shiori::rates::dto::ValueOrReason;
using Shiori::rates::dto::ValueState;
using Shiori::rates::dto::ValuationContext;
using Shiori::rates::dto::ValuationProduct;
using Shiori::rates::dto::VolatilityInput;
using Shiori::rates::dto::assign_derived_identities;
using Shiori::rates::dto::enum_to_token;
using Shiori::rates::dto::freeze_kernel_input;
using Shiori::rates::dto::is_valid_fingerprint;
using Shiori::rates::dto::kFingerprintHexLength;
using Shiori::rates::dto::kUnresolvedToken;
using Shiori::rates::dto::kernel_input_content_fingerprint;
using Shiori::rates::dto::parse_canonical_json;
using Shiori::rates::dto::unresolved_methodology_reason;

// ---------------------------------------------------------------------------------------------
// Fixture constants. Every one of them is either a synthetic contract value or the contract's OWN
// explicit UNRESOLVED token (#225 sections 7.0/7.5/7.6/8.5); no market level, no methodology value and
// no financial convention is invented anywhere below. AGENTS.md rule 4 forbids fabricating market
// data, so the curves deliberately carry NO numeric pillar at all: #225 section 7.2 defines the
// unresolved-pillar state (`value_state=NULL_WITH_REASON`, `value=null`, structured
// `unresolved_reason`) precisely so such a curve is representable without zero-filling a rate.
// ---------------------------------------------------------------------------------------------
constexpr std::string_view kFixtureValuationDate = "2024-01-01";
constexpr std::string_view kFixtureCapturedAt = "2024-01-01T00:00:00Z";
constexpr std::string_view kFixtureProductId = "SYNTHETIC_CONTRACT_FIXTURE_OIS";
constexpr std::string_view kFixtureAdapterIdentity = "NO_ADAPTER_SYNTHETIC_CONTRACT_FIXTURE";
constexpr std::string_view kFixtureSourceDetail =
    "SYNTHETIC CONTRACT-CONFORMANCE FIXTURE; NO MARKET DATA";

// Explicit structured-reason codes for the fixture's unresolved states. #225 section 4.1 makes
// `reason.code` a mandatory machine-readable string and leaves its vocabulary open, so these name the
// RED item that actually owns each gap instead of leaving a bare category.
constexpr std::string_view kFixtureUnresolvedCode = "RED_01_CURVE_METHODOLOGY_UNRESOLVED";
constexpr std::string_view kFixtureConstructionInputsCode = "RED_01_CONSTRUCTION_INPUTS_UNRESOLVED";
constexpr std::string_view kFixtureCalendarCode = "RED_02_FIXING_CALENDAR_UNRESOLVED";
constexpr std::string_view kFixtureObservationRulesCode = "RED_02_OBSERVATION_RULES_UNRESOLVED";

[[nodiscard]] const std::vector<std::string>& fixture_unresolved_field_paths() {
  // #225 section 15.4: `MarketSnapshot.diagnostics.unresolved_fields` is lexicographically sorted and
  // duplicate-free before serialization. The paths use the engine's own locator vocabulary (the
  // JSON-pointer form documented in dto/error.hpp, for example
  // `/market_snapshot/curve_set/curves/0/pillars/2`), and every entry names a field that this fixture
  // really does carry in its unresolved state.
  static const std::vector<std::string> kPaths = {
      "/market_snapshot/curve_set/construction/construction_inputs_ref",
      "/market_snapshot/curve_set/construction/construction_methodology_id",
      "/market_snapshot/curve_set/construction/construction_methodology_version",
      "/market_snapshot/curve_set/curves/0/extrapolation/method_id",
      "/market_snapshot/curve_set/curves/0/extrapolation/method_version",
      "/market_snapshot/curve_set/curves/0/interpolation/method_id",
      "/market_snapshot/curve_set/curves/0/interpolation/method_version",
      "/market_snapshot/curve_set/curves/0/pillars/0/value_state",
      "/market_snapshot/curve_set/curves/0/rate_representation/accrual_boundary",
      "/market_snapshot/curve_set/curves/0/rate_representation/compounding",
      "/market_snapshot/curve_set/curves/0/rate_representation/day_count",
      "/market_snapshot/curve_set/curves/1/extrapolation/method_id",
      "/market_snapshot/curve_set/curves/1/extrapolation/method_version",
      "/market_snapshot/curve_set/curves/1/fixing_calendar_ref",
      "/market_snapshot/curve_set/curves/1/interpolation/method_id",
      "/market_snapshot/curve_set/curves/1/interpolation/method_version",
      "/market_snapshot/curve_set/curves/1/observation_rules_ref",
      "/market_snapshot/curve_set/curves/1/pillars/0/value_state",
      "/market_snapshot/curve_set/curves/1/rate_representation/accrual_boundary",
      "/market_snapshot/curve_set/curves/1/rate_representation/compounding",
      "/market_snapshot/curve_set/curves/1/rate_representation/day_count",
      "/market_snapshot/fixing_store/same_day_rule/cutoff_time",
      "/market_snapshot/fixing_store/same_day_rule/cutoff_time_unit",
      "/market_snapshot/fixing_store/same_day_rule/rule_id",
      "/market_snapshot/fixing_store/same_day_rule/rule_version",
      "/market_snapshot/fixing_store/same_day_rule/timezone",
  };
  return kPaths;
}

[[nodiscard]] Date fixture_date(std::string_view iso_date) { return Date(std::string(iso_date)); }

[[nodiscard]] Timestamp fixture_timestamp(std::string_view iso_timestamp) {
  return Timestamp(std::string(iso_timestamp));
}

[[nodiscard]] ValueOrReason<std::string> unresolved_text(std::string_view code) {
  return ValueOrReason<std::string>::none(unresolved_methodology_reason(std::string(code)));
}

// An unresolved pillar: present at its coordinate, no numeric value, with an explicit structured
// reason. #225 section 7.2: dropping it, storing NaN, zero-filling it or inventing a neighbouring
// value is forbidden, which is exactly why the fixture has no rate to fabricate.
[[nodiscard]] CurvePillar unresolved_pillar(std::string_view pillar_date) {
  CurvePillar pillar;
  pillar.pillar_date = fixture_date(pillar_date);
  pillar.maturity_label = ValueOrReason<std::string>::not_applicable();
  pillar.value_state = ValueState::kNullWithReason;
  pillar.value = std::nullopt;
  pillar.unresolved_reason = unresolved_methodology_reason(std::string(kFixtureUnresolvedCode));
  pillar.source_column = ValueOrReason<std::string>::not_applicable();
  return pillar;
}

// The fields shared by DISCOUNT_CURVE_V1 and FORWARD_CURVE_V1 (#225 section 7.2). Each curve carries
// one unresolved pillar; `source_provenance.quote_timestamps` therefore carries exactly one entry, as
// section 7.2 requires (one per pillar, in pillar order).
[[nodiscard]] CurveCommon fixture_curve_common(CurveRole role,
                                               ValueOrReason<FloatingIndex> index_id,
                                               std::string_view pillar_date) {
  CurveCommon common;
  common.currency = Currency::kUsd;
  common.curve_role = role;
  common.index_id = std::move(index_id);
  common.valuation_date = fixture_date(kFixtureValuationDate);
  // reference_date == valuation_date, so section 7.2 requires a NOT_APPLICABLE reason.
  common.reference_date = fixture_date(kFixtureValuationDate);
  common.reference_date_reason = ValueOrReason<std::string>::not_applicable();
  // A usable (non display-only) value type: ZERO_RATE_CONTINUOUS must carry DECIMAL_ANNUAL, and
  // PAR_RATE_DISPLAY_ONLY_NEVER_PRICE is never a pricing input (section 7.4).
  common.value_type = PillarValueType::kZeroRateContinuous;
  common.value_unit = Unit::kDecimalAnnual;
  common.pillars.push_back(unresolved_pillar(pillar_date));
  // Rate representation / interpolation / extrapolation values are UNRESOLVED-RED (#225 sections
  // 7.0/7.5); the fixture carries the contract's own unresolved token rather than choosing one.
  common.rate_representation = RateRepresentation{std::string(kUnresolvedToken),
                                                  std::string(kUnresolvedToken),
                                                  std::string(kUnresolvedToken)};
  common.interpolation =
      MethodSpec{std::string(kUnresolvedToken), std::string(kUnresolvedToken), std::nullopt};
  common.extrapolation =
      MethodSpec{std::string(kUnresolvedToken), std::string(kUnresolvedToken), std::nullopt};
  common.source_provenance.source = MarketSource::kSyntheticFixture;
  common.source_provenance.quote_timestamps.push_back(ValueOrReason<Timestamp>::not_applicable());
  common.source_provenance.adapter_name = std::string(kFixtureAdapterIdentity);
  common.source_provenance.adapter_version = std::string(kFixtureAdapterIdentity);
  // Section 7.1: an exact echo of the enclosing CurveSet construction methodology (RED-01).
  common.construction_methodology_id = std::string(kUnresolvedToken);
  common.construction_methodology_version = std::string(kUnresolvedToken);
  return common;
}

// The two curves are built in section 15.4 canonical order for `CurveSet.curves`: ascending
// `(curve_role, index_id_sort_key, curve_id)`. `DISCOUNT` < `FORECAST` in UTF-8 byte order, so the
// discount curve is the first entry and the forecast curve follows it.
[[nodiscard]] Curve fixture_discount_curve() {
  DiscountCurve curve;
  curve.common = fixture_curve_common(CurveRole::kDiscount,
                                      ValueOrReason<FloatingIndex>::not_applicable(), "2025-01-01");
  return Curve{std::move(curve)};
}

[[nodiscard]] Curve fixture_forecast_curve() {
  ForwardCurve curve;
  curve.common = fixture_curve_common(CurveRole::kForecast,
                                      ValueOrReason<FloatingIndex>::of(FloatingIndex::kUsdSofr),
                                      "2025-07-01");
  // USD_SOFR is an overnight index: it has no tenor dimension, so section 7.3 makes index_tenor
  // NOT_APPLICABLE. The calendar and observation-rule references are still RED, so they are carried
  // as explicit unresolved-methodology states rather than omitted.
  curve.index_tenor = ValueOrReason<std::string>::not_applicable();
  curve.fixing_calendar_ref = unresolved_text(kFixtureCalendarCode);
  curve.observation_rules_ref = unresolved_text(kFixtureObservationRulesCode);
  return Curve{std::move(curve)};
}

[[nodiscard]] CurveSet fixture_curve_set() {
  CurveSet curve_set;
  curve_set.valuation_date = fixture_date(kFixtureValuationDate);
  curve_set.base_currency = Currency::kUsd;
  curve_set.curves.push_back(fixture_discount_curve());
  curve_set.curves.push_back(fixture_forecast_curve());
  curve_set.construction.construction_methodology_id = std::string(kUnresolvedToken);
  curve_set.construction.construction_methodology_version = std::string(kUnresolvedToken);
  curve_set.construction.construction_inputs_ref = unresolved_text(kFixtureConstructionInputsCode);
  // Section 7.1/8.4: `sources` is the deterministic union of the contributing sources, and a set may
  // not claim a source no child carries. Both curves declare SYNTHETIC_FIXTURE.
  curve_set.provenance.sources = {MarketSource::kSyntheticFixture};
  curve_set.provenance.captured_at = fixture_timestamp(kFixtureCapturedAt);
  return curve_set;
}

[[nodiscard]] FixingStore fixture_fixing_store() {
  FixingStore fixing_store;
  fixing_store.valuation_date = fixture_date(kFixtureValuationDate);
  // #225 section 8.5 / RED-225-F1: the complete same-day rule is UNRESOLVED, and the fixture says so
  // in all five members instead of assuming a cutoff or a timezone.
  fixing_store.same_day_rule.rule_id = std::string(kUnresolvedToken);
  fixing_store.same_day_rule.rule_version = std::string(kUnresolvedToken);
  fixing_store.same_day_rule.cutoff_time = std::string(kUnresolvedToken);
  fixing_store.same_day_rule.cutoff_time_unit = std::string(kUnresolvedToken);
  fixing_store.same_day_rule.timezone = std::string(kUnresolvedToken);
  // No fixing is claimed: a HISTORICAL entry would require a PRESENT rate (section 8.3), and the
  // fixture does not fabricate one. An empty store is representable and its provenance union is
  // therefore empty, exactly as section 8.2 requires.
  fixing_store.provenance.assembled_by = std::string(kFixtureAdapterIdentity);
  fixing_store.provenance.captured_at = fixture_timestamp(kFixtureCapturedAt);
  return fixing_store;
}

[[nodiscard]] MarketSnapshot fixture_market_snapshot() {
  MarketSnapshot snapshot;
  snapshot.valuation_date = fixture_date(kFixtureValuationDate);
  snapshot.captured_at = fixture_timestamp(kFixtureCapturedAt);
  snapshot.source = MarketSource::kSyntheticFixture;
  snapshot.source_detail = std::string(kFixtureSourceDetail);
  snapshot.curve_set = fixture_curve_set();
  snapshot.fixing_store = fixture_fixing_store();
  // Section 5.1 applicability for OIS: volatility is structured NULL_WITH_REASON(NOT_APPLICABLE), and
  // section 6.2 requires the ref and the payload to agree on their state.
  snapshot.volatility_ref = ValueOrReason<std::string>::not_applicable();
  snapshot.volatility_input = ValueOrReason<VolatilityInput>::not_applicable();
  snapshot.provenance.assembled_by = std::string(kFixtureAdapterIdentity);
  snapshot.diagnostics.unresolved_fields = fixture_unresolved_field_paths();
  return snapshot;
}

// The #224 trade economics. `resolved` is the documented structural placeholder of
// kernel_input.hpp: #224 section 2.3 does not publish its per-period field shape, so the fixture
// carries the empty canonical object and invents no unpublished field. The numeric economics are the
// neutral structural values the shape requires; they claim no market level.
[[nodiscard]] ResolvedSwap fixture_resolved_swap() {
  ResolvedSwap swap;
  swap.product_id = std::string(kFixtureProductId);
  swap.convention_set_id = Shiori::rates::dto::ConventionSetId::kUsdSofrOisV1;
  swap.currency = Currency::kUsd;
  swap.notional = NumericWithUnit(1.0, Unit::kCurrencyAmount);
  swap.pay_receive = PayReceive::kPay;
  swap.fixed_rate = NumericWithUnit(0.0, Unit::kDecimalAnnual);
  swap.spread = NumericWithUnit(0.0, Unit::kDecimalAnnual);
  swap.floating_index = FloatingIndex::kUsdSofr;
  swap.resolved = CanonicalValue::make_object(CanonicalMembers{});
  swap.provenance.source = MarketSource::kSyntheticFixture;
  return swap;
}

[[nodiscard]] ValuationProduct fixture_valuation_product() {
  ValuationProduct product;
  product.product_id = std::string(kFixtureProductId);
  product.product_type = ProductType::kOis;
  // Section 5.1: a vanilla swap carries a structured NOT_APPLICABLE underlying id.
  product.underlying_product_id = ValueOrReason<std::string>::not_applicable();
  return product;
}

[[nodiscard]] ValuationContext fixture_valuation_context() {
  ValuationContext context;
  context.valuation_date = fixture_date(kFixtureValuationDate);
  context.reporting_currency = Currency::kUsd;
  // `valuation_context_id` is derived by assign_derived_identities.
  return context;
}

// ---------------------------------------------------------------------------------------------
// The fixture: a genuinely valid RATES_KERNEL_INPUT_V1 document, plus the canonical bytes and the
// declared fingerprint that identity must produce.
// ---------------------------------------------------------------------------------------------
struct KernelInputFixture {
  RatesKernelInput value;
  std::string canonical_bytes;
  std::string declared_fingerprint;
};

[[nodiscard]] KernelInputFixture make_kernel_input_fixture() {
  MarketSnapshot snapshot = fixture_market_snapshot();
  // The curve ids are derived identities, so they must exist before `curve_selection` can name them.
  assign_derived_identities(snapshot);

  CurveSelection selection;
  selection.discount_curve_id = snapshot.curve_set.curves[0].common().curve_id;
  // The forecast map key is the PRESENT canonical enum token of the curve's own index_id, which
  // section 5.1 requires to match exactly.
  selection.forecast_curve_by_index.emplace(
      std::string(enum_to_token(FloatingIndex::kUsdSofr)),
      snapshot.curve_set.curves[1].common().curve_id);

  RatesKernelInput input;
  input.valuation_product = fixture_valuation_product();
  input.resolved_swap = fixture_resolved_swap();
  input.market_snapshot = std::move(snapshot);
  input.curve_selection = std::move(selection);
  // Section 5.1 applicability for OIS: exercise, settlement, model and volatility are all structured
  // NULL_WITH_REASON(NOT_APPLICABLE).
  input.exercise_terms = ValueOrReason<Shiori::rates::dto::ExerciseTerms>::not_applicable();
  input.settlement_terms = ValueOrReason<Shiori::rates::dto::SettlementTerms>::not_applicable();
  input.model_input = ValueOrReason<Shiori::rates::dto::ModelInput>::not_applicable();
  input.valuation_context = fixture_valuation_context();

  // Innermost-first derivation of every id and fingerprint, exactly as a producer publishes it.
  assign_derived_identities(input);

  KernelInputFixture fixture;
  fixture.declared_fingerprint = input.content_fingerprint;
  fixture.canonical_bytes = input.to_canonical().dump();
  fixture.value = std::move(input);
  return fixture;
}

// Replaces ONLY the top-level `content_fingerprint` member of a canonical document, leaving every
// nested object (and therefore every nested derived identity) untouched.
[[nodiscard]] std::string replace_declared_fingerprint(const std::string& canonical_bytes,
                                                       const std::string& fingerprint) {
  const CanonicalValue document = parse_canonical_json(canonical_bytes);
  CanonicalMembers members;
  members.reserve(document.as_object().members.size());
  for (const auto& member : document.as_object().members) {
    if (member.first == "content_fingerprint") {
      members.emplace_back(member.first, CanonicalValue(fingerprint));
    } else {
      members.emplace_back(member);
    }
  }
  return CanonicalValue::make_object(std::move(members)).dump();
}

[[nodiscard]] std::string refusal_token_of(const Shiori::rates::engine::OperationOutcome& outcome) {
  return outcome.refusal_token;
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// (A/B) The fixture is genuinely valid: the engine accepts it, re-serializes it byte-identically and
// reports the declared fingerprint. Before the engine fix this operation refused every valid input.
// ---------------------------------------------------------------------------------------------
TEST(KernelIdentityConformance, ValidFixtureIsAcceptedAndReserializedByteIdentically) {
  const KernelInputFixture fixture = make_kernel_input_fixture();

  // A fixture that is not itself a well-formed annotated document would make everything below
  // meaningless, so it is asserted first.
  ASSERT_EQ(fixture.declared_fingerprint.size(), kFingerprintHexLength);
  ASSERT_TRUE(is_valid_fingerprint(fixture.declared_fingerprint));
  ASSERT_FALSE(fixture.canonical_bytes.empty());
  EXPECT_EQ(kernel_input_content_fingerprint(fixture.value), fixture.declared_fingerprint);

  // Determinism: the fixture is a pure function of its own constants - no clock, no path, no file, no
  // ambient state - so building it again must produce the identical document and identity.
  const KernelInputFixture again = make_kernel_input_fixture();
  EXPECT_EQ(again.canonical_bytes, fixture.canonical_bytes);
  EXPECT_EQ(again.declared_fingerprint, fixture.declared_fingerprint);

  const Shiori::rates::engine::OperationOutcome outcome =
      Shiori::rates::engine::canonicalize_kernel_input(fixture.canonical_bytes);
  ASSERT_TRUE(outcome.success) << refusal_token_of(outcome) << " at " << outcome.refusal_pointer
                               << ": " << outcome.refusal_message;
  // Byte-identical re-serialization: the canonical form of the decoded document IS the document.
  EXPECT_EQ(outcome.payload_json, fixture.canonical_bytes);
  EXPECT_EQ(outcome.fingerprint, fixture.declared_fingerprint);

  // And `validate_kernel_input` accepts the same document, so the acceptance is not specific to the
  // re-serializing operation.
  const Shiori::rates::engine::OperationOutcome validated =
      Shiori::rates::engine::validate_kernel_input(fixture.canonical_bytes);
  EXPECT_TRUE(validated.success) << refusal_token_of(validated) << " at " << validated.refusal_pointer
                                 << ": " << validated.refusal_message;
  EXPECT_EQ(validated.fingerprint, fixture.declared_fingerprint);
}

// ---------------------------------------------------------------------------------------------
// (C) The heart of the round: the declared fingerprint, the published fingerprint and the cache-key
// input identity are ONE value, the frozen canonical bytes are untouched, and that value is NOT the
// full-document digest.
// ---------------------------------------------------------------------------------------------
TEST(KernelIdentityConformance, DeclaredPublishedAndCacheKeyIdentitiesAreTheSameFingerprint) {
  const KernelInputFixture fixture = make_kernel_input_fixture();

  const std::shared_ptr<const FrozenKernelInput> frozen = freeze_kernel_input(fixture.value);
  ASSERT_NE(frozen, nullptr);
  EXPECT_EQ(frozen->canonical_bytes, fixture.canonical_bytes);
  EXPECT_EQ(frozen->content_fingerprint, fixture.declared_fingerprint);

  const PublishedKernelInput published = PublishedKernelInput::publish(fixture.value);
  EXPECT_TRUE(published.valid());
  EXPECT_EQ(published.canonical_view(), fixture.canonical_bytes);
  EXPECT_EQ(published.content_fingerprint(), fixture.declared_fingerprint);
  // The published value's own DTO content must carry the same fingerprint, so an in-process consumer
  // and a peer reading the bytes agree.
  EXPECT_EQ(published.value().content_fingerprint, fixture.declared_fingerprint);

  // (i) == (ii): the declared identity and the frozen/published identity are one value.
  EXPECT_EQ(frozen->content_fingerprint, published.content_fingerprint());

  // The full-document digest is a DIFFERENT value under #225 section 15.4, because the object's own
  // fingerprint field is excluded from its own preimage. Publishing it would be the old defect.
  const std::string full_document_digest =
      Shiori::rates::dto::fingerprint_of_canonical_bytes(fixture.canonical_bytes);
  EXPECT_NE(full_document_digest, fixture.declared_fingerprint);
  EXPECT_NE(published.content_fingerprint(), full_document_digest);
  EXPECT_NE(frozen->content_fingerprint, full_document_digest);

  // (iii) the cache layer consumes exactly that value as K1 `inputs_fingerprint` (#226 section 7.2).
  // The non-input components below are local, explicitly-labelled scaffolding for the key's required
  // slots: this lane implements no pricing methodology, so the honest identity it can report is the
  // engine's own, never an invented methodology version.
  const Shiori::rates::engine::BuildIdentity& build = Shiori::rates::engine::build_identity();
  CacheKeyComponents components;
  components.inputs_fingerprint = published.content_fingerprint();
  components.engine_version = std::string(Shiori::rates::engine::engine_version());
  components.methodology_id = std::string(Shiori::rates::engine::engine_method());
  components.methodology_version = std::string(Shiori::rates::engine::engine_version());
  components.quantlib_version = std::string(kFixtureAdapterIdentity);
  components.quantlib_macro_configuration = std::string(kFixtureAdapterIdentity);
  components.acquisition_mode = build.acquisition_mode;
  components.dependency_baseline_and_triplet =
      build.vcpkg_baseline + ":" + build.vcpkg_triplet;
  components.build_numeric_flags = build.numeric_flags;

  const CacheKey key = CacheKey::from_components(components, "");
  EXPECT_EQ(key.components.inputs_fingerprint, fixture.declared_fingerprint);
  EXPECT_EQ(key.digest, key.compute_digest());

  // The key genuinely consumes the input identity: changing only K1 changes the digest.
  CacheKeyComponents other_inputs = components;
  other_inputs.inputs_fingerprint = std::string(kFingerprintHexLength, 'b');
  EXPECT_NE(CacheKey::from_components(other_inputs, "").digest, key.digest);
}

// ---------------------------------------------------------------------------------------------
// (D.i) A document that declares the FULL-DOCUMENT digest of its own canonical bytes is refused with
// the fingerprint-mismatch kind. This is the value the old engine comparison demanded, and it is not
// the contract fingerprint, so accepting it would accept a fabricated identity.
// ---------------------------------------------------------------------------------------------
TEST(KernelIdentityConformance, RefusesADeclaredFullDocumentDigest) {
  const KernelInputFixture fixture = make_kernel_input_fixture();
  const std::string full_document_digest =
      Shiori::rates::dto::fingerprint_of_canonical_bytes(fixture.canonical_bytes);
  ASSERT_TRUE(is_valid_fingerprint(full_document_digest));
  ASSERT_NE(full_document_digest, fixture.declared_fingerprint);

  const std::string tampered =
      replace_declared_fingerprint(fixture.canonical_bytes, full_document_digest);
  ASSERT_NE(tampered, fixture.canonical_bytes);

  const Shiori::rates::engine::OperationOutcome outcome =
      Shiori::rates::engine::canonicalize_kernel_input(tampered);
  EXPECT_FALSE(outcome.success);
  EXPECT_TRUE(outcome.payload_json.empty());
  EXPECT_EQ(outcome.refusal_token,
            std::string(Shiori::rates::dto::to_token(ContractViolationKind::kFingerprintMismatch)));
  EXPECT_FALSE(outcome.refusal_pointer.empty());
}

// ---------------------------------------------------------------------------------------------
// (D.ii) An arbitrary wrong declared fingerprint (and a one-character corruption of the valid one) is
// refused the same way: the fix is not a blanket acceptance of whatever the payload asserts.
// ---------------------------------------------------------------------------------------------
TEST(KernelIdentityConformance, RefusesAnArbitraryDeclaredFingerprint) {
  const KernelInputFixture fixture = make_kernel_input_fixture();
  const std::string wrong_token = std::string(kFingerprintHexLength, '0');
  ASSERT_NE(wrong_token, fixture.declared_fingerprint);

  const Shiori::rates::engine::OperationOutcome zeroed =
      Shiori::rates::engine::canonicalize_kernel_input(
          replace_declared_fingerprint(fixture.canonical_bytes, wrong_token));
  EXPECT_FALSE(zeroed.success);
  EXPECT_EQ(zeroed.refusal_token,
            std::string(Shiori::rates::dto::to_token(ContractViolationKind::kFingerprintMismatch)));

  std::string corrupted = fixture.declared_fingerprint;
  corrupted[0] = corrupted[0] == '0' ? '1' : '0';
  ASSERT_NE(corrupted, fixture.declared_fingerprint);
  const Shiori::rates::engine::OperationOutcome flipped =
      Shiori::rates::engine::canonicalize_kernel_input(
          replace_declared_fingerprint(fixture.canonical_bytes, corrupted));
  EXPECT_FALSE(flipped.success);
  EXPECT_EQ(flipped.refusal_token,
            std::string(Shiori::rates::dto::to_token(ContractViolationKind::kFingerprintMismatch)));
}

// ---------------------------------------------------------------------------------------------
// (D.iii) Freezing a value whose declared fingerprint is stale still publishes the RE-DERIVED
// contract fingerprint: freeze can never publish a fabricated identity, and it still preserves the
// complete canonical bytes of the document it owns.
// ---------------------------------------------------------------------------------------------
TEST(KernelIdentityConformance, FreezePublishesTheReDerivedFingerprintNotAStaleDeclaration) {
  const KernelInputFixture fixture = make_kernel_input_fixture();

  const std::string stale_fingerprint = std::string(kFingerprintHexLength, 'f');
  ASSERT_NE(stale_fingerprint, fixture.declared_fingerprint);

  RatesKernelInput stale = fixture.value;
  stale.content_fingerprint = stale_fingerprint;
  // A stale nested identity must be repaired by the same derivation, not published as-is.
  stale.resolved_swap.content_fingerprint = stale_fingerprint;
  stale.valuation_context.valuation_context_id = "STALE-VALUATION-CONTEXT-ID";
  stale.market_snapshot.curve_set.content_fingerprint = stale_fingerprint;

  const std::shared_ptr<const FrozenKernelInput> frozen = freeze_kernel_input(stale);
  ASSERT_NE(frozen, nullptr);
  EXPECT_EQ(frozen->content_fingerprint, fixture.declared_fingerprint);
  EXPECT_NE(frozen->content_fingerprint, stale_fingerprint);
  EXPECT_NE(frozen->content_fingerprint,
            Shiori::rates::dto::fingerprint_of_canonical_bytes(fixture.canonical_bytes));
  // The re-derivation is a repair of the DECLARED identity only: the owned document is byte-identical
  // to the fixture's canonical document.
  EXPECT_EQ(frozen->canonical_bytes, fixture.canonical_bytes);
  EXPECT_EQ(frozen->owning_value.content_fingerprint, fixture.declared_fingerprint);
  EXPECT_EQ(frozen->owning_value.resolved_swap.content_fingerprint,
            fixture.value.resolved_swap.content_fingerprint);
  EXPECT_EQ(frozen->owning_value.valuation_context.valuation_context_id,
            fixture.value.valuation_context.valuation_context_id);

  const PublishedKernelInput published = PublishedKernelInput::publish(stale);
  EXPECT_EQ(published.content_fingerprint(), fixture.declared_fingerprint);
  EXPECT_EQ(published.canonical_view(), fixture.canonical_bytes);
}
