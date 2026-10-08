#pragma once
//
// `NumericWithUnit` — #225 principles P2/P3 ("no naked numbers", "units and meaning are
// machine-readable").
//
// One economic fact per field (P1): the number and its unit are separate fields and are never
// collapsed into a formatted string. Currency denomination is NOT carried here: #225 sections 12.2
// and 12.3 put currency on the enclosing object (`headline.currency`, `result_currency`), so that a
// single fact has a single authoritative location.
//
#include <string>

#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/units.hpp"

namespace Shiori::rates::dto {

struct NumericWithUnit {
  double value = 0.0;
  Unit unit = Unit::kDecimal;

  NumericWithUnit() = default;
  NumericWithUnit(double value_in, Unit unit_in) : value(value_in), unit(unit_in) {}

  [[nodiscard]] CanonicalValue to_canonical() const;
  [[nodiscard]] static NumericWithUnit from_canonical(const CanonicalValue& node,
                                                      const std::string& pointer);

  // Applicability helper: #225 states specific required units (for example a PRESENT fixing value
  // must carry DECIMAL_ANNUAL, and a TENOR bucket coordinate must carry YEARS_FRACTION). Refusing a
  // mismatch is a contract rule, not a methodology choice.
  [[nodiscard]] const NumericWithUnit& require_unit(Unit expected,
                                                    const std::string& pointer) const;

  friend bool operator==(const NumericWithUnit& left, const NumericWithUnit& right) {
    return left.value == right.value && left.unit == right.unit;
  }
};

}  // namespace Shiori::rates::dto
