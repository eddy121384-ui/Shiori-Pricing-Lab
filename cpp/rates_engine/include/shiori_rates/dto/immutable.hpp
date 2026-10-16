#pragma once
//
// Deep immutability of published request state (#226 sections 6.1-6.8) and the deterministic cache
// key (#226 sections 7.1-7.6, 8.2).
//
// IMMUTABILITY DESIGN
//   * `FrozenKernelInput` OWNS the DTO value, the canonical UTF-8 bytes and the fingerprint. Nothing
//     in it aliases caller storage, so no caller mutation can move fingerprinted content.
//   * `PublishedKernelInput` holds a `std::shared_ptr<const FrozenKernelInput>`. Its accessors
//     return `const&` or a `std::string_view` whose lifetime is dominated by the published object;
//     no API returns a mutable DTO reference, a pointer into caller storage, or a `std::span`.
//   * Publication takes the DTO BY VALUE and re-canonicalizes it. A caller that keeps its original
//     object can mutate it freely: the published request cannot observe that mutation. This is what
//     `tests/determinism` proves with explicit alias/backing-storage mutation attempts.
//
// CACHE KEY DESIGN
//   The key is a struct of explicit, deterministic components. There is deliberately NO pointer
//   field, NO clock field, NO global-lookup field and NO mutable-object field in the type at all, so
//   the forbidden components of #226 section 7.2 are unrepresentable rather than merely disallowed.
//   `from_components` refuses an empty/unknown component, because #226 section 7.6 makes unknown
//   engine or dependency identity non-reusable.
//
#include <cstddef>
#include <memory>
#include <string>
#include <string_view>
#include <utility>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/kernel_input.hpp"

namespace Shiori::rates::dto {

struct FrozenKernelInput {
  RatesKernelInput owning_value;
  std::string canonical_bytes;
  std::string content_fingerprint;

  // A request-local view over engine-owned storage. Valid only while the owning frozen object (and
  // therefore the published request) is alive; it is never retained as published DTO state.
  [[nodiscard]] std::string_view canonical_view() const noexcept { return canonical_bytes; }
};

// Freezes a kernel input: moves it into owning storage, canonicalizes it once, and fingerprints the
// canonical bytes.
[[nodiscard]] std::shared_ptr<const FrozenKernelInput> freeze_kernel_input(RatesKernelInput value);

class PublishedKernelInput {
 public:
  PublishedKernelInput() = default;

  [[nodiscard]] static PublishedKernelInput publish(RatesKernelInput value) {
    return PublishedKernelInput(freeze_kernel_input(std::move(value)));
  }

  [[nodiscard]] bool valid() const noexcept { return frozen_ != nullptr; }
  [[nodiscard]] const FrozenKernelInput& frozen() const;
  [[nodiscard]] std::string_view canonical_view() const { return frozen().canonical_view(); }
  [[nodiscard]] const std::string& content_fingerprint() const {
    return frozen().content_fingerprint;
  }
  [[nodiscard]] const RatesKernelInput& value() const { return frozen().owning_value; }
  [[nodiscard]] std::size_t canonical_size() const noexcept {
    return frozen_ == nullptr ? 0U : frozen_->canonical_bytes.size();
  }

 private:
  explicit PublishedKernelInput(std::shared_ptr<const FrozenKernelInput> frozen)
      : frozen_(std::move(frozen)) {}
  std::shared_ptr<const FrozenKernelInput> frozen_;
};

// ---------------------------------------------------------------------------------------------
// Cache key (#226 sections 7.2/7.6, 8.2)
// ---------------------------------------------------------------------------------------------
struct CacheKeyComponents {
  std::string inputs_fingerprint;              // K1: the frozen kernel-input identity
  std::string engine_version;                  // K6: REQUIRED component, never optional
  std::string methodology_id;                  // K2: methodology identity
  std::string methodology_version;             // K2
  std::string quantlib_version;                // dependency identity (version alone is insufficient
                                               // for G-2, hence the configuration field below)
  std::string quantlib_macro_configuration;    // G-2 macro/configuration identity
  std::string acquisition_mode;                // vcpkg | FETCHCONTENT
  std::string dependency_baseline_and_triplet; // exact pinned baseline + triplet identity
  std::string build_numeric_flags;             // flags that affect numerics

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CacheKeyComponents from_canonical(const CanonicalValue& node,
                                                         const std::string& pointer);
};

struct CacheKey {
  CacheKeyComponents components;
  std::string digest;  // derived from the canonical components

  [[nodiscard]] static CacheKey from_components(CacheKeyComponents components,
                                                const std::string& pointer);
  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static CacheKey from_canonical(const CanonicalValue& node,
                                               const std::string& pointer);
  // A key is reusable only when every REQUIRED component is non-empty and the digest matches.
  [[nodiscard]] bool is_reusable(std::string* refusal_reason = nullptr) const;
  [[nodiscard]] std::string compute_digest() const;
};

}  // namespace Shiori::rates::dto
