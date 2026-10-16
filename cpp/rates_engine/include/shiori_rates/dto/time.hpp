#pragma once
//
// Calendar-date and timestamp DTOs.
//
// SCOPE LIMIT (deliberate): these types validate *representability* only — ISO-8601 shape and
// calendar plausibility. They contain NO business-day, holiday, spot-lag, schedule or day-count
// behaviour. Those are curve/fixing/calendar primitives owned by Issue #228 (docs/32 section 2,
// docs/33 section 7). Inventing them here would pre-implement #228.
//
// #225 requires every date to be explicit ("never system date", "no silent date roll"), so these
// types have no "today" constructor and no clock access.
//
#include <string>
#include <string_view>

#include "shiori_rates/dto/canonical.hpp"

namespace Shiori::rates::dto {

// A calendar date in the exact ISO-8601 form `YYYY-MM-DD`.
struct Date {
  std::string text;  // canonical `YYYY-MM-DD`

  Date() = default;
  explicit Date(std::string value);  // validates; refuses anything else

  [[nodiscard]] static Date parse(std::string_view value, const std::string& pointer);
  [[nodiscard]] static Date from_canonical(const CanonicalValue& node, const std::string& pointer);
  [[nodiscard]] CanonicalValue to_canonical() const;

  [[nodiscard]] bool empty() const noexcept { return text.empty(); }
  friend bool operator==(const Date& left, const Date& right) { return left.text == right.text; }
  friend bool operator!=(const Date& left, const Date& right) { return !(left == right); }
};

// An ISO-8601 timestamp with an explicit UTC offset: `YYYY-MM-DDTHH:MM:SS[.fff](Z|±HH:MM)`.
// #225 section 8.5 requires an explicit as-of instant with an explicit offset, and section 15.3
// requires an explicit offset on every applicable timestamp. A timestamp without an offset is
// refused rather than assumed to be UTC.
struct Timestamp {
  std::string text;

  Timestamp() = default;
  explicit Timestamp(std::string value);

  [[nodiscard]] static Timestamp parse(std::string_view value, const std::string& pointer);
  [[nodiscard]] static Timestamp from_canonical(const CanonicalValue& node,
                                               const std::string& pointer);
  [[nodiscard]] CanonicalValue to_canonical() const;

  [[nodiscard]] bool empty() const noexcept { return text.empty(); }
  friend bool operator==(const Timestamp& left, const Timestamp& right) {
    return left.text == right.text;
  }
  friend bool operator!=(const Timestamp& left, const Timestamp& right) { return !(left == right); }
};

// True when `text` is a plausible proleptic-Gregorian `YYYY-MM-DD` date (leap years honoured).
// This is representability validation, not a business-day calendar.
[[nodiscard]] bool is_valid_iso_date(std::string_view text) noexcept;

// True when `text` is an ISO-8601 timestamp with an explicit offset.
[[nodiscard]] bool is_valid_iso_timestamp(std::string_view text) noexcept;

}  // namespace Shiori::rates::dto
