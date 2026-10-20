// Settlement-provenance set semantics for RATES_KERNEL_INPUT_V1 (docs/33 Issue #225 section 15.4).
//
// WHAT THIS SUITE PROVES:
//   * ACCEPT - `SettlementProvenance.evidence_refs` in the contract's canonical encoding (strictly
//     ascending, duplicate-free) is accepted and round-trips byte-identically. Contract: docs/33 line
//     1233 - "Set-semantic provenance lists such as `upstream_ids`, `evidence_refs`, capture/surface
//     ids are lexicographically sorted and duplicate-free before serialization."
//   * REFUSE - the same evidence SET in inverse order, with a repeat, or with a single adjacent pair out
//     of order is refused at the offending element's own pointer. The decoder never silently sorts:
//     `settlement_terms_identity_preimage` (kernel_input.cpp) hashes the list in the decoded order, so
//     sorting would both hide a malformed producer document and mint an identity the producer could not
//     have predicted.
//   * IDENTITY - the evidence list order changes the identity preimage and the content fingerprint, and
//     two independently built canonical encodings of the same set agree. Accepting exactly one encoding
//     is therefore what makes `settlement_terms_id` a function of the evidence SET rather than of the
//     producer's iteration order.
//
// FIXTURE POLICY: contract-only synthetic fixtures. The provenance source is `SYNTHETIC_FIXTURE` and the
// methodology texts are the contract's own `UNRESOLVED` token (docs/33 section 7.0); the E-citations are
// shape-only labels. No market data, methodology value or approved token is asserted or implied.
#include <gtest/gtest.h>

#include <string>
#include <utility>
#include <vector>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/market.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"

namespace {

using Shiori::rates::dto::CanonicalValue;
using Shiori::rates::dto::ContractViolation;
using Shiori::rates::dto::ContractViolationKind;
using Shiori::rates::dto::MarketSource;
using Shiori::rates::dto::SettlementProvenance;
using Shiori::rates::dto::SettlementType;
using Shiori::rates::dto::CashSettlementMethodology;
using Shiori::rates::dto::Date;
using Shiori::rates::dto::ValueOrReason;
using Shiori::rates::dto::assign_derived_identities;
using Shiori::rates::dto::SettlementTerms;
using Shiori::rates::dto::settlement_terms_content_fingerprint;
using Shiori::rates::dto::settlement_terms_identity_preimage;

// The contract's own token for a methodology value that is not yet approved (docs/33 section 7.0).
constexpr const char* kUnresolved = "UNRESOLVED";

[[nodiscard]] SettlementProvenance provenance_with(std::vector<std::string> refs) {
  SettlementProvenance provenance;
  provenance.source = MarketSource::kSyntheticFixture;
  provenance.evidence_refs = std::move(refs);
  provenance.methodology_id = std::string(kUnresolved);
  provenance.methodology_version = std::string(kUnresolved);
  return provenance;
}

// A missing refusal is a reported failure, never a sentinel that could be mistaken for a real refusal.
[[nodiscard]] ContractViolation refusal_for(const CanonicalValue& document, const char* what) {
  try {
    (void)SettlementProvenance::from_canonical(document, "");
  } catch (const ContractViolation& violation) {
    return violation;
  }
  ADD_FAILURE() << "this document must be refused: " << what;
  return ContractViolation(ContractViolationKind::kInvariantViolation, "<no refusal>",
                           "no refusal was thrown");
}

}  // namespace

TEST(SettlementProvenanceSetSemantics, AcceptsTheCanonicalSortedDuplicateFreeList) {
  const SettlementProvenance canonical = provenance_with({"E-0001", "E-0002"});
  const CanonicalValue document = canonical.to_canonical();

  const SettlementProvenance decoded = SettlementProvenance::from_canonical(document, "");
  EXPECT_EQ(decoded.to_canonical().dump(), document.dump())
      << "a contract-valid provenance must round-trip byte-identically";
  EXPECT_EQ(decoded.evidence_refs, canonical.evidence_refs);
}

TEST(SettlementProvenanceSetSemantics, RefusesTheSameSetInInverseOrder) {
  const SettlementProvenance inverse = provenance_with({"E-0002", "E-0001"});
  const ContractViolation violation =
      refusal_for(inverse.to_canonical(), "evidence_refs in descending order");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/evidence_refs/1");
}

TEST(SettlementProvenanceSetSemantics, RefusesADuplicatedEvidenceReference) {
  const SettlementProvenance duplicated = provenance_with({"E-0001", "E-0001"});
  const ContractViolation violation =
      refusal_for(duplicated.to_canonical(), "a repeated evidence reference");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/evidence_refs/1");
}

TEST(SettlementProvenanceSetSemantics, RefusesASingleAdjacentPairOutOfOrderInALongerList) {
  const SettlementProvenance partially_sorted = provenance_with({"E-0001", "E-0003", "E-0002"});
  const ContractViolation violation =
      refusal_for(partially_sorted.to_canonical(), "one adjacent pair out of order");
  EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
  EXPECT_EQ(violation.pointer(), "/evidence_refs/2");
}

TEST(SettlementProvenanceSetSemantics, TheEvidenceOrderChangesTheSettlementIdentity) {
  SettlementTerms canonical_terms;
  canonical_terms.source_methodology_provenance = provenance_with({"E-0001", "E-0002"});
  SettlementTerms reordered_terms = canonical_terms;
  reordered_terms.source_methodology_provenance = provenance_with({"E-0002", "E-0001"});

  // The same evidence set, iterated in another order, hashes differently - which is exactly why the
  // decoder refuses the noncanonical encoding instead of silently repairing it.
  EXPECT_NE(settlement_terms_identity_preimage(canonical_terms).dump(),
            settlement_terms_identity_preimage(reordered_terms).dump())
      << "the same evidence set in another order must not hash to the same identity preimage";
  EXPECT_NE(settlement_terms_content_fingerprint(canonical_terms),
            settlement_terms_content_fingerprint(reordered_terms));

  // The canonical encoding is the single accepted order, so the identity is a function of the set.
  SettlementTerms rebuilt_terms;
  rebuilt_terms.source_methodology_provenance = provenance_with({"E-0001", "E-0002"});
  EXPECT_EQ(settlement_terms_content_fingerprint(rebuilt_terms),
            settlement_terms_content_fingerprint(canonical_terms));
}

namespace {

// Contract-shape only: every methodology value uses the explicitly unresolved V1 token.
[[nodiscard]] SettlementTerms synthetic_cash_settlement_terms() {
  SettlementTerms terms;
  terms.settlement_type = SettlementType::kCash;
  terms.settlement_method = ValueOrReason<std::string>::of(std::string(kUnresolved));
  terms.settlement_method_version = ValueOrReason<std::string>::of(std::string(kUnresolved));
  CashSettlementMethodology methodology;
  methodology.methodology_id = kUnresolved;
  methodology.methodology_version = kUnresolved;
  methodology.settlement_rate_source = kUnresolved;
  methodology.settlement_date_rule = kUnresolved;
  terms.cash_settlement_methodology = ValueOrReason<CashSettlementMethodology>::of(methodology);
  terms.settlement_date = ValueOrReason<Date>::not_applicable();
  terms.source_methodology_provenance = provenance_with({});
  assign_derived_identities(terms);
  return terms;
}

}  // namespace

TEST(CashSettlementMethodTokens, AcceptsNonemptyUnresolvedMethodAndVersion) {
  const SettlementTerms fixture = synthetic_cash_settlement_terms();
  const CanonicalValue encoded = fixture.to_canonical();
  const SettlementTerms decoded = SettlementTerms::from_canonical(encoded, "");
  EXPECT_EQ(decoded.to_canonical().dump(), encoded.dump());
}

TEST(CashSettlementMethodTokens, RefusesRehashedEmptyPresentMethod) {
  SettlementTerms terms = synthetic_cash_settlement_terms();
  terms.settlement_method = ValueOrReason<std::string>::of("");
  assign_derived_identities(terms);
  const CanonicalValue encoded = terms.to_canonical();
  try {
    (void)SettlementTerms::from_canonical(encoded, "");
    FAIL() << "rehashed empty PRESENT CASH settlement method must be refused";
  } catch (const ContractViolation& violation) {
    EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
    EXPECT_EQ(violation.pointer(), "/settlement_method/value");
  }
}

TEST(CashSettlementMethodTokens, RefusesRehashedEmptyPresentMethodVersion) {
  SettlementTerms terms = synthetic_cash_settlement_terms();
  terms.settlement_method_version = ValueOrReason<std::string>::of("");
  assign_derived_identities(terms);
  const CanonicalValue encoded = terms.to_canonical();
  try {
    (void)SettlementTerms::from_canonical(encoded, "");
    FAIL() << "rehashed empty PRESENT CASH settlement method version must be refused";
  } catch (const ContractViolation& violation) {
    EXPECT_EQ(violation.kind(), ContractViolationKind::kInvariantViolation);
    EXPECT_EQ(violation.pointer(), "/settlement_method_version/value");
  }
}
