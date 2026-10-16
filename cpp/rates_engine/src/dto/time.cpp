#include "shiori_rates/dto/time.hpp"

#include <cstddef>
#include <string>
#include <string_view>
#include <utility>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/error.hpp"

namespace Shiori::rates::dto {

namespace {

bool all_digits(const std::string_view text) noexcept {
  if (text.empty()) {
    return false;
  }
  for (const char character : text) {
    if (character < '0' || character > '9') {
      return false;
    }
  }
  return true;
}

int to_int(const std::string_view text) noexcept {
  int value = 0;
  for (const char character : text) {
    value = (value * 10) + (character - '0');
  }
  return value;
}

// Proleptic-Gregorian leap-year arithmetic. This is representability validation only; it is not a
// business-day or holiday calendar, which belongs to #228.
bool is_leap_year(const int year) noexcept {
  return ((year % 4) == 0 && (year % 100) != 0) || ((year % 400) == 0);
}

int days_in_month(const int year, const int month) noexcept {
  static constexpr int kMonthLengths[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (month < 1 || month > 12) {
    return 0;
  }
  if (month == 2 && is_leap_year(year)) {
    return 29;
  }
  return kMonthLengths[month - 1];
}

}  // namespace

bool is_valid_iso_date(const std::string_view text) noexcept {
  if (text.size() != 10) {
    return false;
  }
  if (text[4] != '-' || text[7] != '-') {
    return false;
  }
  if (!all_digits(text.substr(0, 4)) || !all_digits(text.substr(5, 2)) ||
      !all_digits(text.substr(8, 2))) {
    return false;
  }
  const int year = to_int(text.substr(0, 4));
  const int month = to_int(text.substr(5, 2));
  const int day = to_int(text.substr(8, 2));
  const int month_length = days_in_month(year, month);
  return month_length != 0 && day >= 1 && day <= month_length;
}

bool is_valid_iso_timestamp(const std::string_view text) noexcept {
  // YYYY-MM-DDTHH:MM:SS[.fraction] then an explicit offset: Z or ±HH:MM.
  if (text.size() < 20) {
    return false;
  }
  if (!is_valid_iso_date(text.substr(0, 10))) {
    return false;
  }
  if (text[10] != 'T') {
    return false;
  }
  if (text[13] != ':' || text[16] != ':') {
    return false;
  }
  if (!all_digits(text.substr(11, 2)) || !all_digits(text.substr(14, 2)) ||
      !all_digits(text.substr(17, 2))) {
    return false;
  }
  const int hour = to_int(text.substr(11, 2));
  const int minute = to_int(text.substr(14, 2));
  const int second = to_int(text.substr(17, 2));
  // No leap-second arithmetic is performed; a leap second is not representable in this V1 shape.
  if (hour > 23 || minute > 59 || second > 59) {
    return false;
  }

  std::size_t index = 19;
  if (index < text.size() && text[index] == '.') {
    ++index;
    const std::size_t fraction_start = index;
    while (index < text.size() && text[index] >= '0' && text[index] <= '9') {
      ++index;
    }
    if (index == fraction_start) {
      return false;
    }
  }
  if (index >= text.size()) {
    return false;
  }
  if (text[index] == 'Z') {
    return index + 1 == text.size();
  }
  if (text[index] != '+' && text[index] != '-') {
    return false;
  }
  ++index;
  if (index + 5 != text.size()) {
    return false;
  }
  if (text[index + 2] != ':') {
    return false;
  }
  if (!all_digits(text.substr(index, 2)) || !all_digits(text.substr(index + 3, 2))) {
    return false;
  }
  const int offset_hours = to_int(text.substr(index, 2));
  const int offset_minutes = to_int(text.substr(index + 3, 2));
  return offset_hours <= 23 && offset_minutes <= 59;
}

Date::Date(std::string value) : text(std::move(value)) {
  if (!is_valid_iso_date(text)) {
    fail(ContractViolationKind::kInvalidDate, "", "not an ISO-8601 YYYY-MM-DD date");
  }
}

Date Date::parse(const std::string_view value, const std::string& pointer) {
  if (!is_valid_iso_date(value)) {
    fail(ContractViolationKind::kInvalidDate, pointer,
         "value is not an explicit ISO-8601 YYYY-MM-DD date; no default date is ever substituted");
  }
  return Date(std::string(value));
}

Date Date::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  if (!node.is_string()) {
    fail_wrong_type(pointer, "string ISO-8601 date", node.type_name());
  }
  return Date::parse(node.as_string(), pointer);
}

CanonicalValue Date::to_canonical() const {
  // A default-constructed `Date` is an empty placeholder that exists only so DTO structs can be
  // default-constructed. It must never reach the wire: an absent date is not a date.
  if (text.empty()) {
    fail(ContractViolationKind::kInvalidDate, "",
         "an unset date must never be serialized; no default date exists");
  }
  return CanonicalValue(text);
}

Timestamp::Timestamp(std::string value) : text(std::move(value)) {
  if (!is_valid_iso_timestamp(text)) {
    fail(ContractViolationKind::kInvalidTimestamp, "",
         "not an ISO-8601 timestamp with an explicit UTC offset");
  }
}

Timestamp Timestamp::parse(const std::string_view value, const std::string& pointer) {
  if (!is_valid_iso_timestamp(value)) {
    fail(ContractViolationKind::kInvalidTimestamp, pointer,
         "value must be an ISO-8601 timestamp with an explicit offset; UTC is never assumed");
  }
  return Timestamp(std::string(value));
}

Timestamp Timestamp::from_canonical(const CanonicalValue& node, const std::string& pointer) {
  if (!node.is_string()) {
    fail_wrong_type(pointer, "string ISO-8601 timestamp", node.type_name());
  }
  return Timestamp::parse(node.as_string(), pointer);
}

CanonicalValue Timestamp::to_canonical() const {
  if (text.empty()) {
    fail(ContractViolationKind::kInvalidTimestamp, "",
         "an unset timestamp must never be serialized; no implicit instant exists");
  }
  return CanonicalValue(text);
}

}  // namespace Shiori::rates::dto
