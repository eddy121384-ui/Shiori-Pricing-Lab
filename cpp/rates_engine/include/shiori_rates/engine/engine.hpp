#pragma once
//
// Deterministic engine identity and the two operations #227 can honestly support.
//
// #227 does NOT price anything. The engine surface it exposes is exactly:
//   * a deterministic engine/build/runtime identity (the G-1 Lane A fact plus the build identity);
//   * `validate_kernel_input` — decode + full contract validation, returning the accepted input;
//   * `canonicalize_kernel_input` — decode + validate + re-canonicalize, returning the canonical
//     UTF-8 bytes and their fingerprint.
//
// No function here computes a price, a discount factor, a curve, a volatility or a sensitivity.
//
// This header must not include QuantLib: `shiori_rates_core` may not expose or include QuantLib, and
// the adapter is the only production containment boundary (docs/34 section 2.3).
//
#include <string>
#include <string_view>

#include "shiori_rates/dto/kernel_input.hpp"

namespace Shiori::rates::engine {

// `engine_version` is a REQUIRED cache-key component (#226 section 7.6) and participates in replay
// identity (#225 section 12.2). It identifies SHIORI's engine, never QuantLib's version: #226
// section 12.3 forbids collapsing source + methodology + version into one field and forbids
// overloading this one with the dependency version.
[[nodiscard]] std::string_view engine_name() noexcept;
[[nodiscard]] std::string_view engine_version() noexcept;

// The deterministic method token of the lane this build implements. #227 implements no pricing
// method, so the honest token names the boundary itself rather than a pricing methodology that has
// not been approved.
[[nodiscard]] std::string_view engine_method() noexcept;

[[nodiscard]] dto::EngineIdentity identity();

// Build identity: build type, compiler, acquisition mode, dependency pins, numeric flags,
// sanitizers, git revision. Reported through the CLI and Lane B telemetry; it is never placed inside
// a result document (#226 section 12.3 assigns it to Lane B).
//
// No timestamp and no address is recorded: a configure-time clock reading would make the artifact
// non-reproducible, and source identity is carried by `git_revision` (with an explicit dirty
// marker) instead.
struct BuildIdentity {
  std::string build_type;
  std::string compiler;
  std::string compiler_version;
  std::string acquisition_mode;
  std::string vcpkg_baseline;
  std::string vcpkg_triplet;
  std::string numeric_flags;
  std::string sanitizers;
  std::string git_revision;
};

[[nodiscard]] const BuildIdentity& build_identity();

// Result of one deterministic operation. `status` is a transport token, never a #225 domain code.
struct OperationOutcome {
  bool success = false;
  std::string payload_json;      // canonical UTF-8 JSON of the operation payload
  std::string fingerprint;       // content fingerprint of the accepted input
  std::string refusal_token;     // structural refusal token when !success
  std::string refusal_pointer;   // JSON-pointer-like locator of the refusal
  std::string refusal_message;
};

// Decodes, validates and re-canonicalizes kernel-input JSON. Never partial: a refusal returns no
// payload at all.
[[nodiscard]] OperationOutcome validate_kernel_input(std::string_view canonical_json);

// Same validation, plus the canonical bytes and fingerprint that the no-I/O kernel would consume.
[[nodiscard]] OperationOutcome canonicalize_kernel_input(std::string_view canonical_json);

}  // namespace Shiori::rates::engine
