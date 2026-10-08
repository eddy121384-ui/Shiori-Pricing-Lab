#pragma once
//
// QuantLib containment boundary (docs/34 section 2.3 and section 3).
//
// `shiori_rates_quantlib_adapter` is the ONLY production target that may include a QuantLib header,
// and this is the ONLY public header of that target. It therefore must not include QuantLib itself:
// every QuantLib type is hidden behind a pimpl. No QuantLib type may cross this interface, and no
// QuantLib identity may be placed inside a #225 result document (docs/34 section 12.3 keeps G-2/G-3
// out of Lane A).
//
// #227 links QuantLib, records its identity, guards the process-global QuantLib `Settings` state and
// serializes QuantLib execution. It constructs no instrument, curve, model or engine, so no financial
// value can be produced here.
//
#include <cstdint>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>

namespace Shiori::rates::adapter {

// ---------------------------------------------------------------------------------------------
// Dependency identity (docs/34 G-2/G-3). Version alone is explicitly insufficient: the macro /
// configuration identity is part of the same record.
// ---------------------------------------------------------------------------------------------
struct QuantLibIdentity {
  std::string version;                  // dotted triple, for example "1.43.0"
  std::string version_string;           // the library's own version string when it defines one
  int major = 0;
  int minor = 0;
  int patch = 0;
  std::string macro_configuration;      // deterministic "MACRO=VALUE;MACRO=VALUE" list, sorted
  std::string build_platform;           // compiler + architecture the adapter was built for
  std::string global_settings_quantifiable;  // the process-global state shape this build exposes

  // A dependency identity with an unset version is never reusable (#226 section 7.6).
  [[nodiscard]] bool complete() const noexcept { return major > 0 && minor > 0; }
};

[[nodiscard]] const QuantLibIdentity& quantlib_identity();

// Proves the adapter is linked against QuantLib and returns the library's own opaque build stamp.
// `complete()` must be true; otherwise callers fail closed.
[[nodiscard]] bool quantlib_linked() noexcept;

// ---------------------------------------------------------------------------------------------
// Process-global QuantLib `Settings` guard (docs/34 section 3.7).
//
// QuantLib's evaluation date is process-global mutable state. The guard:
//   * records whether the previous evaluation date was UNSET (a raw-null QuantLib date, i.e.
//     "follow the clock") or an explicit date;
//   * sets the requested explicit date;
//   * restores the exact previous state on destruction — calling QuantLib's own
//     `resetEvaluationDate()` for the UNSET case rather than writing a substitute date, and setting
//     the exact previous date otherwise;
//   * re-reads the state after restoring and VERIFIES it; a failed verification POISONS the adapter
//     permanently rather than leaving an unverified global behind.
//
// A poisoned adapter refuses every subsequent operation. That is the fail-closed behaviour section
// 3.7 requires: an unverifiable global state is not treated as safe.
//
// The guard is non-copyable and non-movable. Nesting is refused: a nested guard could restore an
// intermediate state and make the outer guard's verification meaningless.
// ---------------------------------------------------------------------------------------------
class QuantLibSettingsGuard {
 public:
  // `iso_valuation_date` is `YYYY-MM-DD` (the DTO wire form). An invalid date refuses.
  explicit QuantLibSettingsGuard(std::string_view iso_valuation_date);
  ~QuantLibSettingsGuard();

  QuantLibSettingsGuard(const QuantLibSettingsGuard&) = delete;
  QuantLibSettingsGuard& operator=(const QuantLibSettingsGuard&) = delete;
  QuantLibSettingsGuard(QuantLibSettingsGuard&&) = delete;
  QuantLibSettingsGuard& operator=(QuantLibSettingsGuard&&) = delete;

  // True when the requested date is set and, after destruction, when restoration verified.
  [[nodiscard]] bool satisfied() const noexcept { return satisfied_; }
  // The value of the process-global QuantLib settings BEFORE the guard ran.
  [[nodiscard]] bool previous_evaluation_date_was_unset() const noexcept {
    return previous_evaluation_date_was_unset_;
  }
  [[nodiscard]] const std::string& requested_iso_date() const noexcept { return requested_iso_date_; }
  // Non-empty when the guard refused or when restoration could not be verified.
  [[nodiscard]] const std::string& refusal_reason() const noexcept { return refusal_reason_; }

 private:
  struct Impl;
  std::unique_ptr<Impl> impl_;
  std::string requested_iso_date_;
  std::string refusal_reason_;
  bool previous_evaluation_date_was_unset_ = false;
  bool satisfied_ = false;
};

// ---------------------------------------------------------------------------------------------
// Poison flag
//
// Fault injection (poisoning / un-poisoning for a test) lives in the separate `testing.hpp` header,
// so that no production header offers a way to disable a fail-closed check.
// ---------------------------------------------------------------------------------------------
[[nodiscard]] bool adapter_poisoned() noexcept;
[[nodiscard]] std::string adapter_poison_reason();

// ---------------------------------------------------------------------------------------------
// Serialization gate (docs/34 section 5.2).
//
// INITIAL PRODUCTION POSTURE: parallel pricing is DISABLED. QuantLib's process-global `Settings`,
// its observer/singleton patterns and its non-reentrant global state make concurrent execution
// unsafe, so exactly one QuantLib-touching operation may run at a time per process. This is an
// enforced serialization, not an assertion of thread safety: the engine makes no thread-safety
// claim about QuantLib, and does not rely on one.
//
// Re-entering the gate on a thread that already holds it is refused rather than deadlocked: a
// deadlock would be an unavailable-engine failure with no diagnosis.
// ---------------------------------------------------------------------------------------------
inline constexpr bool kProductionParallelPricingEnabled = false;

class SerializationGate {
 public:
  SerializationGate() = default;
  ~SerializationGate();
  SerializationGate(const SerializationGate&) = delete;
  SerializationGate& operator=(const SerializationGate&) = delete;
  SerializationGate(SerializationGate&& other) noexcept;
  SerializationGate& operator=(SerializationGate&& other) noexcept;

  // Blocks until this process holds the gate. Refuses re-entry on the same thread.
  [[nodiscard]] static SerializationGate acquire(std::string_view operation);

  [[nodiscard]] bool held() const noexcept { return held_; }
  [[nodiscard]] const std::string& operation() const noexcept { return operation_; }

  // Lane B counters (G-6). Observations only; never a pass/fail gate.
  struct Counters {
    std::int64_t acquisitions = 0;
    std::int64_t waits = 0;
    std::int64_t reentrant_refusals = 0;
  };
  [[nodiscard]] static Counters counters();
  static void reset_counters_for_testing() noexcept;

 private:
  std::unique_lock<std::mutex> lock_;
  std::string operation_;
  bool held_ = false;
};

}  // namespace Shiori::rates::adapter
