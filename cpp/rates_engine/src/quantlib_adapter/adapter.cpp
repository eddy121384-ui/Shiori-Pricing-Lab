// QuantLib containment boundary implementation.
//
// This file and its directory are the ONLY production location allowed to include QuantLib
// (docs/34 section 3.9). The isolation guard enforces that mechanically.
#include "shiori_rates/quantlib_adapter/adapter.hpp"

#include <ql/settings.hpp>
#include <ql/time/date.hpp>
#include <ql/version.hpp>

#include <algorithm>
#include <atomic>
#include <cstdio>
#include <mutex>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/quantlib_adapter/testing.hpp"

namespace Shiori::rates::adapter {

namespace {

#define SHIORI_STRINGIZE_IMPL(x) #x
#define SHIORI_STRINGIZE(x) SHIORI_STRINGIZE_IMPL(x)

std::atomic<bool> g_poisoned{false};
std::mutex g_poison_mutex;
std::string g_poison_reason;

std::mutex& gate_mutex() {
  static std::mutex mutex;
  return mutex;
}

bool& thread_holds_gate() {
  static thread_local bool holding = false;
  return holding;
}

std::atomic<std::int64_t>& counter_ref(int index) {
  static std::atomic<std::int64_t> acquisitions{0};
  static std::atomic<std::int64_t> waits{0};
  static std::atomic<std::int64_t> refusals{0};
  switch (index) {
    case 0:
      return acquisitions;
    case 1:
      return waits;
    default:
      return refusals;
  }
}

[[nodiscard]] std::string macro_flag(std::string_view name, bool enabled) {
  std::string out(name);
  out.push_back('=');
  out += enabled ? "ON" : "OFF";
  return out;
}

[[nodiscard]] std::string quantlib_macro_configuration() {
  std::vector<std::string> flags;
  // Only macros that change observable QuantLib behaviour are listed. A macro this build does not
  // define reports OFF, which is an accurate statement about this binary.
#if defined(QL_ENABLE_SESSIONS)
  constexpr bool kSessions = true;
#else
  constexpr bool kSessions = false;
#endif
#if defined(QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN)
  constexpr bool kThreadSafeObserver = true;
#else
  constexpr bool kThreadSafeObserver = false;
#endif
#if defined(QL_HIGH_RESOLUTION_DATE)
  constexpr bool kHighResolutionDate = true;
#else
  constexpr bool kHighResolutionDate = false;
#endif
#if defined(QL_DISABLE_DEPRECATED)
  constexpr bool kDisableDeprecated = true;
#else
  constexpr bool kDisableDeprecated = false;
#endif
#if defined(QL_USE_INDEXED_COUPON)
  constexpr bool kIndexedCoupon = true;
#else
  constexpr bool kIndexedCoupon = false;
#endif
  flags.push_back(macro_flag("QL_ENABLE_SESSIONS", kSessions));
  flags.push_back(macro_flag("QL_ENABLE_THREAD_SAFE_OBSERVER_PATTERN", kThreadSafeObserver));
  flags.push_back(macro_flag("QL_HIGH_RESOLUTION_DATE", kHighResolutionDate));
  flags.push_back(macro_flag("QL_DISABLE_DEPRECATED", kDisableDeprecated));
  flags.push_back(macro_flag("QL_USE_INDEXED_COUPON", kIndexedCoupon));
  // Already appended in sorted order by macro name; keep it explicit rather than trust the order.
  std::sort(flags.begin(), flags.end());
  std::string joined;
  for (const std::string& flag : flags) {
    if (!joined.empty()) {
      joined.push_back(';');
    }
    joined += flag;
  }
  return joined;
}

[[nodiscard]] std::string build_platform() {
  std::string out;
#if defined(_MSC_VER)
  out = "MSVC/";
  out += SHIORI_STRINGIZE(_MSC_VER);
#elif defined(__clang__)
  out = "Clang/";
  out += __clang_version__;
#elif defined(__GNUC__)
  out = "GCC/";
  out += SHIORI_STRINGIZE(__GNUC__);
  out.push_back('.');
  out += SHIORI_STRINGIZE(__GNUC_MINOR__);
#else
  out = "unknown-compiler";
#endif
  out += "-";
  out += (sizeof(void*) == 8) ? "64bit" : "32bit";
  return out;
}

// Converts the DTO wire date (already validated as a real proleptic-Gregorian calendar date) into a
// QuantLib date. The DTO layer owns date validity; this function does not re-decide it.
[[nodiscard]] QuantLib::Date to_quantlib_date(std::string_view iso_date) {
  if (iso_date.size() != 10 || iso_date[4] != '-' || iso_date[7] != '-') {
    throw std::invalid_argument("quantlib adapter: expected YYYY-MM-DD");
  }
  const auto parse = [&](std::size_t offset) -> int {
    int value = 0;
    for (std::size_t i = offset; i < offset + 2; ++i) {
      const char c = iso_date[i];
      if (c < '0' || c > '9') {
        throw std::invalid_argument("quantlib adapter: expected digits in YYYY-MM-DD");
      }
      value = value * 10 + (c - '0');
    }
    return value;
  };
  const int year = parse(0) * 100 + parse(2);
  const int month = parse(5);
  const int day = parse(8);
  return QuantLib::Date(day, static_cast<QuantLib::Month>(month), year);
}

[[nodiscard]] std::string from_quantlib_date(const QuantLib::Date& date) {
  char buffer[16];
  std::snprintf(buffer, sizeof(buffer), "%04d-%02d-%02d", date.year(),
                static_cast<int>(date.month()), date.dayOfMonth());
  return std::string(buffer);
}

}  // namespace

const QuantLibIdentity& quantlib_identity() {
  static const QuantLibIdentity identity = [] {
    QuantLibIdentity value;
    // The version is read from QuantLib's OWN version string. QuantLib 1.43's `ql/version.hpp` defines
    // `QL_VERSION` and `QL_HEX_VERSION` but NOT a QL_VERSION_MAJOR/MINOR/PATCH triple (verified
    // against the pinned release), so the dotted components are parsed from the string rather than
    // assumed to exist as macros.
    value.version_string = QL_VERSION;
    value.version = QL_VERSION;
    int components[3] = {0, 0, 0};
    int component = 0;
    for (const char* cursor = QL_VERSION; *cursor != '\0' && component < 3; ++cursor) {
      if (*cursor >= '0' && *cursor <= '9') {
        components[component] = components[component] * 10 + (*cursor - '0');
      } else if (*cursor == '.') {
        ++component;
      } else {
        break;  // a pre-release or vendor suffix ends the numeric prefix
      }
    }
    value.major = components[0];
    value.minor = components[1];
    value.patch = components[2];
    value.macro_configuration = quantlib_macro_configuration();
    value.build_platform = build_platform();
    value.global_settings_quantifiable =
        "evaluation_date(process-global;raw-null=follow-clock);"
        "enforces_todays_historic_fixings(process-global);"
        "quantlib_objects_globally_observable=true";
    return value;
  }();
  return identity;
}

bool quantlib_linked() noexcept {
  // Touching the library's own calendar arithmetic proves the symbol is resolved at link time; a
  // header-only declaration would not.
  static const bool linked = [] {
    const QuantLib::Date d(1, QuantLib::January, 2000);
    return d.dayOfMonth() == 1 && d.year() == 2000;
  }();
  return linked;
}

bool adapter_poisoned() noexcept { return g_poisoned.load(std::memory_order_acquire); }

std::string adapter_poison_reason() {
  const std::lock_guard<std::mutex> lock(g_poison_mutex);
  return g_poison_reason;
}

void poison_adapter_for_testing(std::string reason) {
  {
    const std::lock_guard<std::mutex> lock(g_poison_mutex);
    if (g_poison_reason.empty()) {
      g_poison_reason = std::move(reason);
    }
  }
  g_poisoned.store(true, std::memory_order_release);
}

void unpoison_adapter_for_testing() {
  {
    const std::lock_guard<std::mutex> lock(g_poison_mutex);
    g_poison_reason.clear();
  }
  g_poisoned.store(false, std::memory_order_release);
}

// ---------------------------------------------------------------------------------------------
// SerializationGate
// ---------------------------------------------------------------------------------------------
SerializationGate::~SerializationGate() {
  if (held_) {
    thread_holds_gate() = false;
    held_ = false;
    lock_.unlock();
  }
}

SerializationGate::SerializationGate(SerializationGate&& other) noexcept
    : lock_(std::move(other.lock_)), operation_(std::move(other.operation_)), held_(other.held_) {
  other.held_ = false;
}

SerializationGate& SerializationGate::operator=(SerializationGate&& other) noexcept {
  if (this != &other) {
    if (held_) {
      thread_holds_gate() = false;
      lock_.unlock();
    }
    lock_ = std::move(other.lock_);
    operation_ = std::move(other.operation_);
    held_ = other.held_;
    other.held_ = false;
  }
  return *this;
}

SerializationGate SerializationGate::acquire(std::string_view operation) {
  if (adapter_poisoned()) {
    dto::fail(dto::ContractViolationKind::kInvariantViolation, "",
              "quantlib adapter is poisoned: " + adapter_poison_reason());
  }
  if (thread_holds_gate()) {
    counter_ref(2).fetch_add(1, std::memory_order_relaxed);
    dto::fail(dto::ContractViolationKind::kInvariantViolation, "",
              "refused re-entrant QuantLib execution on one thread; the initial posture serializes "
              "QuantLib per process and must not deadlock instead of diagnosing");
  }
  SerializationGate gate;
  gate.operation_ = std::string(operation);
  counter_ref(0).fetch_add(1, std::memory_order_relaxed);
  if (!gate_mutex().try_lock()) {
    counter_ref(1).fetch_add(1, std::memory_order_relaxed);
    gate_mutex().lock();
  }
  gate.lock_ = std::unique_lock<std::mutex>(gate_mutex(), std::adopt_lock);
  thread_holds_gate() = true;
  gate.held_ = true;
  return gate;
}

SerializationGate::Counters SerializationGate::counters() {
  Counters value;
  value.acquisitions = counter_ref(0).load(std::memory_order_relaxed);
  value.waits = counter_ref(1).load(std::memory_order_relaxed);
  value.reentrant_refusals = counter_ref(2).load(std::memory_order_relaxed);
  return value;
}

void SerializationGate::reset_counters_for_testing() noexcept {
  counter_ref(0).store(0, std::memory_order_relaxed);
  counter_ref(1).store(0, std::memory_order_relaxed);
  counter_ref(2).store(0, std::memory_order_relaxed);
}

// ---------------------------------------------------------------------------------------------
// QuantLibSettingsGuard
// ---------------------------------------------------------------------------------------------
struct QuantLibSettingsGuard::Impl {
  SerializationGate gate;
  QuantLib::Date previous_evaluation_date;
  bool previous_was_unset = false;
  bool previous_enforces_historic_fixings = false;
  bool armed = false;
};

QuantLibSettingsGuard::QuantLibSettingsGuard(std::string_view iso_valuation_date)
    : impl_(std::make_unique<Impl>()) {
  requested_iso_date_ = std::string(iso_valuation_date);

  if (adapter_poisoned()) {
    refusal_reason_ = "quantlib adapter is poisoned: " + adapter_poison_reason();
    return;
  }

  QuantLib::Date requested;
  try {
    requested = to_quantlib_date(iso_valuation_date);
  } catch (const std::exception& error) {
    refusal_reason_ = std::string("invalid valuation date for the settings guard: ") + error.what();
    return;
  }

  impl_->gate = SerializationGate::acquire("quantlib_settings_guard");

  const QuantLib::Settings& const_settings = QuantLib::Settings::instance();
  impl_->previous_evaluation_date = const_settings.evaluationDate();
  // A raw-null QuantLib date means "no explicit evaluation date": the library follows the clock.
  // This is a DIFFERENT state from an explicit date and must be restored as itself.
  impl_->previous_was_unset = (impl_->previous_evaluation_date == QuantLib::Date());
  previous_evaluation_date_was_unset_ = impl_->previous_was_unset;
  impl_->previous_enforces_historic_fixings = const_settings.enforcesTodaysHistoricFixings();
  impl_->armed = true;

  QuantLib::Settings& settings = QuantLib::Settings::instance();
  settings.evaluationDate(requested);

  const QuantLib::Date observed = QuantLib::Settings::instance().evaluationDate();
  if (!(observed == requested)) {
    refusal_reason_ = "QuantLib settings guard could not observe the requested evaluation date";
    poison_adapter_for_testing(refusal_reason_);
    return;
  }
  satisfied_ = true;
}

QuantLibSettingsGuard::~QuantLibSettingsGuard() {
  if (impl_ == nullptr || !impl_->armed) {
    return;
  }
  QuantLib::Settings& settings = QuantLib::Settings::instance();
  if (impl_->previous_was_unset) {
    settings.resetEvaluationDate();
    if (!(QuantLib::Settings::instance().evaluationDate() == QuantLib::Date())) {
      refusal_reason_ =
          "QuantLib settings guard could not restore the unset (follow-the-clock) evaluation date";
      poison_adapter_for_testing(refusal_reason_);
      satisfied_ = false;
      return;
    }
  } else {
    settings.evaluationDate(impl_->previous_evaluation_date);
    if (QuantLib::Settings::instance().evaluationDate() != impl_->previous_evaluation_date) {
      refusal_reason_ =
          "QuantLib settings guard could not restore the previous evaluation date";
      poison_adapter_for_testing(refusal_reason_);
      satisfied_ = false;
      return;
    }
  }
  settings.enforcesTodaysHistoricFixings(impl_->previous_enforces_historic_fixings);
  if (QuantLib::Settings::instance().enforcesTodaysHistoricFixings() !=
      impl_->previous_enforces_historic_fixings) {
    refusal_reason_ =
        "QuantLib settings guard could not restore enforcesTodaysHistoricFixings";
    poison_adapter_for_testing(refusal_reason_);
    satisfied_ = false;
    return;
  }
  // The process-global state is verified restored. Only now is the adapter still usable.
  impl_->armed = false;
}

}  // namespace Shiori::rates::adapter
