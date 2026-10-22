#pragma once
//
// Fault injection for the adapter's fail-closed tests. NOT a production surface.
//
// The restored-state verification in `QuantLibSettingsGuard` can only fail if restoration itself is
// broken, so a test cannot reach that branch by ordinary use. These hooks let the fail-closed
// behaviour be proven instead of assumed: poison the adapter, observe that every subsequent
// operation refuses, then un-poison so the rest of a test binary is unaffected.
//
// No production target includes this header. `tools/rates_engine_cli` and every non-test target must
// never call these functions; the isolation guard does not police that, so it is stated here and in
// the PR body instead.
//
#include <string>

namespace Shiori::rates::adapter {

void poison_adapter_for_testing(std::string reason);
void unpoison_adapter_for_testing();

}  // namespace Shiori::rates::adapter
