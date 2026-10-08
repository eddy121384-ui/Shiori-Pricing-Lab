// Fail-closed behaviour of a poisoned adapter.
//
// A poisoned adapter means the process-global QuantLib state could not be verified as restored. The
// adapter must then refuse EVERY subsequent operation instead of continuing to produce results whose
// global configuration is unknown.
//
// This lives in its own test executable because poisoning is process-wide and permanent until
// explicitly cleared: sharing a binary with the other adapter tests would make their result depend on
// test order.
#include <gtest/gtest.h>

#include <string>

#include "shiori_rates/dto/error.hpp"
#include "shiori_rates/quantlib_adapter/adapter.hpp"
#include "shiori_rates/quantlib_adapter/testing.hpp"

namespace {

using Shiori::rates::adapter::QuantLibSettingsGuard;
using Shiori::rates::adapter::SerializationGate;

}  // namespace

TEST(AdapterPoison, RefusesEveryOperationOncePoisoned) {
  ASSERT_FALSE(Shiori::rates::adapter::adapter_poisoned());
  Shiori::rates::adapter::poison_adapter_for_testing("simulated failed restore");

  EXPECT_TRUE(Shiori::rates::adapter::adapter_poisoned());
  EXPECT_NE(Shiori::rates::adapter::adapter_poison_reason().find("simulated failed restore"),
            std::string::npos);

  // Every entry point that would touch QuantLib's global state must fail closed.
  EXPECT_THROW(
      {
        const SerializationGate gate = SerializationGate::acquire("poisoned");
        (void)gate.held();
      },
      Shiori::rates::dto::ContractViolation);

  {
    QuantLibSettingsGuard guard("2026-06-10");
    EXPECT_FALSE(guard.satisfied());
    EXPECT_NE(guard.refusal_reason().find("poisoned"), std::string::npos) << guard.refusal_reason();
  }

  // A poisoned adapter stays poisoned: nothing recovers it implicitly.
  EXPECT_TRUE(Shiori::rates::adapter::adapter_poisoned());

  Shiori::rates::adapter::unpoison_adapter_for_testing();
  EXPECT_FALSE(Shiori::rates::adapter::adapter_poisoned());
  EXPECT_TRUE(Shiori::rates::adapter::adapter_poison_reason().empty());

  // With the poison cleared the engine is usable again, which proves the refusal came from the poison
  // flag and not from an unrelated failure.
  const SerializationGate recovered = SerializationGate::acquire("recovered");
  EXPECT_TRUE(recovered.held());
  QuantLibSettingsGuard guard("2026-06-10");
  EXPECT_TRUE(guard.satisfied()) << guard.refusal_reason();
}
