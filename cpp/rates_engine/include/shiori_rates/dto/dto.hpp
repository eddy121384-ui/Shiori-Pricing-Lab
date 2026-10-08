#pragma once
//
// Umbrella header for the Shiori Rates DTO layer.
//
// The DTO layer is `shiori_rates_dto`. It has ONE hard rule (docs/34 section 2.3): it must not
// depend on QuantLib in any way, directly or transitively. The isolation guard enforces that rule
// mechanically against every file under `src/dto/` and `include/shiori_rates/dto/`.
//
#include "shiori_rates/dto/calibration.hpp"
#include "shiori_rates/dto/canonical.hpp"
#include "shiori_rates/dto/codec_traits.hpp"
#include "shiori_rates/dto/enum_tokens.hpp"
#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/dto/fingerprint.hpp"
#include "shiori_rates/dto/immutable.hpp"
#include "shiori_rates/dto/kernel_input.hpp"
#include "shiori_rates/dto/market.hpp"
#include "shiori_rates/dto/numeric.hpp"
#include "shiori_rates/dto/result.hpp"
#include "shiori_rates/dto/sha256.hpp"
#include "shiori_rates/dto/time.hpp"
#include "shiori_rates/dto/units.hpp"
#include "shiori_rates/dto/value_or_reason.hpp"
#include "shiori_rates/dto/version.hpp"
