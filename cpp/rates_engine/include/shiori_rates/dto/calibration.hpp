#pragma once
//
// Model / calibration schema plumbing — docs/33 (Issue #225) sections 14.2/14.3.
//
// #227 section 4 authorises this explicitly: "model / calibration envelopes to the extent required
// for schema/round-trip support", and "#227 may implement schema plumbing without executable
// pricing/calibration behavior". #227 must not invent a model parameter, objective, optimizer,
// tolerance or convergence shape while RED-225-M1/M2 values are open, and section 14.2 states those
// values are UNRESOLVED-RED.
//
// Therefore these are ENVELOPES: version + derived identity + byte-exact canonical payload +
// fingerprint, with the payload preserved and verified but not re-typed here. The object that
// publishes the concrete section 14.2/14.3 field shapes owns them. `payload` is inside the identity
// preimage, so an output-only fact such as a calibration timestamp participates in identity exactly
// as section 14.3 requires, without #227 having to name its field.
//
#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/market.hpp"

namespace Shiori::rates::dto {

struct ModelCalibrationInput {
  std::string calibration_input_id;
  CanonicalValue payload;  // section 14.2 content (envelope placeholder)
  SourceProvenance provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ModelCalibrationInput from_canonical(const CanonicalValue& node,
                                                            const std::string& pointer);
};

struct ModelCalibrationResult {
  std::string calibration_result_id;
  CanonicalValue payload;  // section 14.3 content (envelope placeholder)
  SourceProvenance provenance;
  std::string content_fingerprint;

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static ModelCalibrationResult from_canonical(const CanonicalValue& node,
                                                             const std::string& pointer);
};

[[nodiscard]] CanonicalValue calibration_input_identity_preimage(const ModelCalibrationInput& input);
[[nodiscard]] CanonicalValue calibration_input_fingerprint_preimage(
    const ModelCalibrationInput& input, const std::string& input_id);
[[nodiscard]] DerivedIdentity compute_calibration_input_identities(const ModelCalibrationInput& input);
void verify_calibration_input_identities(const ModelCalibrationInput& input,
                                         const std::string& pointer);
void assign_derived_identities(ModelCalibrationInput& input);

[[nodiscard]] CanonicalValue calibration_result_identity_preimage(
    const ModelCalibrationResult& result);
[[nodiscard]] CanonicalValue calibration_result_fingerprint_preimage(
    const ModelCalibrationResult& result, const std::string& result_id);
[[nodiscard]] DerivedIdentity compute_calibration_result_identities(
    const ModelCalibrationResult& result);
void verify_calibration_result_identities(const ModelCalibrationResult& result,
                                          const std::string& pointer);
void assign_derived_identities(ModelCalibrationResult& result);

}  // namespace Shiori::rates::dto
