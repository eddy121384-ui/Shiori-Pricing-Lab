#pragma once
//
// Market DTOs — docs/33 (Issue #225) sections 6 (MarketSnapshot), 7 (curves), 8 (FixingStore) and
// 9 (volatility boundary).
//
// Implemented here: the versioned shapes, the mandatory-field rules, the role/applicability rules,
// the cross-object identity invariants, the declared-fingerprint verification, and the structural
// canonical ordering rules that #225 states (strictly ascending, duplicate-free curve pillars;
// unique fixing (index_id, observation_date) keys).
//
// NOT implemented here (and deliberately so):
//   * NO curve construction, interpolation, extrapolation, discounting or forecasting maths. The
//     interpolation/extrapolation/compounding/day-count fields are carried as open METHODOLOGY TEXT
//     whose V1 value is the contract's own `UNRESOLVED` token; #227 chooses no value
//     (RED-01 / RED-02-adjacent, docs/33 section 7.0).
//   * `VolatilityInput`'s internal node/surface/cube shape is a documented STRUCTURAL PLACEHOLDER:
//     see docs/CANONICALIZATION.md. Its payload is preserved byte-exactly, its declared fingerprint
//     is recomputed and verified, and its version is validated, but its per-node required fields are
//     owned by the issue that implements the vol contract. #227 does not type them and does not
//     invent a vol node.
//
#include <map>
#include <optional>
#include <string>
#include <string_view>
#include <variant>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/numeric.hpp"
#include "shiori_rates/dto/time.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"
#include "shiori_rates/dto/version.hpp"

namespace Shiori::rates::dto {

// ---------------------------------------------------------------------------------------------
// Small shared shapes
// ---------------------------------------------------------------------------------------------
// docs/33 section 6.2 `diagnostics.warnings`: list[code+message]. Deliberately a different shape
// from the result-contract diagnostics record (section 12.2), which carries a `detail`.
struct CodeMessage {
  std::string code;
  std::string message;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CodeMessage from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer);
  friend bool operator==(const CodeMessage& left, const CodeMessage& right) {
    return left.code == right.code && left.message == right.message;
  }
};

// docs/33 sections 12.2 / 13.2 `warnings` and `errors`: list[{code, message, detail}].
struct DiagnosticRecord {
  std::string code;
  std::string message;
  std::optional<std::string> detail;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static DiagnosticRecord from_canonical(const CanonicalValue& node,
                                                       const std::string& pointer);
  friend bool operator==(const DiagnosticRecord& left, const DiagnosticRecord& right) {
    return left.code == right.code && left.message == right.message && left.detail == right.detail;
  }
};

// docs/33 section 15.3 common provenance, used by objects whose sections do not declare a narrower
// envelope. `source` is the enum token; methodology identifiers are open text (RED values).
struct SourceProvenance {
  std::optional<MarketSource> source;
  std::optional<std::string> adapter_name;
  std::optional<std::string> adapter_version;
  std::vector<std::string> upstream_ids;
  std::vector<std::string> evidence_refs;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static SourceProvenance from_canonical(const CanonicalValue& node,
                                                       const std::string& pointer);
};

// docs/33 section 7.2 `interpolation` / `extrapolation`. All three members are REQUIRED; their
// VALUES are UNRESOLVED-RED. `FAIL_CLOSED` is representable and is the only extrapolation token
// #225 pre-approves as a fallback contract shape.
struct MethodSpec {
  std::string method_id;        // open methodology text; "UNRESOLVED" until locked
  std::string method_version;   // open methodology text; "UNRESOLVED" until locked
  std::optional<std::map<std::string, NumericWithUnit>> parameters;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static MethodSpec from_canonical(const CanonicalValue& node,
                                                 const std::string& pointer);
};

// docs/33 section 7.2 `rate_representation`.
struct RateRepresentation {
  std::string compounding;       // UNRESOLVED
  std::string day_count;         // UNRESOLVED
  std::string accrual_boundary;  // UNRESOLVED

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static RateRepresentation from_canonical(const CanonicalValue& node,
                                                         const std::string& pointer);
};

struct CurvePillar {
  Date pillar_date;
  ValueOrReason<std::string> maturity_label;
  ValueState value_state = ValueState::kResolved;
  std::optional<double> value;
  std::optional<StructuredReason> unresolved_reason;
  ValueOrReason<std::string> source_column;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CurvePillar from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer);
};

struct CurveSourceProvenance {
  MarketSource source = MarketSource::kSyntheticFixture;
  std::vector<ValueOrReason<Timestamp>> quote_timestamps;  // one per pillar
  std::string adapter_name;
  std::string adapter_version;
  std::vector<std::string> upstream_ids;
  std::vector<std::string> evidence_refs;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CurveSourceProvenance from_canonical(const CanonicalValue& node,
                                                            const std::string& pointer);
};

// ---------------------------------------------------------------------------------------------
// Curves (docs/33 sections 7.2 / 7.3)
// ---------------------------------------------------------------------------------------------
// All members shared by DISCOUNT_CURVE_V1 and FORWARD_CURVE_V1. `curve_id` and
// `content_fingerprint` are derived; see curve_identity_preimage / curve_fingerprint_preimage.
struct CurveCommon {
  std::string curve_id;
  Currency currency = Currency::kUsd;
  CurveRole curve_role = CurveRole::kDiscount;
  ValueOrReason<FloatingIndex> index_id;
  Date valuation_date;
  Date reference_date;
  ValueOrReason<std::string> reference_date_reason;
  PillarValueType value_type = PillarValueType::kZeroRateContinuous;
  Unit value_unit = Unit::kDecimalAnnual;
  std::vector<CurvePillar> pillars;
  RateRepresentation rate_representation;
  MethodSpec interpolation;
  MethodSpec extrapolation;
  CurveSourceProvenance source_provenance;
  std::string construction_methodology_id;       // exact echo of the CurveSet value
  std::string construction_methodology_version;  // exact echo of the CurveSet value
  std::string content_fingerprint;
};

struct DiscountCurve {
  CurveCommon common;
};

struct ForwardCurve {
  CurveCommon common;
  ValueOrReason<std::string> index_tenor;
  ValueOrReason<std::string> fixing_calendar_ref;
  ValueOrReason<std::string> observation_rules_ref;
};

// A role-tagged curve list entry, discriminated by schema_version (DISCOUNT_CURVE_V1 |
// FORWARD_CURVE_V1). No role is ever inferred from list position.
struct Curve {
  std::variant<DiscountCurve, ForwardCurve> body;

  [[nodiscard]] bool is_forward() const noexcept {
    return std::holds_alternative<ForwardCurve>(body);
  }
  [[nodiscard]] const CurveCommon& common() const;
  [[nodiscard]] std::string_view curve_id() const;
  [[nodiscard]] CurveRole curve_role() const;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static Curve from_canonical(const CanonicalValue& node, const std::string& pointer);
};

// ---------------------------------------------------------------------------------------------
// CurveSet (docs/33 section 7.1)
// ---------------------------------------------------------------------------------------------
struct CurveSetConstruction {
  std::string construction_methodology_id;       // UNRESOLVED
  std::string construction_methodology_version;  // UNRESOLVED
  ValueOrReason<std::string> construction_inputs_ref;
  std::vector<std::string> evidence_refs;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CurveSetConstruction from_canonical(const CanonicalValue& node,
                                                           const std::string& pointer);
};

struct CurveSetProvenance {
  std::vector<MarketSource> sources;  // may be empty; an empty list is data, never a claim
  std::map<std::string, std::string> adapter_versions;
  std::vector<std::string> upstream_ids;
  Timestamp captured_at;
  std::vector<std::string> evidence_refs;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CurveSetProvenance from_canonical(const CanonicalValue& node,
                                                         const std::string& pointer);
};

struct CurveSet {
  std::string curve_set_id;
  Date valuation_date;
  Currency base_currency = Currency::kUsd;
  std::vector<Curve> curves;  // 1..n role-tagged; list order is authoritative (no reordering rule)
  CurveSetConstruction construction;
  CurveSetProvenance provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CurveSet from_canonical(const CanonicalValue& node,
                                               const std::string& pointer);
};

// ---------------------------------------------------------------------------------------------
// FixingStore (docs/33 section 8)
// ---------------------------------------------------------------------------------------------
struct SameDayRule {
  std::string rule_id;           // UNRESOLVED (RED-225-F1)
  std::string rule_version;      // UNRESOLVED
  std::string cutoff_time;       // UNRESOLVED
  std::string cutoff_time_unit;  // UNRESOLVED
  std::string timezone;          // UNRESOLVED

  // Number of members still carrying the contract's explicit UNRESOLVED token. The V1 same-day
  // rule is entirely unresolved, so any same-day requirement must fail closed downstream; this
  // accessor exists so that condition is checkable rather than assumed.
  [[nodiscard]] int count_explicitly_unresolved() const noexcept;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static SameDayRule from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer);
};

struct ProjectionInputsRef {
  std::string curve_set_id;
  std::string forecast_curve_id;
  std::string observation_rule_id;
  std::string observation_rule_version;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ProjectionInputsRef from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer);
};

struct FixingEntry {
  FloatingIndex index_id = FloatingIndex::kUsdSofr;
  Date observation_date;
  ValueOrReason<Date> publication_date;
  ValueOrReason<Timestamp> publication_timestamp;
  ObservationState observation_state = ObservationState::kMissing;
  ValueOrReason<NumericWithUnit> value;
  std::string day_count_basis;  // UNRESOLVED until evidenced
  ValueOrReason<MarketSource> source;
  ValueOrReason<Timestamp> quote_timestamp;
  ValueOrReason<std::string> version;
  ValueOrReason<std::string> projection_method_id;
  ValueOrReason<std::string> projection_method_version;
  ValueOrReason<ProjectionInputsRef> projection_inputs_ref;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static FixingEntry from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer);
};

struct FixingStoreProvenance {
  std::vector<MarketSource> sources;  // deterministic union of PRESENT entry sources; may be empty
  std::string assembled_by;
  std::map<std::string, std::string> adapter_versions;
  std::vector<std::string> upstream_ids;
  Timestamp captured_at;
  std::vector<std::string> evidence_refs;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static FixingStoreProvenance from_canonical(const CanonicalValue& node,
                                                           const std::string& pointer);
};

struct FixingStore {
  std::string fixing_store_id;
  Date valuation_date;
  SameDayRule same_day_rule;
  std::vector<FixingEntry> entries;
  FixingStoreProvenance provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static FixingStore from_canonical(const CanonicalValue& node,
                                                  const std::string& pointer);
};

// ---------------------------------------------------------------------------------------------
// Volatility boundary (docs/33 section 9) — STRUCTURAL PLACEHOLDER
// ---------------------------------------------------------------------------------------------
// The identity/version/fingerprint envelope is real and enforced. The per-node shape (VolQuote,
// axis entries, strike coordinates, VolNodeKey, surface/cube containers) is owned by the contract
// implementation that consumes it and is carried here as a byte-exact canonical payload. #227 does
// not type those nodes, does not invent a vol node, and chooses no vol methodology.
struct VolatilityInput {
  std::string volatility_input_id;
  Date valuation_date;
  CanonicalValue payload;  // object; byte-exact canonical child
  SourceProvenance provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static VolatilityInput from_canonical(const CanonicalValue& node,
                                                      const std::string& pointer);
};

// ---------------------------------------------------------------------------------------------
// MarketSnapshot (docs/33 section 6)
// ---------------------------------------------------------------------------------------------
struct SnapshotProvenance {
  std::string assembled_by;
  std::map<std::string, std::string> adapter_versions;
  std::vector<std::string> upstream_ids;
  std::vector<std::string> evidence_refs;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static SnapshotProvenance from_canonical(const CanonicalValue& node,
                                                         const std::string& pointer);
};

struct SnapshotDiagnostics {
  std::vector<std::string> unresolved_fields;
  std::vector<CodeMessage> warnings;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static SnapshotDiagnostics from_canonical(const CanonicalValue& node,
                                                          const std::string& pointer);
};

struct MarketSnapshot {
  std::string snapshot_id;
  Date valuation_date;
  Timestamp captured_at;
  MarketSource source = MarketSource::kSyntheticFixture;
  std::string source_detail;
  std::string curve_set_ref;
  CurveSet curve_set;
  std::string fixing_store_ref;
  FixingStore fixing_store;
  ValueOrReason<std::string> volatility_ref;
  ValueOrReason<VolatilityInput> volatility_input;
  SnapshotProvenance provenance;
  std::string content_fingerprint;
  SnapshotDiagnostics diagnostics;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static MarketSnapshot from_canonical(const CanonicalValue& node,
                                                     const std::string& pointer);

  // Cross-object invariants of section 6.4 (ref/payload identity, valuation-date agreement) plus
  // section 7.1 curve/set construction-methodology equality. Called by from_canonical; also callable
  // directly on an in-memory value before publication.
  void validate_invariants(const std::string& pointer) const;
};

// ---------------------------------------------------------------------------------------------
// Derived-identity helpers (see docs/CANONICALIZATION.md for the derivation order)
// ---------------------------------------------------------------------------------------------
// The full canonical object of one curve, including its own schema_version token and, for a forward
// curve, its three extra fields (`forward` is null for a discount curve). This is the authoritative
// serialization: `Curve::to_canonical` and every identity preimage go through it so a shape can
// never drift between the wire form and the preimage.
[[nodiscard]] CanonicalValue curve_full_canonical(const CurveCommon& common,
                                                  SchemaVersion schema_version,
                                                  const ForwardCurve* forward);

struct DerivedIdentity {
  std::string id;
  std::string content_fingerprint;
  friend bool operator==(const DerivedIdentity& left, const DerivedIdentity& right) {
    return left.id == right.id && left.content_fingerprint == right.content_fingerprint;
  }
};

// Identity preimage: own id and own content_fingerprint excluded.
[[nodiscard]] CanonicalValue curve_identity_preimage(const Curve& curve);
// Fingerprint preimage: only own content_fingerprint excluded (the id participates).
[[nodiscard]] CanonicalValue curve_fingerprint_preimage(const Curve& curve,
                                                        const std::string& curve_id);
[[nodiscard]] DerivedIdentity compute_curve_identities(const Curve& curve);
// Recomputes and refuses on any disagreement with the declared id/fingerprint.
void verify_curve_identities(const Curve& curve, const std::string& pointer);

[[nodiscard]] CanonicalValue curve_set_identity_preimage(const CurveSet& curve_set);
[[nodiscard]] CanonicalValue curve_set_fingerprint_preimage(const CurveSet& curve_set,
                                                            const std::string& curve_set_id);
[[nodiscard]] DerivedIdentity compute_curve_set_identities(const CurveSet& curve_set);
void verify_curve_set_identities(const CurveSet& curve_set, const std::string& pointer);

[[nodiscard]] CanonicalValue fixing_store_identity_preimage(const FixingStore& fixing_store);
[[nodiscard]] CanonicalValue fixing_store_fingerprint_preimage(const FixingStore& fixing_store,
                                                               const std::string& fixing_store_id);
[[nodiscard]] DerivedIdentity compute_fixing_store_identities(const FixingStore& fixing_store);
void verify_fixing_store_identities(const FixingStore& fixing_store, const std::string& pointer);

[[nodiscard]] CanonicalValue snapshot_identity_preimage(const MarketSnapshot& snapshot);
[[nodiscard]] CanonicalValue snapshot_fingerprint_preimage(const MarketSnapshot& snapshot,
                                                           const std::string& snapshot_id);
[[nodiscard]] DerivedIdentity compute_snapshot_identities(const MarketSnapshot& snapshot);
void verify_snapshot_identities(const MarketSnapshot& snapshot, const std::string& pointer);

[[nodiscard]] CanonicalValue volatility_identity_preimage(const VolatilityInput& input);
[[nodiscard]] CanonicalValue volatility_fingerprint_preimage(const VolatilityInput& input,
                                                             const std::string& input_id);
[[nodiscard]] DerivedIdentity compute_volatility_identities(const VolatilityInput& input);
void verify_volatility_identities(const VolatilityInput& input, const std::string& pointer);

// Computes and (re)assigns every derived id and fingerprint in a subtree, innermost first, so that
// an enclosing identity covers its children's recomputed identities.
void assign_derived_identities(Curve& curve);
void assign_derived_identities(CurveSet& curve_set);
void assign_derived_identities(FixingStore& fixing_store);
void assign_derived_identities(VolatilityInput& volatility_input);
void assign_derived_identities(MarketSnapshot& snapshot);

}  // namespace Shiori::rates::dto
