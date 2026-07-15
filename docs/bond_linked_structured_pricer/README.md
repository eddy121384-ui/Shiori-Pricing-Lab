# Bond Linked Structured Pricer — v1.3 reference specs

This directory holds the v1.3 reference specifications for the Bond Linked
Structured Pricer. None of these files authorize implementation by
themselves; see `AGENTS.md` for the actual implementation authority order.

## Files

- `SPEC_v1.3.md` — future-state/reference specification (v1.3). Does not
  authorize implementation.
- `ANNEX_A_v1.3.md` — methodology reference (v1.3). Only sections explicitly
  invoked by the current Eddy-approved issue or PR slice are binding for
  that slice.
- `ANNEX_B_v1.3.md` — Annex B: FTP file specification (v1.3). Future-state/
  reference; does not authorize implementation.
- `ANNEX_C_v1.3.md` — Annex C: UI/UX and brand visual guidance (v1.3).
  Future-state/reference; does not authorize implementation.

## Scope of this PR

This PR **only lands the reference specs** above. It makes no code, test, CI,
architecture-doc, roadmap, or pricing-implementation changes, and introduces no
Bloomberg or QuantLib dependency.

An integration plan that maps these specs onto the existing architecture will be
proposed in a **separate follow-up PR**.
