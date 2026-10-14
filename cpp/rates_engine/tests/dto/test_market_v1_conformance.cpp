// Market DTO conformance: the #225 state/ordering rules that the first #227 round implemented with
// the wrong polarity, at the wrong place, or not at all.
//
// This is CORRECTIVE conformance (Issue #227 / PR #250). Every assertion below pins a rule that
// already exists in docs/33 (Issue #225); no new functionality, schema shape, struct field or
// contract token is introduced, and nothing here decides a methodology value.
//
//   * sections 8.2/8.3 (docs/33 lines 583-601): `PROJECTED` is the COMPLETE audit-only forecast
//     record and `FORECAST_REQUIRED` is the not-yet-projected state. The decoder used to accept a
//     projected number as `FORECAST_REQUIRED` and to refuse the complete `PROJECTED` record.
//   * sections 7.2/7.3 (docs/33 lines 439 and 496): `DISCOUNT_CURVE_V1` permits
//     `DISCOUNT | DISCOUNT_AND_FORECAST_REFERENCE_ONLY`; `FORWARD_CURVE_V1` requires `FORECAST`.
//   * section 15.4 (docs/33 lines 1233-1234): set-semantic provenance lists and
//     `MarketSnapshot.diagnostics.unresolved_fields` are lexicographically sorted and duplicate-free.
//   * section 15.4 (docs/33 line 1226): `CurveSet.curves` is in ascending canonical
//     `(curve_role, index_id_sort_key, curve_id)` order, and duplicate `curve_id` is forbidden.
//   * section 15.4 (docs/33 line 1228): `FixingStore.entries` is in ascending
//     `(index_id, observation_date)` order.
//
// Fixture posture: documents are built through the DTO layer and re-hashed with
// `assign_derived_identities` before being handed to the decoder, so no refusal below can be excused
// by a stale declared fingerprint or identity. The fixtures are contract SHAPE only: they encode no
// market evidence and no financial level. The single decimal magnitude below exists only because the
// `HISTORICAL` and `PROJECTED` states REQUIRE a PRESENT `DECIMAL_ANNUAL` value (docs/33 lines 586/596),
// and every identifier is an unmistakable fixture label.
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <functional>
#include <optional>
#include <string>
#include <utility>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/market.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"

namespace {

using Shiori::rates::dto::CanonicalItems;
using Shiori::rates::dto::CanonicalMember;
using Shiori::rates::dto::CanonicalMembers;
using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::Currency;
using Shiori::rates::dto::Curve;
using Shiori::rates::dto::CurveCommon;
using Shiori::rates::dto::CurvePillar;
using Shiori::rates::dto::CurveRole;
using Shiori::rates::dto::CurveSet;
using Shiori::rates::dto::CurveSourceProvenance;
using Shiori::rates::dto::Date;
using Shiori::rates::dto::DiscountCurve;
using Shiori::rates::dto::FixingEntry;
using Shiori::rates::dto::FixingStore;
using Shiori::rates::dto::FloatingIndex;
using Shiori::rates::dto::ForwardCurve;
using Shiori::rates::dto::MarketSnapshot;
using Shiori::rates::dto::MarketSource;
using Shiori::rates::dto::MethodSpec;
using Shiori::rates::dto::NumericWithUnit;
using Shiori::rates::dto::ObservationState;
using Shiori::rates::dto::PillarValueType;
using Shiori::rates::dto::ProjectionInputsRef;
using Shiori::rates::dto::ReasonCategory;
using Shiori::rates::dto::SameDayRule;
using Shiori::rates::dto::SnapshotDiagnostics;
using Shiori::rates::dto::SourceProvenance;
using Shiori::rates::dto::StructuredReason;
using Shiori::rates::dto::Timestamp;
using Shiori::rates::dto::Unit;
using Shiori::rates::dto::ValueOrReason;
using Shiori::rates::dto::ValueState;
using Shiori::rates::dto::VolatilityInput;
using Shiori::rates::dto::assign_derived_identities;
using Shiori::rates::dto::enum_to_token;
using Shiori::rates::dto::not_applicable_reason;
using Shiori::rates::dto::unresolved_methodology_reason;

// ---------------------------------------------------------------------------------------------
// Fixture vocabulary
// ---------------------------------------------------------------------------------------------
constexpr const char* kValuationDate = "2024-01-15";
constexpr const char* kCapturedAt = "2024-01-15T10:00:00+00:00";
// The contract's own explicitly-unresolved methodology token. It is reused here so no fixture invents
// a methodology value or an "approved" token (docs/33 section 7.0, line 388).
constexpr const char* kUnresolved = "UNRESOLVED";
// Synthetic contract-shape magnitude: `HISTORICAL` (line 586) and `PROJECTED` (line 596) both require
// a PRESENT DECIMAL_ANNUAL value. This is not a market level and must never be read as one.
constexpr double kSyntheticDecimalRate = 0.0125;

// ---------------------------------------------------------------------------------------------
// Refusal helpers (same posture as tests/dto/test_decoder_refusals.cpp: a missing refusal is a
// reported failure, never a sentinel that could be mistaken for a real refusal).
// ---------------------------------------------------------------------------------------------
[[nodiscard]] ContractViolation refusal_for(const std::function<void()>& decode, const char* what) {
  try {
    decode();
  } catch (const ContractViolation& violation) {
    return violation;
  }
  ADD_FAILURE() << "this document must be refused: " << what;
  return ContractViolation(ContractViolationKind::kInvariantViolation, "<no refusal>",
                           "no refusal was thrown");
}

template <class Dto>
void expect_round_trip(const Dto& value, const char* what) {
  const CanonicalValue encoded = value.to_canonical();
  const Dto decoded = Dto::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump())
      << "a contract-valid document must round-trip byte-identically: " << what;
}

// ---------------------------------------------------------------------------------------------
// Canonical-tree surgery: fixtures are built as DTOs, re-hashed, and then changed in canonical form
// so a test can present a document that is invalid in exactly one place while every declared identity
// stays self-consistent (the "re-hashed invalid document" case).
// ---------------------------------------------------------------------------------------------
[[nodiscard]] CanonicalValue with_member(const CanonicalValue& object, std::string key,
                                         CanonicalValue value) {
  CanonicalMembers members;
  for (const CanonicalMember& member : object.as_object().members) {
    if (member.first != key) {
      members.push_back(member);
    }
  }
  members.emplace_back(std::move(key), std::move(value));
  return CanonicalValue::make_object(std::move(members));
}

[[nodiscard]] CanonicalValue nested_member(const CanonicalValue& object, const std::string& key) {
  const CanonicalValue* node = object.find(key);
  if (node == nullptr || !node->is_object()) {
    ADD_FAILURE() << "fixture member is missing or not an object: " << key;
    return CanonicalValue::make_object(CanonicalMembers{});
  }
  return *node;
}

[[nodiscard]] CanonicalValue string_list_canonical(const std::vector<std::string>& values) {
  CanonicalItems items;
  items.reserve(values.size());
  for (const std::string& value : values) {
    items.emplace_back(value);
  }
  return CanonicalValue::make_array(std::move(items));
}

// ---------------------------------------------------------------------------------------------
// `ValueOrReason` / `StructuredReason` fixture builders
// ---------------------------------------------------------------------------------------------
[[nodiscard]] StructuredReason fixture_missing_reason(const char* code) {
  // A MISSING observation carries "structured missing/unavailable reasons" (docs/33 line 588) with no
  // contract-fixed code, so the code is explicitly a fixture label.
  return StructuredReason(ReasonCategory::kMissingMarketData, std::string(code));
}

[[nodiscard]] StructuredReason not_applicable_historical_reason() {
  // The contract's own token for a HISTORICAL entry's projection fields (docs/33 line 586).
  return StructuredReason(ReasonCategory::kNotApplicable, "NOT_APPLICABLE_HISTORICAL");
}

[[nodiscard]] StructuredReason to_be_projected_reason() {
  // The contract's own token for the FORECAST_REQUIRED value state (docs/33 line 587). The contract
  // fixes no reason CATEGORY for it, so the fixture uses UNRESOLVED_METHODOLOGY and only the code is
  // asserted by the decoder.
  return StructuredReason(ReasonCategory::kUnresolvedMethodology, "TO_BE_PROJECTED_AT_VALUATION");
}

[[nodiscard]] StructuredReason not_yet_projected_reason() {
  // FORECAST_REQUIRED projection fields are "structured not-yet-projected states" and MISSING
  // projection fields are NULL_WITH_REASON (docs/33 lines 587-588); no code is fixed, so the fixture
  // reuses the contract's own UNRESOLVED methodology token.
  return unresolved_methodology_reason(kUnresolved);
}

[[nodiscard]] ValueOrReason<NumericWithUnit> present_decimal_rate(const double value) {
  return ValueOrReason<NumericWithUnit>::of(NumericWithUnit(value, Unit::kDecimalAnnual));
}

// ---------------------------------------------------------------------------------------------
// FixingEntry fixture. The base entry is a valid MISSING record; each test moves it to the state it
// asserts, so a state rule is never accidentally satisfied by unrelated fixture content.
// ---------------------------------------------------------------------------------------------
[[nodiscard]] FixingEntry make_entry(const FloatingIndex index, const char* observation_date) {
  FixingEntry entry;
  entry.index_id = index;
  entry.observation_date = Date(std::string(observation_date));
  entry.publication_date = ValueOrReason<Date>::none(fixture_missing_reason("FIXTURE_NOT_PUBLISHED"));
  entry.publication_timestamp =
      ValueOrReason<Timestamp>::none(fixture_missing_reason("FIXTURE_NOT_PUBLISHED"));
  entry.observation_state = ObservationState::kMissing;
  entry.value = ValueOrReason<NumericWithUnit>::none(fixture_missing_reason("FIXTURE_FIXING_NOT_HELD"));
  entry.day_count_basis = kUnresolved;
  entry.source = ValueOrReason<MarketSource>::none(fixture_missing_reason("FIXTURE_SOURCE_NOT_HELD"));
  entry.quote_timestamp =
      ValueOrReason<Timestamp>::none(fixture_missing_reason("FIXTURE_NO_QUOTE_TIMESTAMP"));
  entry.version = ValueOrReason<std::string>::none(fixture_missing_reason("FIXTURE_NO_VERSION"));
  entry.projection_method_id = ValueOrReason<std::string>::none(not_yet_projected_reason());
  entry.projection_method_version = ValueOrReason<std::string>::none(not_yet_projected_reason());
  entry.projection_inputs_ref = ValueOrReason<ProjectionInputsRef>::none(not_yet_projected_reason());
  return entry;
}

// A HISTORICAL entry with a PRESENT publication_date. `HISTORICAL` requires a PRESENT value, source
// and version with projection fields NULL_WITH_REASON(NOT_APPLICABLE_HISTORICAL) (docs/33 line 586);
// the publication date is the field sections 8.3/8.4 (docs/33 lines 594 and 613) constrain against
// the embedding store's valuation date, so it is a parameter here.
[[nodiscard]] FixingEntry make_historical_entry(const FloatingIndex index, const char* observation_date,
                                                const char* publication_date) {
  FixingEntry entry = make_entry(index, observation_date);
  entry.observation_state = ObservationState::kHistorical;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kSyntheticFixture);
  entry.version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.publication_date = ValueOrReason<Date>::of(Date(std::string(publication_date)));
  entry.publication_timestamp = ValueOrReason<Timestamp>::of(Timestamp(std::string(kCapturedAt)));
  entry.projection_method_id = ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_method_version =
      ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_inputs_ref =
      ValueOrReason<ProjectionInputsRef>::none(not_applicable_historical_reason());
  return entry;
}

[[nodiscard]] ValueOrReason<ProjectionInputsRef> present_projection_reference(
    const std::string& curve_set_id, const std::string& forecast_curve_id) {
  return ValueOrReason<ProjectionInputsRef>::of(
      ProjectionInputsRef{curve_set_id, forecast_curve_id, "fixture-observation-rule", "FIXTURE_V0"});
}

// ---------------------------------------------------------------------------------------------
// Curve fixtures
// ---------------------------------------------------------------------------------------------
[[nodiscard]] CurvePillar unresolved_pillar(const char* pillar_date) {
  CurvePillar pillar;
  pillar.pillar_date = Date(std::string(pillar_date));
  pillar.maturity_label = ValueOrReason<std::string>::not_applicable();
  pillar.value_state = ValueState::kNullWithReason;
  pillar.value = std::nullopt;
  pillar.unresolved_reason = unresolved_methodology_reason(kUnresolved);
  pillar.source_column = ValueOrReason<std::string>::not_applicable();
  return pillar;
}

[[nodiscard]] MethodSpec unresolved_method_spec() {
  MethodSpec spec;
  spec.method_id = kUnresolved;
  spec.method_version = kUnresolved;
  spec.parameters = std::nullopt;
  return spec;
}

[[nodiscard]] CurveSourceProvenance curve_provenance() {
  CurveSourceProvenance provenance;
  provenance.source = MarketSource::kSyntheticFixture;
  provenance.quote_timestamps.push_back(
      ValueOrReason<Timestamp>::none(fixture_missing_reason("FIXTURE_NO_QUOTE_TIMESTAMP")));
  provenance.adapter_name = "fixture-adapter";
  provenance.adapter_version = "FIXTURE_V0";
  provenance.upstream_ids = {"fixture-batch-0001"};
  provenance.evidence_refs = {};
  return provenance;
}

[[nodiscard]] CurveCommon make_curve_common(const CurveRole role, const bool index_present) {
  CurveCommon common;
  common.currency = Currency::kUsd;
  common.curve_role = role;
  common.index_id = index_present
                        ? ValueOrReason<FloatingIndex>::of(FloatingIndex::kUsdSofr)
                        : ValueOrReason<FloatingIndex>::not_applicable();
  common.valuation_date = Date(std::string(kValuationDate));
  common.reference_date = Date(std::string(kValuationDate));
  common.reference_date_reason = ValueOrReason<std::string>::not_applicable();
  common.value_type = PillarValueType::kZeroRateContinuous;
  common.value_unit = Unit::kDecimalAnnual;
  common.pillars.push_back(unresolved_pillar("2025-01-15"));
  common.rate_representation.compounding = kUnresolved;
  common.rate_representation.day_count = kUnresolved;
  common.rate_representation.accrual_boundary = kUnresolved;
  common.interpolation = unresolved_method_spec();
  common.extrapolation = unresolved_method_spec();
  common.source_provenance = curve_provenance();
  common.construction_methodology_id = kUnresolved;
  common.construction_methodology_version = kUnresolved;
  return common;
}

// A DISCOUNT_CURVE_V1-shape curve. The combined role requires a PRESENT index (docs/33 line 479).
[[nodiscard]] Curve make_discount_curve(const CurveRole role) {
  DiscountCurve discount;
  discount.common = make_curve_common(role, role != CurveRole::kDiscount);
  Curve curve{std::move(discount)};
  assign_derived_identities(curve);
  return curve;
}

// A FORWARD_CURVE_V1-shape curve, which requires the FORECAST role (docs/33 line 496).
[[nodiscard]] Curve make_forward_curve(const FloatingIndex index) {
  ForwardCurve forward;
  forward.common = make_curve_common(CurveRole::kForecast, true);
  forward.common.index_id = ValueOrReason<FloatingIndex>::of(index);
  forward.index_tenor = ValueOrReason<std::string>::not_applicable();
  forward.fixing_calendar_ref = ValueOrReason<std::string>::none(unresolved_methodology_reason(kUnresolved));
  forward.observation_rules_ref =
      ValueOrReason<std::string>::none(unresolved_methodology_reason(kUnresolved));
  Curve curve{std::move(forward)};
  assign_derived_identities(curve);
  return curve;
}

// ---------------------------------------------------------------------------------------------
// CurveSet / FixingStore / MarketSnapshot fixtures
// ---------------------------------------------------------------------------------------------
[[nodiscard]] CurveSet make_curve_set(const std::vector<Curve>& curves) {
  CurveSet set;
  set.valuation_date = Date(std::string(kValuationDate));
  set.base_currency = Currency::kUsd;
  set.curves = curves;
  set.construction.construction_methodology_id = kUnresolved;
  set.construction.construction_methodology_version = kUnresolved;
  set.construction.construction_inputs_ref = ValueOrReason<std::string>::not_applicable();
  set.construction.evidence_refs = {};
  set.provenance.sources = {MarketSource::kSyntheticFixture};
  set.provenance.adapter_versions = {{"fixture-adapter", "FIXTURE_V0"}};
  set.provenance.upstream_ids = {"fixture-batch-0001"};
  set.provenance.captured_at = Timestamp(std::string(kCapturedAt));
  set.provenance.evidence_refs = {};
  assign_derived_identities(set);
  return set;
}

[[nodiscard]] FixingStore make_fixing_store(const std::vector<FixingEntry>& entries,
                                            const std::vector<MarketSource>& sources) {
  FixingStore store;
  store.valuation_date = Date(std::string(kValuationDate));
  store.same_day_rule = SameDayRule{};
  store.same_day_rule.rule_id = kUnresolved;
  store.same_day_rule.rule_version = kUnresolved;
  store.same_day_rule.cutoff_time = kUnresolved;
  store.same_day_rule.cutoff_time_unit = kUnresolved;
  store.same_day_rule.timezone = kUnresolved;
  store.entries = entries;
  store.provenance.sources = sources;
  store.provenance.assembled_by = "fixture-assembler";
  store.provenance.adapter_versions = {{"fixture-adapter", "FIXTURE_V0"}};
  store.provenance.upstream_ids = {"fixture-batch-0001"};
  store.provenance.captured_at = Timestamp(std::string(kCapturedAt));
  store.provenance.evidence_refs = {};
  assign_derived_identities(store);
  return store;
}

[[nodiscard]] MarketSnapshot make_snapshot(const CurveSet& curve_set, const FixingStore& fixing_store) {
  MarketSnapshot snapshot;
  snapshot.valuation_date = Date(std::string(kValuationDate));
  snapshot.captured_at = Timestamp(std::string(kCapturedAt));
  snapshot.source = MarketSource::kSyntheticFixture;
  snapshot.source_detail = "fixture source detail";
  snapshot.curve_set = curve_set;
  snapshot.fixing_store = fixing_store;
  snapshot.volatility_ref = ValueOrReason<std::string>::not_applicable();
  snapshot.volatility_input = ValueOrReason<VolatilityInput>::not_applicable();
  snapshot.provenance.assembled_by = "fixture-assembler";
  snapshot.provenance.adapter_versions = {{"fixture-adapter", "FIXTURE_V0"}};
  snapshot.provenance.upstream_ids = {"fixture-batch-0001"};
  snapshot.provenance.evidence_refs = {};
  snapshot.diagnostics.unresolved_fields = {};
  snapshot.diagnostics.warnings = {};
  // Sets curve_set_ref / fixing_store_ref / volatility_ref and every derived identity, so a decoded
  // document below differs from the fixture only in the one property under test.
  assign_derived_identities(snapshot);
  return snapshot;
}

// ---------------------------------------------------------------------------------------------
// The contract's canonical curve order (docs/33 line 1226), computed independently in the test so a
// fixture is ordered by the RULE rather than by the implementation helper.
// ---------------------------------------------------------------------------------------------
[[nodiscard]] std::array<std::string, 3> canonical_curve_key(const Curve& curve) {
  const CurveCommon& common = curve.common();
  std::string index_key;
  if (common.index_id.present) {
    index_key = enum_to_token<FloatingIndex>(common.index_id.value);
  }
  return {std::string(enum_to_token<CurveRole>(common.curve_role)), index_key, common.curve_id};
}

[[nodiscard]] std::vector<Curve> sorted_curves(std::vector<Curve> curves) {
  std::sort(curves.begin(), curves.end(), [](const Curve& left, const Curve& right) {
    return canonical_curve_key(left) < canonical_curve_key(right);
  });
  return curves;
}

// ---------------------------------------------------------------------------------------------
// Finding 3: set-semantic lists (docs/33 section 15.4, lines 1233-1234)
// ---------------------------------------------------------------------------------------------
TEST(SetSemanticListOrdering, AcceptsASortedDuplicateFreeListAndKeepsTheDocument) {
  SourceProvenance provenance;
  provenance.source = MarketSource::kSyntheticFixture;
  provenance.adapter_name = "fixture-adapter";
  provenance.adapter_version = "FIXTURE_V0";
  provenance.upstream_ids = {"batch-0001", "batch-0002"};
  provenance.evidence_refs = {"E-0001", "E-0002"};
  expect_round_trip(provenance, "SourceProvenance with sorted set-semantic lists");

  SnapshotDiagnostics diagnostics;
  diagnostics.unresolved_fields = {"curve_set.curves[0].pillars[0].value",
                                   "fixing_store.entries[0].publication_date"};
  expect_round_trip(diagnostics, "SnapshotDiagnostics with a sorted unresolved_fields set");
}

TEST(SetSemanticListOrdering, RefusesAnUnsortedOrDuplicatedUpstreamIdsList) {
  SourceProvenance provenance;
  provenance.upstream_ids = {"batch-0002", "batch-0001"};
  const CanonicalValue unsorted = provenance.to_canonical();
  const ContractViolation inverted =
      refusal_for([&unsorted] { (void)SourceProvenance::from_canonical(unsorted, ""); },
                  "upstream_ids in descending order");
  EXPECT_EQ(inverted.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(inverted.pointer(), "/upstream_ids/1");

  const CanonicalValue duplicated =
      with_member(unsorted, "upstream_ids", string_list_canonical({"batch-0001", "batch-0001"}));
  const ContractViolation repeated =
      refusal_for([&duplicated] { (void)SourceProvenance::from_canonical(duplicated, ""); },
                  "upstream_ids with a duplicate entry");
  EXPECT_EQ(repeated.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(repeated.pointer(), "/upstream_ids/1");
}

TEST(SetSemanticListOrdering, RefusesAnUnsortedEvidenceRefsListAndUnresolvedFields) {
  SourceProvenance provenance;
  provenance.evidence_refs = {"E-0002", "E-0001"};
  const CanonicalValue unsorted = provenance.to_canonical();
  const ContractViolation refusal =
      refusal_for([&unsorted] { (void)SourceProvenance::from_canonical(unsorted, ""); },
                  "evidence_refs in descending order");
  EXPECT_EQ(refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(refusal.pointer(), "/evidence_refs/1");

  // `MarketSnapshot.diagnostics.unresolved_fields` is the other named set (docs/33 line 1234) and is
  // deliberately a different call site of the shared helper: descending order must be refused at that
  // list's own pointer.
  const CanonicalValue diagnostics = with_member(
      SnapshotDiagnostics{}.to_canonical(), "unresolved_fields",
      string_list_canonical({"fixing_store.entries[0].version", "curve_set.curve_set_id"}));
  const ContractViolation fields =
      refusal_for([&diagnostics] { (void)SnapshotDiagnostics::from_canonical(diagnostics, ""); },
                  "diagnostics.unresolved_fields in descending order");
  EXPECT_EQ(fields.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(fields.pointer(), "/unresolved_fields/1");
}

TEST(SetSemanticListOrdering, RefusesEveryProvenanceEnvelopeCarryingAnUnsortedList) {
  const CanonicalValue unordered = string_list_canonical({"fixture-batch-0002", "fixture-batch-0001"});

  // CurveSourceProvenance.upstream_ids (docs/33 section 15.4 line 1233).
  const CanonicalValue curve_node = make_forward_curve(FloatingIndex::kUsdSofr).to_canonical();
  const CanonicalValue curve_tampered = with_member(
      curve_node, "source_provenance",
      with_member(nested_member(curve_node, "source_provenance"), "upstream_ids", unordered));
  const ContractViolation curve_refusal =
      refusal_for([&curve_tampered] { (void)Curve::from_canonical(curve_tampered, ""); },
                  "curve source_provenance.upstream_ids in descending order");
  EXPECT_EQ(curve_refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(curve_refusal.pointer(), "/source_provenance/upstream_ids/1");

  // CurveSetConstruction.evidence_refs and CurveSetProvenance.upstream_ids.
  const CanonicalValue set_node =
      make_curve_set(sorted_curves({make_discount_curve(CurveRole::kDiscount)})).to_canonical();
  const CanonicalValue construction_tampered = with_member(
      set_node, "construction",
      with_member(nested_member(set_node, "construction"), "evidence_refs", unordered));
  const ContractViolation construction_refusal = refusal_for(
      [&construction_tampered] { (void)CurveSet::from_canonical(construction_tampered, ""); },
      "CurveSet.construction.evidence_refs in descending order");
  EXPECT_EQ(construction_refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(construction_refusal.pointer(), "/construction/evidence_refs/1");

  const CanonicalValue set_provenance_tampered = with_member(
      set_node, "provenance",
      with_member(nested_member(set_node, "provenance"), "upstream_ids", unordered));
  const ContractViolation set_provenance_refusal = refusal_for(
      [&set_provenance_tampered] { (void)CurveSet::from_canonical(set_provenance_tampered, ""); },
      "CurveSet.provenance.upstream_ids in descending order");
  EXPECT_EQ(set_provenance_refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(set_provenance_refusal.pointer(), "/provenance/upstream_ids/1");

  // FixingStoreProvenance.evidence_refs.
  const CanonicalValue store_node = make_fixing_store({make_entry(FloatingIndex::kUsdSofr, "2024-01-10")},
                                                      {}).to_canonical();
  const CanonicalValue store_tampered = with_member(
      store_node, "provenance",
      with_member(nested_member(store_node, "provenance"), "evidence_refs", unordered));
  const ContractViolation store_refusal =
      refusal_for([&store_tampered] { (void)FixingStore::from_canonical(store_tampered, ""); },
                  "FixingStore.provenance.evidence_refs in descending order");
  EXPECT_EQ(store_refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(store_refusal.pointer(), "/provenance/evidence_refs/1");

  // SnapshotProvenance.upstream_ids and SnapshotDiagnostics.unresolved_fields through the snapshot.
  const CanonicalValue snapshot_node =
      make_snapshot(make_curve_set(sorted_curves({make_discount_curve(CurveRole::kDiscount)})),
                    make_fixing_store({make_entry(FloatingIndex::kUsdSofr, "2024-01-10")}, {}))
          .to_canonical();
  const CanonicalValue snapshot_provenance_tampered = with_member(
      snapshot_node, "provenance",
      with_member(nested_member(snapshot_node, "provenance"), "upstream_ids", unordered));
  const ContractViolation snapshot_provenance_refusal = refusal_for(
      [&snapshot_provenance_tampered] {
        (void)MarketSnapshot::from_canonical(snapshot_provenance_tampered, "");
      },
      "MarketSnapshot.provenance.upstream_ids in descending order");
  EXPECT_EQ(snapshot_provenance_refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(snapshot_provenance_refusal.pointer(), "/provenance/upstream_ids/1");

  const CanonicalValue diagnostics_tampered = with_member(
      snapshot_node, "diagnostics",
      with_member(nested_member(snapshot_node, "diagnostics"), "unresolved_fields", unordered));
  const ContractViolation diagnostics_refusal = refusal_for(
      [&diagnostics_tampered] { (void)MarketSnapshot::from_canonical(diagnostics_tampered, ""); },
      "MarketSnapshot.diagnostics.unresolved_fields in descending order");
  EXPECT_EQ(diagnostics_refusal.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(diagnostics_refusal.pointer(), "/diagnostics/unresolved_fields/1");
}

// ---------------------------------------------------------------------------------------------
// Finding 1: observation states (docs/33 sections 8.2/8.3, lines 583-601)
// ---------------------------------------------------------------------------------------------
TEST(FixingObservationStateConformance, AcceptsACompleteHistoricalRecordAndKeepsItsIdentity) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kHistorical;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kSyntheticFixture);
  entry.quote_timestamp = ValueOrReason<Timestamp>::of(Timestamp(std::string(kCapturedAt)));
  entry.version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_method_id = ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_method_version =
      ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_inputs_ref = ValueOrReason<ProjectionInputsRef>::none(not_applicable_historical_reason());

  const CanonicalValue encoded = entry.to_canonical();
  const FixingEntry decoded = FixingEntry::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  EXPECT_EQ(decoded.observation_state, ObservationState::kHistorical);
  ASSERT_TRUE(decoded.value.present);
  EXPECT_DOUBLE_EQ(decoded.value.value.value, kSyntheticDecimalRate);
  EXPECT_EQ(decoded.value.value.unit, Unit::kDecimalAnnual);
}

TEST(FixingObservationStateConformance, RefusesAHistoricalRecordCarryingAProjectionInputsReference) {
  // Before the correction only projection_method_id/version were refused outside FORECAST_REQUIRED, so
  // a HISTORICAL entry could carry the typed projection inputs reference.
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kHistorical;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kSyntheticFixture);
  entry.version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_method_id = ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_method_version =
      ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_inputs_ref = present_projection_reference("fixture-curve-set", "fixture-curve");

  const CanonicalValue encoded = entry.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingEntry::from_canonical(encoded, ""); },
                  "HISTORICAL with a PRESENT projection_inputs_ref");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/projection_inputs_ref");
}

TEST(FixingObservationStateConformance, RefusesAHistoricalRecordWithoutASourceOrVersion) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kHistorical;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kSyntheticFixture);
  entry.version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_method_id = ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_method_version =
      ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_inputs_ref = ValueOrReason<ProjectionInputsRef>::none(not_applicable_historical_reason());

  FixingEntry without_source = entry;
  without_source.source = ValueOrReason<MarketSource>::none(fixture_missing_reason("FIXTURE_SOURCE_NOT_HELD"));
  const CanonicalValue source_node = without_source.to_canonical();
  const ContractViolation source_violation =
      refusal_for([&source_node] { (void)FixingEntry::from_canonical(source_node, ""); },
                  "HISTORICAL without a PRESENT source");
  EXPECT_EQ(source_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(source_violation.pointer(), "/source");

  FixingEntry without_version = entry;
  without_version.version = ValueOrReason<std::string>::none(fixture_missing_reason("FIXTURE_NO_VERSION"));
  const CanonicalValue version_node = without_version.to_canonical();
  const ContractViolation version_violation =
      refusal_for([&version_node] { (void)FixingEntry::from_canonical(version_node, ""); },
                  "HISTORICAL without a PRESENT version");
  EXPECT_EQ(version_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(version_violation.pointer(), "/version");
}

TEST(FixingObservationStateConformance, RefusesAHistoricalProjectionReasonThatIsNotTheContractReason) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kHistorical;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kSyntheticFixture);
  entry.version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  // A generic NOT_APPLICABLE reason is not the reason the contract names for this case.
  entry.projection_method_id = ValueOrReason<std::string>::none(not_applicable_reason());
  entry.projection_method_version =
      ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_inputs_ref = ValueOrReason<ProjectionInputsRef>::none(not_applicable_historical_reason());

  const CanonicalValue encoded = entry.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingEntry::from_canonical(encoded, ""); },
                  "HISTORICAL projection field without NOT_APPLICABLE_HISTORICAL");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/projection_method_id/reason");
}

TEST(FixingObservationStateConformance, RefusesAHistoricalRecordSourcedFromACurveProjection) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kHistorical;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);
  entry.version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_method_id = ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_method_version =
      ValueOrReason<std::string>::none(not_applicable_historical_reason());
  entry.projection_inputs_ref = ValueOrReason<ProjectionInputsRef>::none(not_applicable_historical_reason());

  const CanonicalValue encoded = entry.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingEntry::from_canonical(encoded, ""); },
                  "HISTORICAL sourced from CURVE_PROJECTION");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/source");
}

TEST(FixingObservationStateConformance, AcceptsAHistoricalRecordPublishedOnOrBeforeTheValuationDate) {
  // Docs/33 line 594 defines HISTORICAL as published on/before the valuation date, so the store's
  // valuation date itself (the line 610 same-day case) and an earlier publication are both accepted.
  // Different indices keep the two entries distinct economic keys in ascending order; every identity
  // is re-hashed by make_fixing_store, so the only property under test is the publication date.
  const FixingStore store = make_fixing_store(
      {make_historical_entry(FloatingIndex::kEurEstr, "2024-01-10", "2024-01-15"),
       make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-11", "2024-01-12")},
      {MarketSource::kSyntheticFixture});

  const CanonicalValue encoded = store.to_canonical();
  const FixingStore decoded = FixingStore::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  ASSERT_EQ(decoded.entries.size(), 2U);
  ASSERT_TRUE(decoded.entries[0].publication_date.present);
  ASSERT_TRUE(decoded.entries[1].publication_date.present);
  EXPECT_EQ(decoded.entries[0].publication_date.value.text, "2024-01-15");
  EXPECT_EQ(decoded.entries[1].publication_date.value.text, "2024-01-12");
}

TEST(FixingObservationStateConformance, RefusesAHistoricalRecordPublishedAfterTheValuationDate) {
  // Docs/33 line 613: a HISTORICAL entry whose publication availability is after the valuation context
  // is a malformed snapshot (UNPUBLISHED_FIXING_STORED_AS_HISTORY), and line 611 routes
  // `publication_date > valuation_date` to the forecast path only. The document is REFUSED where the
  // store's valuation date is available; it is never silently rewritten as PROJECTED/forecast, so a
  // future observation can never become an authoritative market input.
  FixingEntry entry = make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-11", "2024-01-16");
  FixingStore store = make_fixing_store({entry}, {MarketSource::kSyntheticFixture});
  // Re-hashing keeps every declared identity self-consistent, so the refusal can only be the date rule
  // and not a stale fixing_store_id / content_fingerprint.
  assign_derived_identities(store);
  const CanonicalValue encoded = store.to_canonical();

  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingStore::from_canonical(encoded, ""); },
                  "a HISTORICAL entry published after the store's valuation date");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/entries/0/publication_date");
  EXPECT_NE(violation.message().find("UNPUBLISHED_FIXING_STORED_AS_HISTORY"), std::string::npos)
      << violation.message();
}

TEST(FixingObservationStateConformance, RefusesAHistoricalEntryWithUnknownPublicationAvailability) {
  // Docs/33 line 612: "Publication availability required but `publication_date` is `NULL_WITH_REASON` /
  // unknown -> Fail closed; do not reinterpret `observation_date` as publication date and do not
  // silently decide history vs forecast." A HISTORICAL entry is exactly the state in which publication
  // availability is required (line 594: "published on/before valuation date and captured"), so the
  // unknown state is refused at its own field pointer. The contract fixes NO reason code for this row,
  // so the late-publication token (line 613) is deliberately not reused and nothing is invented.
  FixingEntry entry = make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-11", "2024-01-12");
  entry.publication_date =
      ValueOrReason<Date>::none(fixture_missing_reason("FIXTURE_PUBLICATION_AVAILABILITY_UNKNOWN"));
  FixingStore store = make_fixing_store({entry}, {MarketSource::kSyntheticFixture});
  // Re-hashing keeps every declared identity self-consistent, so the refusal can only be the unknown
  // publication-availability rule and not a stale fixing_store_id / content_fingerprint.
  assign_derived_identities(store);
  const CanonicalValue encoded = store.to_canonical();

  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingStore::from_canonical(encoded, ""); },
                  "a HISTORICAL entry with unknown publication availability");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvalidTaggedUnionState);
  EXPECT_EQ(violation.pointer(), "/entries/0/publication_date");
  EXPECT_EQ(violation.message().find("UNPUBLISHED_FIXING_STORED_AS_HISTORY"), std::string::npos)
      << "line 612 prescribes no reason code, so the line 613 code must not be reused: "
      << violation.message();
}

TEST(FixingObservationStateConformance,
     RefusesUnknownPublicationAvailabilityAtTheRehashedOffendingEntryAlone) {
  // The refusal is per entry and attributable, not a rejection of the document shape: two HISTORICAL
  // entries with different indices, only entry 0 (the canonical EUR_ESTR key) has unknown publication
  // availability. Its twin with the field PRESENT is accepted, so the refusal is caused by that one
  // field rather than by the surrounding snapshot.
  FixingEntry unknown = make_historical_entry(FloatingIndex::kEurEstr, "2024-01-10", "2024-01-12");
  unknown.publication_date =
      ValueOrReason<Date>::none(fixture_missing_reason("FIXTURE_PUBLICATION_AVAILABILITY_UNKNOWN"));
  FixingEntry published = make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-11", "2024-01-12");

  FixingStore refused = make_fixing_store({unknown, published}, {MarketSource::kSyntheticFixture});
  assign_derived_identities(refused);
  const CanonicalValue refused_encoded = refused.to_canonical();
  const ContractViolation violation =
      refusal_for([&refused_encoded] { (void)FixingStore::from_canonical(refused_encoded, ""); },
                  "the EUR_ESTR entry with unknown publication availability");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvalidTaggedUnionState);
  EXPECT_EQ(violation.pointer(), "/entries/0/publication_date");

  FixingEntry known = make_historical_entry(FloatingIndex::kEurEstr, "2024-01-10", "2024-01-12");
  FixingStore accepted = make_fixing_store({known, published}, {MarketSource::kSyntheticFixture});
  assign_derived_identities(accepted);
  const CanonicalValue accepted_encoded = accepted.to_canonical();
  const FixingStore decoded = FixingStore::from_canonical(accepted_encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), accepted_encoded.dump());
}

TEST(FixingObservationStateConformance, KeepsEveryNonHistoricalStateWithUnknownPublicationAvailability) {
  // Scope guard for line 612: publication availability is only "required" where the state claims a
  // captured publication. A MISSING, FORECAST_REQUIRED and PROJECTED record keep their unknown
  // publication_date exactly as before - the new rule neither reinterprets `observation_date` nor
  // silently promotes a forecast record to history, and it must not reject the forecast path.
  FixingEntry missing = make_entry(FloatingIndex::kUsdSofr, "2024-01-11");

  FixingEntry forecast_required = make_entry(FloatingIndex::kEurEstr, "2024-01-10");
  forecast_required.observation_state = ObservationState::kForecastRequired;
  forecast_required.value = ValueOrReason<NumericWithUnit>::none(to_be_projected_reason());

  FixingEntry projected = make_entry(FloatingIndex::kGbpSonia, "2024-01-09");
  projected.observation_state = ObservationState::kProjected;
  projected.value = present_decimal_rate(kSyntheticDecimalRate);
  projected.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);
  projected.quote_timestamp = ValueOrReason<Timestamp>::of(Timestamp(std::string(kCapturedAt)));
  projected.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  projected.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  projected.projection_inputs_ref = present_projection_reference("fixture-curve-set", "fixture-curve");

  // The store provenance union is the exact unique union of PRESENT entry sources (docs/33 section
  // 8.4), and only the PROJECTED audit record carries a PRESENT source here.
  FixingStore store = make_fixing_store({forecast_required, projected, missing},
                                        {MarketSource::kCurveProjection});
  assign_derived_identities(store);
  const CanonicalValue encoded = store.to_canonical();

  const FixingStore decoded = FixingStore::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  ASSERT_EQ(decoded.entries.size(), 3U);
  EXPECT_FALSE(decoded.entries[0].publication_date.present);
  EXPECT_FALSE(decoded.entries[1].publication_date.present);
  EXPECT_FALSE(decoded.entries[2].publication_date.present);
}

TEST(FixingObservationStateConformance, KeepsTheForecastPathForAPublicationDateAfterValuation) {
  // Line 611: for a non-HISTORICAL entry, `publication_date > valuation_date` IS the forecast path
  // (FORECAST_REQUIRED or a complete PROJECTED), so the new HISTORICAL rule must leave every other
  // state untouched. A MISSING/FORECAST_REQUIRED entry with a future publication still round-trips.
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-11");
  entry.observation_state = ObservationState::kForecastRequired;
  entry.value = ValueOrReason<NumericWithUnit>::none(to_be_projected_reason());
  entry.publication_date = ValueOrReason<Date>::of(Date(std::string("2024-01-16")));
  FixingStore store = make_fixing_store({entry}, {});
  assign_derived_identities(store);
  const CanonicalValue encoded = store.to_canonical();

  const FixingStore decoded = FixingStore::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  ASSERT_EQ(decoded.entries.size(), 1U);
  EXPECT_EQ(decoded.entries[0].observation_state, ObservationState::kForecastRequired);
}

TEST(FixingObservationStateConformance, AcceptsACompleteProjectedAuditRecord) {
  // The complete audit-only forecast record: value PRESENT, source=CURVE_PROJECTION, and all three
  // projection fields PRESENT (docs/33 lines 585/596). The defective decoder REFUSED this document.
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kProjected;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);
  entry.quote_timestamp = ValueOrReason<Timestamp>::of(Timestamp(std::string(kCapturedAt)));
  entry.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  entry.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_inputs_ref = present_projection_reference("fixture-curve-set", "fixture-curve");

  const CanonicalValue encoded = entry.to_canonical();
  const FixingEntry decoded = FixingEntry::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  EXPECT_EQ(decoded.observation_state, ObservationState::kProjected);
  ASSERT_TRUE(decoded.value.present);
  EXPECT_EQ(decoded.value.value.unit, Unit::kDecimalAnnual);
}

TEST(FixingObservationStateConformance, RefusesAProjectedRecordMissingItsProjectionFields) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kProjected;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);
  entry.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  entry.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_inputs_ref = present_projection_reference("fixture-curve-set", "fixture-curve");

  FixingEntry without_method = entry;
  without_method.projection_method_id = ValueOrReason<std::string>::none(not_yet_projected_reason());
  const CanonicalValue method_node = without_method.to_canonical();
  const ContractViolation method_violation =
      refusal_for([&method_node] { (void)FixingEntry::from_canonical(method_node, ""); },
                  "PROJECTED without a projection method id");
  EXPECT_EQ(method_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(method_violation.pointer(), "/projection_method_id");

  FixingEntry without_reference = entry;
  without_reference.projection_inputs_ref =
      ValueOrReason<ProjectionInputsRef>::none(not_yet_projected_reason());
  const CanonicalValue reference_node = without_reference.to_canonical();
  const ContractViolation reference_violation =
      refusal_for([&reference_node] { (void)FixingEntry::from_canonical(reference_node, ""); },
                  "PROJECTED without a projection inputs reference");
  EXPECT_EQ(reference_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(reference_violation.pointer(), "/projection_inputs_ref");
}

TEST(FixingObservationStateConformance, RefusesAProjectedRecordWhoseSourceIsNotCurveProjection) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kProjected;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kSyntheticFixture);
  entry.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  entry.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_inputs_ref = present_projection_reference("fixture-curve-set", "fixture-curve");

  const CanonicalValue encoded = entry.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingEntry::from_canonical(encoded, ""); },
                  "PROJECTED without source=CURVE_PROJECTION");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/source");
}

TEST(FixingObservationStateConformance, AcceptsAForecastRequiredRecordWithNoProjectedValue) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kForecastRequired;
  entry.value = ValueOrReason<NumericWithUnit>::none(to_be_projected_reason());
  expect_round_trip(entry, "FORECAST_REQUIRED with NULL_WITH_REASON(TO_BE_PROJECTED_AT_VALUATION)");
}

TEST(FixingObservationStateConformance, RefusesAForecastRequiredRecordCarryingAPresentValue) {
  // The inversion: the defective decoder REQUIRED a projected value for FORECAST_REQUIRED, so a
  // projected number was accepted exactly where the contract forbids it (docs/33 line 600).
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kForecastRequired;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);

  const CanonicalValue encoded = entry.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingEntry::from_canonical(encoded, ""); },
                  "FORECAST_REQUIRED carrying a PRESENT numeric value");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/value");
}

TEST(FixingObservationStateConformance, RefusesAForecastRequiredRecordCarryingProjectionMethodology) {
  // The other half of the inversion: FORECAST_REQUIRED used to DEMAND a PRESENT projection
  // methodology. Its projection fields are not-yet-projected states (docs/33 line 587).
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kForecastRequired;
  entry.value = ValueOrReason<NumericWithUnit>::none(to_be_projected_reason());
  entry.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  entry.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_inputs_ref = present_projection_reference("fixture-curve-set", "fixture-curve");

  const CanonicalValue encoded = entry.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingEntry::from_canonical(encoded, ""); },
                  "FORECAST_REQUIRED carrying projection methodology");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/projection_method_id");
}

TEST(FixingObservationStateConformance, RefusesAForecastRequiredValueWithoutTheContractReason) {
  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kForecastRequired;
  entry.value = ValueOrReason<NumericWithUnit>::none(fixture_missing_reason("FIXTURE_FIXING_NOT_HELD"));

  const CanonicalValue encoded = entry.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingEntry::from_canonical(encoded, ""); },
                  "FORECAST_REQUIRED without TO_BE_PROJECTED_AT_VALUATION");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/value");
}

TEST(FixingObservationStateConformance, AcceptsAMissingRecordAndRefusesAMissingRecordWithData) {
  const FixingEntry missing = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  expect_round_trip(missing, "MISSING with structured missing reasons");

  // The pre-existing "a MISSING observation must not carry a PRESENT value" refusal is preserved.
  FixingEntry with_value = missing;
  with_value.value = present_decimal_rate(kSyntheticDecimalRate);
  const CanonicalValue value_node = with_value.to_canonical();
  const ContractViolation value_violation =
      refusal_for([&value_node] { (void)FixingEntry::from_canonical(value_node, ""); },
                  "MISSING carrying a PRESENT value");
  EXPECT_EQ(value_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(value_violation.pointer(), "/value");

  // A MISSING observation carries a structured source reason, never a PRESENT source (line 588).
  FixingEntry with_source = missing;
  with_source.source = ValueOrReason<MarketSource>::of(MarketSource::kSyntheticFixture);
  const CanonicalValue source_node = with_source.to_canonical();
  const ContractViolation source_violation =
      refusal_for([&source_node] { (void)FixingEntry::from_canonical(source_node, ""); },
                  "MISSING carrying a PRESENT source");
  EXPECT_EQ(source_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(source_violation.pointer(), "/source");
}

// docs/33 line 588: a MISSING observation asserts the required historical fixing is not held, so
// `quote_timestamp` and `version` carry structured missing/unavailable reasons and neither may be
// PRESENT. The value/source refusals above are preserved unchanged.
TEST(FixingObservationStateConformance, RefusesAMissingRecordCarryingAQuoteTimestampOrAVersion) {
  const FixingEntry missing = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");

  FixingEntry with_quote = missing;
  with_quote.quote_timestamp = ValueOrReason<Timestamp>::of(Timestamp(std::string(kCapturedAt)));
  const CanonicalValue quote_node = with_quote.to_canonical();
  const ContractViolation quote_violation =
      refusal_for([&quote_node] { (void)FixingEntry::from_canonical(quote_node, ""); },
                  "MISSING carrying a PRESENT quote_timestamp");
  EXPECT_EQ(quote_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(quote_violation.pointer(), "/quote_timestamp");

  FixingEntry with_version = missing;
  with_version.version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  const CanonicalValue version_node = with_version.to_canonical();
  const ContractViolation version_violation =
      refusal_for([&version_node] { (void)FixingEntry::from_canonical(version_node, ""); },
                  "MISSING carrying a PRESENT version");
  EXPECT_EQ(version_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(version_violation.pointer(), "/version");
}

TEST(FixingObservationStateConformance, KeepsPresentQuoteTimestampAndVersionForAHistoricalRecord) {
  // docs/33 line 586 requires a HISTORICAL entry to carry a PRESENT `source` and `version`, so the new
  // MISSING-only rule must not reach the other states. A PRESENT quote timestamp is a legitimate
  // captured fact for a historical observation.
  FixingEntry historical =
      make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-10", "2024-01-10");
  historical.quote_timestamp = ValueOrReason<Timestamp>::of(Timestamp(std::string(kCapturedAt)));
  expect_round_trip(historical, "HISTORICAL with a PRESENT version and quote timestamp");
}

// ---------------------------------------------------------------------------------------------
// `provenance.sources` is the exact unique union of the PRESENT entry sources (docs/33 section 8.4)
// ---------------------------------------------------------------------------------------------
TEST(FixingStoreProvenanceSources, AcceptsTheExactUniqueUnionInAnyOrderAndTheEmptyUnion) {
  FixingEntry first = make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-09", "2024-01-09");
  FixingEntry second = make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-10", "2024-01-10");
  second.source = ValueOrReason<MarketSource>::of(MarketSource::kScreenTranscription);

  // Two PRESENT entry sources, so the declared list carries both. The V1 contract prescribes no order
  // for this list, so a non-ascending order must still be accepted.
  const FixingStore distinct = make_fixing_store(
      {first, second}, {MarketSource::kScreenTranscription, MarketSource::kSyntheticFixture});
  expect_round_trip(distinct, "the exact unique union in a non-ascending order");

  // A forecast-only / MISSING-only store carries no PRESENT entry source, so the empty union is the
  // exact answer rather than a missing list.
  const FixingStore missing_only =
      make_fixing_store({make_entry(FloatingIndex::kUsdSofr, "2024-01-10")}, {});
  expect_round_trip(missing_only, "the legitimate empty union");
}

TEST(FixingStoreProvenanceSources, RefusesADuplicatedListThatHidesAnOmittedSource) {
  FixingEntry first = make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-09", "2024-01-09");
  FixingEntry second = make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-10", "2024-01-10");
  second.source = ValueOrReason<MarketSource>::of(MarketSource::kScreenTranscription);

  // `[A, A]` against the real union `{A, B}`: the union's size, every declared element a member, and B
  // omitted - so only the duplicate-free rule can refuse it. The store is built and re-hashed through
  // `make_fixing_store`, so the refusal cannot be a stale-fingerprint artifact.
  const FixingStore hides_a_source = make_fixing_store(
      {first, second}, {MarketSource::kSyntheticFixture, MarketSource::kSyntheticFixture});
  const CanonicalValue hidden_node = hides_a_source.to_canonical();
  const ContractViolation hidden_violation =
      refusal_for([&hidden_node] { (void)FixingStore::from_canonical(hidden_node, ""); },
                  "a duplicated sources list hiding an omitted source");
  EXPECT_EQ(hidden_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(hidden_violation.pointer(), "/provenance/sources/1");
}

TEST(FixingStoreProvenanceSources, RefusesARepeatAndKeepsTheOversizedClaimRefusal) {
  const FixingEntry single =
      make_historical_entry(FloatingIndex::kUsdSofr, "2024-01-10", "2024-01-10");

  // A repeat in a single-source union is refused at the repeated element.
  const FixingStore repeated = make_fixing_store(
      {single}, {MarketSource::kSyntheticFixture, MarketSource::kSyntheticFixture});
  const CanonicalValue repeated_node = repeated.to_canonical();
  const ContractViolation repeated_violation =
      refusal_for([&repeated_node] { (void)FixingStore::from_canonical(repeated_node, ""); },
                  "a single-source union declared twice");
  EXPECT_EQ(repeated_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(repeated_violation.pointer(), "/provenance/sources/1");

  // Pre-existing refusal preserved: a declared source that no entry carries is refused even though the
  // list is duplicate-free.
  const FixingStore claims_extra = make_fixing_store(
      {single}, {MarketSource::kSyntheticFixture, MarketSource::kScreenTranscription});
  const CanonicalValue extra_node = claims_extra.to_canonical();
  const ContractViolation extra_violation =
      refusal_for([&extra_node] { (void)FixingStore::from_canonical(extra_node, ""); },
                  "a declared source that no entry carries");
  EXPECT_EQ(extra_violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(extra_violation.pointer(), "/provenance");
}

// ---------------------------------------------------------------------------------------------
// Finding 2: curve schema / role binding (docs/33 sections 7.1.1, 7.2, 7.3)
// ---------------------------------------------------------------------------------------------
TEST(CurveSchemaRoleBinding, AcceptsTheSanctionedSchemaRolePairsAndKeepsIdentities) {
  const Curve discount = make_discount_curve(CurveRole::kDiscount);
  const Curve combined = make_discount_curve(CurveRole::kDiscountAndForecastReferenceOnly);
  const Curve forward = make_forward_curve(FloatingIndex::kUsdSofr);

  const CanonicalValue discount_node = discount.to_canonical();
  const Curve discount_decoded = Curve::from_canonical(discount_node, "");
  EXPECT_EQ(discount_decoded.to_canonical().dump(), discount_node.dump());
  EXPECT_EQ(discount_decoded.curve_id(), discount.curve_id());

  const CanonicalValue combined_node = combined.to_canonical();
  const Curve combined_decoded = Curve::from_canonical(combined_node, "");
  EXPECT_EQ(combined_decoded.to_canonical().dump(), combined_node.dump());
  EXPECT_EQ(combined_decoded.curve_role(), CurveRole::kDiscountAndForecastReferenceOnly);

  const CanonicalValue forward_node = forward.to_canonical();
  const Curve forward_decoded = Curve::from_canonical(forward_node, "");
  EXPECT_EQ(forward_decoded.to_canonical().dump(), forward_node.dump());
  EXPECT_EQ(forward_decoded.curve_role(), CurveRole::kForecast);
}

TEST(CurveSchemaRoleBinding, RefusesAForwardRoleUnderTheDiscountCurveSchema) {
  // The combined-role fixture already carries a PRESENT index_id, so it gets past the role/index rule
  // and only the schema/role binding can refuse the mutated document.
  const CanonicalValue combined =
      make_discount_curve(CurveRole::kDiscountAndForecastReferenceOnly).to_canonical();
  const CanonicalValue tampered = with_member(combined, "curve_role", CanonicalValue(std::string("FORECAST")));

  const ContractViolation violation =
      refusal_for([&tampered] { (void)Curve::from_canonical(tampered, ""); },
                  "DISCOUNT_CURVE_V1 with curve_role=FORECAST");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "");
  EXPECT_NE(violation.message().find("DISCOUNT_CURVE_V1"), std::string::npos) << violation.message();
}

TEST(CurveSchemaRoleBinding, RefusesADiscountRoleUnderTheForwardCurveSchema) {
  // index_id is moved to NOT_APPLICABLE at the same time, otherwise the section 7.2 role/index rule
  // would refuse the document first and this test would not isolate the schema/role binding.
  const CanonicalValue forward = make_forward_curve(FloatingIndex::kUsdSofr).to_canonical();
  CanonicalValue tampered =
      with_member(forward, "curve_role", CanonicalValue(std::string("DISCOUNT")));
  tampered = with_member(tampered, "index_id",
                         ValueOrReason<FloatingIndex>::not_applicable().to_canonical());

  const ContractViolation violation =
      refusal_for([&tampered] { (void)Curve::from_canonical(tampered, ""); },
                  "FORWARD_CURVE_V1 with curve_role=DISCOUNT");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "");
  EXPECT_NE(violation.message().find("FORWARD_CURVE_V1"), std::string::npos) << violation.message();
}

// ---------------------------------------------------------------------------------------------
// Finding 4: CurveSet.curves canonical order (docs/33 section 15.4, line 1226)
// ---------------------------------------------------------------------------------------------
TEST(CurveSetCanonicalOrder, AcceptsCurvesInAscendingCanonicalOrderAndKeepsTheSetIdentity) {
  // Three distinct role tokens, so the expected order is fixed by the contract rather than by a hash:
  // "DISCOUNT" < "DISCOUNT_AND_FORECAST_REFERENCE_ONLY" < "FORECAST" (lexicographic canonical token
  // order; enum declaration order would place FORECAST before the combined role, and section 15.4 says
  // "enum order" explicitly only where it means it, line 1229).
  const std::vector<Curve> curves = sorted_curves({make_forward_curve(FloatingIndex::kUsdSofr),
                                                   make_discount_curve(CurveRole::kDiscount),
                                                   make_discount_curve(
                                                       CurveRole::kDiscountAndForecastReferenceOnly)});
  ASSERT_EQ(curves.size(), 3U);
  EXPECT_EQ(curves[0].curve_role(), CurveRole::kDiscount);
  EXPECT_EQ(curves[1].curve_role(), CurveRole::kDiscountAndForecastReferenceOnly);
  EXPECT_EQ(curves[2].curve_role(), CurveRole::kForecast);

  const CurveSet set = make_curve_set(curves);
  const CanonicalValue encoded = set.to_canonical();
  const CurveSet decoded = CurveSet::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  EXPECT_EQ(decoded.curve_set_id, set.curve_set_id);
  EXPECT_EQ(decoded.content_fingerprint, set.content_fingerprint);
  ASSERT_EQ(decoded.curves.size(), 3U);
  EXPECT_EQ(decoded.curves[0].curve_id(), curves[0].curve_id());
}

TEST(CurveSetCanonicalOrder, RefusesCurvesInDescendingOrderEvenWhenEveryIdentityIsRehashed) {
  std::vector<Curve> curves = sorted_curves({make_forward_curve(FloatingIndex::kUsdSofr),
                                             make_discount_curve(CurveRole::kDiscount)});
  ASSERT_EQ(curves.size(), 2U);
  std::reverse(curves.begin(), curves.end());

  // Re-hashing makes every declared curve id, curve_set id and fingerprint self-consistent, so the
  // only defect left is the order itself.
  CurveSet set = make_curve_set(curves);
  assign_derived_identities(set);
  const CanonicalValue encoded = set.to_canonical();
  const ContractViolation violation =
      refusal_for([&encoded] { (void)CurveSet::from_canonical(encoded, ""); },
                  "curves in descending canonical order");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/curves/1");
}

TEST(CurveSetCanonicalOrder, RefusesADuplicateCurveIdEvenWhenEveryIdentityIsRehashed) {
  const Curve curve = make_discount_curve(CurveRole::kDiscount);
  CurveSet set = make_curve_set({curve, curve});
  assign_derived_identities(set);
  const CanonicalValue encoded = set.to_canonical();

  const ContractViolation violation =
      refusal_for([&encoded] { (void)CurveSet::from_canonical(encoded, ""); },
                  "two curves with the same curve_id");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/curves/1");
  EXPECT_NE(violation.message().find("duplicate curve_id"), std::string::npos) << violation.message();
}

// ---------------------------------------------------------------------------------------------
// Finding 5: FixingStore.entries canonical order (docs/33 section 15.4, line 1228)
// ---------------------------------------------------------------------------------------------
TEST(FixingStoreCanonicalOrder, AcceptsEntriesInAscendingKeyOrderAndKeepsTheStoreIdentity) {
  // Different indices are ordered by their canonical enum token, the convention section 15.4 uses when
  // it defines `index_id_sort_key` as the canonical token (line 1226): "EUR_ESTR" < "USD_SOFR".
  const std::vector<FixingEntry> entries = {make_entry(FloatingIndex::kEurEstr, "2024-01-10"),
                                            make_entry(FloatingIndex::kUsdSofr, "2024-01-10"),
                                            make_entry(FloatingIndex::kUsdSofr, "2024-01-11")};
  const FixingStore store = make_fixing_store(entries, {});

  const CanonicalValue encoded = store.to_canonical();
  const FixingStore decoded = FixingStore::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  EXPECT_EQ(decoded.fixing_store_id, store.fixing_store_id);
  EXPECT_EQ(decoded.content_fingerprint, store.content_fingerprint);
}

TEST(FixingStoreCanonicalOrder, RefusesEntriesInDescendingOrderEvenWhenEveryIdentityIsRehashed) {
  std::vector<FixingEntry> entries = {make_entry(FloatingIndex::kUsdSofr, "2024-01-11"),
                                      make_entry(FloatingIndex::kUsdSofr, "2024-01-10")};
  FixingStore store = make_fixing_store(entries, {});
  assign_derived_identities(store);
  const CanonicalValue encoded = store.to_canonical();

  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingStore::from_canonical(encoded, ""); },
                  "entries in descending (index_id, observation_date) order");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/entries/1");
  EXPECT_NE(violation.message().find("strictly ascending"), std::string::npos) << violation.message();
}

TEST(FixingStoreCanonicalOrder, KeepsTheDuplicateEconomicKeyRefusal) {
  // The pre-existing duplicate rule must survive the added ordering rule: an exact duplicate key is
  // still reported as a duplicate, not as an ordering inversion.
  const FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  FixingStore store = make_fixing_store({entry, entry}, {});
  assign_derived_identities(store);
  const CanonicalValue encoded = store.to_canonical();

  const ContractViolation violation =
      refusal_for([&encoded] { (void)FixingStore::from_canonical(encoded, ""); },
                  "a duplicated (index_id, observation_date) economic key");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/entries/1");
  EXPECT_NE(violation.message().find("duplicate fixing"), std::string::npos) << violation.message();
}

// ---------------------------------------------------------------------------------------------
// Finding 1 (typed reference): a PROJECTED entry resolves through the sibling CurveSet
// (docs/33 line 585)
// ---------------------------------------------------------------------------------------------
TEST(ProjectedTypedReference, AcceptsAReferenceThatResolvesToTheSiblingForecastCurve) {
  const Curve forward = make_forward_curve(FloatingIndex::kUsdSofr);
  const CurveSet curve_set = make_curve_set({forward});

  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kProjected;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);
  entry.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  entry.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_inputs_ref =
      present_projection_reference(curve_set.curve_set_id, std::string(forward.curve_id()));

  const FixingStore fixing_store =
      make_fixing_store({entry}, {MarketSource::kCurveProjection});
  const MarketSnapshot snapshot = make_snapshot(curve_set, fixing_store);

  const CanonicalValue encoded = snapshot.to_canonical();
  const MarketSnapshot decoded = MarketSnapshot::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
  EXPECT_EQ(decoded.snapshot_id, snapshot.snapshot_id);
  EXPECT_EQ(decoded.content_fingerprint, snapshot.content_fingerprint);
}

TEST(ProjectedTypedReference, AcceptsAReferenceToACombinedRoleCurve) {
  // Line 585: the reference "is never restricted to the ForwardCurve concrete shape", so an
  // explicitly-authorized combined-role reference curve must be representable.
  const Curve combined = make_discount_curve(CurveRole::kDiscountAndForecastReferenceOnly);
  const CurveSet curve_set = make_curve_set({combined});

  FixingEntry entry = make_entry(FloatingIndex::kUsdSofr, "2024-01-10");
  entry.observation_state = ObservationState::kProjected;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);
  entry.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  entry.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_inputs_ref =
      present_projection_reference(curve_set.curve_set_id, std::string(combined.curve_id()));

  const FixingStore fixing_store =
      make_fixing_store({entry}, {MarketSource::kCurveProjection});
  const MarketSnapshot snapshot = make_snapshot(curve_set, fixing_store);
  expect_round_trip(snapshot, "PROJECTED referencing a combined-role curve");
}

namespace {

// Shared builder for the PROJECTED reference negatives: a snapshot whose fixing store holds one
// PROJECTED entry whose reference the caller has made unresolvable, with every derived identity
// re-hashed so only the reference itself is defective.
[[nodiscard]] CanonicalValue projected_snapshot_with_reference(const Curve& referenced_curve,
                                                               const FloatingIndex entry_index,
                                                               const std::string& curve_set_id,
                                                               const std::string& forecast_curve_id) {
  const CurveSet curve_set = make_curve_set({referenced_curve});
  FixingEntry entry = make_entry(entry_index, "2024-01-10");
  entry.observation_state = ObservationState::kProjected;
  entry.value = present_decimal_rate(kSyntheticDecimalRate);
  entry.source = ValueOrReason<MarketSource>::of(MarketSource::kCurveProjection);
  entry.projection_method_id = ValueOrReason<std::string>::of(std::string("FIXTURE_PROJECTION_METHOD"));
  entry.projection_method_version = ValueOrReason<std::string>::of(std::string("FIXTURE_V0"));
  entry.projection_inputs_ref = present_projection_reference(curve_set_id, forecast_curve_id);
  const FixingStore fixing_store =
      make_fixing_store({entry}, {MarketSource::kCurveProjection});
  MarketSnapshot snapshot = make_snapshot(curve_set, fixing_store);
  assign_derived_identities(snapshot);
  return snapshot.to_canonical();
}

}  // namespace

TEST(ProjectedTypedReference, RefusesAReferenceWhoseCurveCarriesADifferentIndex) {
  const Curve forward = make_forward_curve(FloatingIndex::kUsdSofr);
  const CurveSet curve_set = make_curve_set({forward});
  // The entry claims EUR_ESTR while the referenced curve's PRESENT index_id is USD_SOFR.
  const CanonicalValue encoded = projected_snapshot_with_reference(
      forward, FloatingIndex::kEurEstr, curve_set.curve_set_id, std::string(forward.curve_id()));

  const ContractViolation violation =
      refusal_for([&encoded] { (void)MarketSnapshot::from_canonical(encoded, ""); },
                  "PROJECTED reference whose curve index differs from the entry index");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(),
            "/fixing_store/entries/0/projection_inputs_ref/forecast_curve_id");
}

TEST(ProjectedTypedReference, RefusesAReferenceThatDoesNotResolveInTheSiblingCurveSet) {
  const Curve forward = make_forward_curve(FloatingIndex::kUsdSofr);
  const CurveSet curve_set = make_curve_set({forward});
  const CanonicalValue encoded = projected_snapshot_with_reference(
      forward, FloatingIndex::kUsdSofr, curve_set.curve_set_id, "fixture-absent-curve");

  const ContractViolation violation =
      refusal_for([&encoded] { (void)MarketSnapshot::from_canonical(encoded, ""); },
                  "PROJECTED reference with a zero-match forecast_curve_id");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(),
            "/fixing_store/entries/0/projection_inputs_ref/forecast_curve_id");
}

TEST(ProjectedTypedReference, RefusesAReferenceNamingAnotherCurveSet) {
  const Curve forward = make_forward_curve(FloatingIndex::kUsdSofr);
  const CurveSet curve_set = make_curve_set({forward});
  const CanonicalValue encoded = projected_snapshot_with_reference(
      forward, FloatingIndex::kUsdSofr, "fixture-other-curve-set", std::string(forward.curve_id()));

  const ContractViolation violation =
      refusal_for([&encoded] { (void)MarketSnapshot::from_canonical(encoded, ""); },
                  "PROJECTED reference naming an unrelated CurveSet");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kIdentityMismatch);
  EXPECT_EQ(violation.pointer(), "/fixing_store/entries/0/projection_inputs_ref/curve_set_id");
}

TEST(ProjectedTypedReference, RefusesAReferenceToADiscountOnlyCurve) {
  const Curve discount = make_discount_curve(CurveRole::kDiscount);
  const CurveSet curve_set = make_curve_set({discount});
  const CanonicalValue encoded = projected_snapshot_with_reference(
      discount, FloatingIndex::kUsdSofr, curve_set.curve_set_id, std::string(discount.curve_id()));

  const ContractViolation violation =
      refusal_for([&encoded] { (void)MarketSnapshot::from_canonical(encoded, ""); },
                  "PROJECTED reference to a DISCOUNT-only curve");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(),
            "/fixing_store/entries/0/projection_inputs_ref/forecast_curve_id");
  EXPECT_NE(violation.message().find("role"), std::string::npos) << violation.message();
}

}  // namespace
