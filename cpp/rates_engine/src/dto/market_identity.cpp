#include <initializer_list>
#include <string>
#include <string_view>
#include <utility>
#include <variant>

#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/market.hpp"

namespace Shiori::rates::dto {

namespace {

// Returns the object with the listed members removed. CanonicalValue::make_object re-sorts, so a
// preimage is always in canonical order regardless of removal order.
[[nodiscard]] CanonicalValue strip_keys(const CanonicalValue& object,
                                        const std::initializer_list<std::string_view> keys) {
  CanonicalMembers members;
  members.reserve(object.as_object().members.size());
  for (const auto& member : object.as_object().members) {
    bool drop = false;
    for (const std::string_view key : keys) {
      if (member.first == key) {
        drop = true;
        break;
      }
    }
    if (!drop) {
      members.push_back(member);
    }
  }
  return CanonicalValue::make_object(std::move(members));
}

[[nodiscard]] Curve curve_with_id(const Curve& curve, const std::string& curve_id) {
  Curve copy = curve;
  if (copy.is_forward()) {
    std::get<ForwardCurve>(copy.body).common.curve_id = curve_id;
  } else {
    std::get<DiscountCurve>(copy.body).common.curve_id = curve_id;
  }
  return copy;
}

[[noreturn]] void fail_identity_mismatch(const std::string& pointer, std::string_view field,
                                         const char* what) {
  fail(ContractViolationKind::kIdentityMismatch, pointer_child(pointer, field), what);
}

[[noreturn]] void fail_fingerprint_mismatch(const std::string& pointer, std::string_view field,
                                            const char* what) {
  fail(ContractViolationKind::kFingerprintMismatch, pointer_child(pointer, field), what);
}

}  // namespace

// ---------------------------------------------------------------------------------------------
// Curve
// ---------------------------------------------------------------------------------------------
CanonicalValue curve_identity_preimage(const Curve& curve) {
  const CurveCommon& common = curve.common();
  if (curve.is_forward()) {
    const ForwardCurve& forward = std::get<ForwardCurve>(curve.body);
    return strip_keys(curve_full_canonical(common, SchemaVersion::kForwardCurveV1, &forward),
                      {"curve_id", "content_fingerprint"});
  }
  return strip_keys(curve_full_canonical(common, SchemaVersion::kDiscountCurveV1, nullptr),
                    {"curve_id", "content_fingerprint"});
}

CanonicalValue curve_fingerprint_preimage(const Curve& curve, const std::string& curve_id) {
  const Curve with_id = curve_with_id(curve, curve_id);
  const CurveCommon& common = with_id.common();
  if (with_id.is_forward()) {
    const ForwardCurve& forward = std::get<ForwardCurve>(with_id.body);
    return strip_keys(curve_full_canonical(common, SchemaVersion::kForwardCurveV1, &forward),
                      {"content_fingerprint"});
  }
  return strip_keys(curve_full_canonical(common, SchemaVersion::kDiscountCurveV1, nullptr),
                    {"content_fingerprint"});
}

DerivedIdentity compute_curve_identities(const Curve& curve) {
  DerivedIdentity identity;
  // Step 1: the identity preimage excludes BOTH the own id and the own fingerprint, which is what
  // makes the id derivable without self-reference.
  identity.id = fingerprint_of(curve_identity_preimage(curve));
  // Step 2: the fingerprint preimage excludes only the own fingerprint, so it includes the id just
  // derived; id and fingerprint are therefore always distinct values for the same payload.
  identity.content_fingerprint =
      fingerprint_of(curve_fingerprint_preimage(curve, identity.id));
  return identity;
}

void verify_curve_identities(const Curve& curve, const std::string& pointer) {
  const DerivedIdentity expected = compute_curve_identities(curve);
  const CurveCommon& common = curve.common();
  if (common.curve_id != expected.id) {
    fail_identity_mismatch(pointer, "curve_id",
                           "declared curve_id does not match the recomputed canonical identity");
  }
  if (common.content_fingerprint != expected.content_fingerprint) {
    fail_fingerprint_mismatch(
        pointer, "content_fingerprint",
        "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(Curve& curve) {
  const DerivedIdentity identity = compute_curve_identities(curve);
  if (curve.is_forward()) {
    ForwardCurve& forward = std::get<ForwardCurve>(curve.body);
    forward.common.curve_id = identity.id;
    forward.common.content_fingerprint = identity.content_fingerprint;
  } else {
    DiscountCurve& discount = std::get<DiscountCurve>(curve.body);
    discount.common.curve_id = identity.id;
    discount.common.content_fingerprint = identity.content_fingerprint;
  }
}

// ---------------------------------------------------------------------------------------------
// CurveSet
// ---------------------------------------------------------------------------------------------
CanonicalValue curve_set_identity_preimage(const CurveSet& curve_set) {
  return strip_keys(curve_set.to_canonical(), {"curve_set_id", "content_fingerprint"});
}

CanonicalValue curve_set_fingerprint_preimage(const CurveSet& curve_set,
                                              const std::string& curve_set_id) {
  CurveSet copy = curve_set;
  copy.curve_set_id = curve_set_id;
  return strip_keys(copy.to_canonical(), {"content_fingerprint"});
}

DerivedIdentity compute_curve_set_identities(const CurveSet& curve_set) {
  DerivedIdentity identity;
  identity.id = fingerprint_of(curve_set_identity_preimage(curve_set));
  identity.content_fingerprint =
      fingerprint_of(curve_set_fingerprint_preimage(curve_set, identity.id));
  return identity;
}

void verify_curve_set_identities(const CurveSet& curve_set, const std::string& pointer) {
  for (std::size_t index = 0; index < curve_set.curves.size(); ++index) {
    verify_curve_identities(curve_set.curves[index], pointer_index(pointer_child(pointer, "curves"),
                                                                   index));
  }
  const DerivedIdentity expected = compute_curve_set_identities(curve_set);
  if (curve_set.curve_set_id != expected.id) {
    fail_identity_mismatch(pointer, "curve_set_id",
                           "declared curve_set_id does not match the recomputed identity");
  }
  if (curve_set.content_fingerprint != expected.content_fingerprint) {
    fail_fingerprint_mismatch(
        pointer, "content_fingerprint",
        "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(CurveSet& curve_set) {
  for (Curve& curve : curve_set.curves) {
    assign_derived_identities(curve);
  }
  const DerivedIdentity identity = compute_curve_set_identities(curve_set);
  curve_set.curve_set_id = identity.id;
  curve_set.content_fingerprint = identity.content_fingerprint;
}

// ---------------------------------------------------------------------------------------------
// FixingStore
// ---------------------------------------------------------------------------------------------
CanonicalValue fixing_store_identity_preimage(const FixingStore& fixing_store) {
  // #225 section 8.2 states that valuation_date, entries and same_day_rule participate; the general
  // rule of section 15.4 ("the full canonical payload") also brings provenance and schema_version, so
  // the preimage is everything except the own id and the own fingerprint.
  return strip_keys(fixing_store.to_canonical(), {"fixing_store_id", "content_fingerprint"});
}

CanonicalValue fixing_store_fingerprint_preimage(const FixingStore& fixing_store,
                                                 const std::string& fixing_store_id) {
  FixingStore copy = fixing_store;
  copy.fixing_store_id = fixing_store_id;
  return strip_keys(copy.to_canonical(), {"content_fingerprint"});
}

DerivedIdentity compute_fixing_store_identities(const FixingStore& fixing_store) {
  DerivedIdentity identity;
  identity.id = fingerprint_of(fixing_store_identity_preimage(fixing_store));
  identity.content_fingerprint =
      fingerprint_of(fixing_store_fingerprint_preimage(fixing_store, identity.id));
  return identity;
}

void verify_fixing_store_identities(const FixingStore& fixing_store, const std::string& pointer) {
  const DerivedIdentity expected = compute_fixing_store_identities(fixing_store);
  if (fixing_store.fixing_store_id != expected.id) {
    fail_identity_mismatch(pointer, "fixing_store_id",
                           "declared fixing_store_id does not match the recomputed identity");
  }
  if (fixing_store.content_fingerprint != expected.content_fingerprint) {
    fail_fingerprint_mismatch(
        pointer, "content_fingerprint",
        "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(FixingStore& fixing_store) {
  const DerivedIdentity identity = compute_fixing_store_identities(fixing_store);
  fixing_store.fixing_store_id = identity.id;
  fixing_store.content_fingerprint = identity.content_fingerprint;
}

// ---------------------------------------------------------------------------------------------
// VolatilityInput (STRUCTURAL PLACEHOLDER envelope)
// ---------------------------------------------------------------------------------------------
CanonicalValue volatility_identity_preimage(const VolatilityInput& input) {
  return strip_keys(input.to_canonical(),
                    {"volatility_input_id", "content_fingerprint"});
}

CanonicalValue volatility_fingerprint_preimage(const VolatilityInput& input,
                                               const std::string& input_id) {
  VolatilityInput copy = input;
  copy.volatility_input_id = input_id;
  return strip_keys(copy.to_canonical(), {"content_fingerprint"});
}

DerivedIdentity compute_volatility_identities(const VolatilityInput& input) {
  DerivedIdentity identity;
  identity.id = fingerprint_of(volatility_identity_preimage(input));
  identity.content_fingerprint = fingerprint_of(volatility_fingerprint_preimage(input, identity.id));
  return identity;
}

void verify_volatility_identities(const VolatilityInput& input, const std::string& pointer) {
  const DerivedIdentity expected = compute_volatility_identities(input);
  if (input.volatility_input_id != expected.id) {
    fail_identity_mismatch(pointer, "volatility_input_id",
                           "declared volatility_input_id does not match the recomputed identity");
  }
  if (input.content_fingerprint != expected.content_fingerprint) {
    fail_fingerprint_mismatch(
        pointer, "content_fingerprint",
        "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(VolatilityInput& volatility_input) {
  const DerivedIdentity identity = compute_volatility_identities(volatility_input);
  volatility_input.volatility_input_id = identity.id;
  volatility_input.content_fingerprint = identity.content_fingerprint;
}

// ---------------------------------------------------------------------------------------------
// MarketSnapshot
// ---------------------------------------------------------------------------------------------
CanonicalValue snapshot_identity_preimage(const MarketSnapshot& snapshot) {
  return strip_keys(snapshot.to_canonical(), {"snapshot_id", "content_fingerprint"});
}

CanonicalValue snapshot_fingerprint_preimage(const MarketSnapshot& snapshot,
                                             const std::string& snapshot_id) {
  MarketSnapshot copy = snapshot;
  copy.snapshot_id = snapshot_id;
  return strip_keys(copy.to_canonical(), {"content_fingerprint"});
}

DerivedIdentity compute_snapshot_identities(const MarketSnapshot& snapshot) {
  DerivedIdentity identity;
  identity.id = fingerprint_of(snapshot_identity_preimage(snapshot));
  identity.content_fingerprint =
      fingerprint_of(snapshot_fingerprint_preimage(snapshot, identity.id));
  return identity;
}

void verify_snapshot_identities(const MarketSnapshot& snapshot, const std::string& pointer) {
  const DerivedIdentity expected = compute_snapshot_identities(snapshot);
  if (snapshot.snapshot_id != expected.id) {
    fail_identity_mismatch(pointer, "snapshot_id",
                           "declared snapshot_id does not match the recomputed identity");
  }
  if (snapshot.content_fingerprint != expected.content_fingerprint) {
    fail_fingerprint_mismatch(
        pointer, "content_fingerprint",
        "declared content_fingerprint does not match the recomputed canonical preimage");
  }
}

void assign_derived_identities(MarketSnapshot& snapshot) {
  // Innermost first: an enclosing identity must cover its children's recomputed identities.
  assign_derived_identities(snapshot.curve_set);
  assign_derived_identities(snapshot.fixing_store);
  if (snapshot.volatility_input.present) {
    assign_derived_identities(snapshot.volatility_input.value);
  }
  // The identity indexes are derived from the embedded payload identities, never parallel facts.
  snapshot.curve_set_ref = snapshot.curve_set.curve_set_id;
  snapshot.fixing_store_ref = snapshot.fixing_store.fixing_store_id;
  if (snapshot.volatility_input.present) {
    snapshot.volatility_ref = ValueOrReason<std::string>::of(
        snapshot.volatility_input.value.volatility_input_id);
  }
  const DerivedIdentity identity = compute_snapshot_identities(snapshot);
  snapshot.snapshot_id = identity.id;
  snapshot.content_fingerprint = identity.content_fingerprint;
}

}  // namespace Shiori::rates::dto
