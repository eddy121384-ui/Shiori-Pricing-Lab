"""LSEG Workspace desktop-session read-only swaption-vol probe
(Market Data Reconnection Phase 1).

The LSEG half of the same question the Bloomberg probe asks: can Shiori
obtain a real USD swaption **normal** volatility -- first target 1Y expiry x
5Y tenor, ATM, MID -- from the locally-installed LSEG Workspace desktop
session, and what does the installed client actually offer?

Bounded, read-only workstation diagnostic CLI -- **not** part of the
production pricing, vol, ingestion or workbench path, and never imported by
any of them. This run opens no SQLite database, writes no vol surface,
changes no production field mapping, and calls no pricing entry point. It
calls no LSEG write/object-creation path (no UDF/formula/basket creation, no
streaming subscription).

What "read-only" does *not* mean here, stated precisely because an auditor
will rely on it. The probe calls the session opener, so it does not *close*
a session the operator may already have open -- there is no unconditional
``close_session`` call, because closing the workstation's own Workspace
session is not this probe's business. And this file deliberately *reuses*
the repository's probe helpers, so at *import* time it pulls in production
modules: ``bloomberg_vcub_field_hunter`` imports
``shiori_pricing_lab.app.standalone_option_workbench``, and the shared
evidence module reaches ``bloomberg_dapi_probe``, which imports
``shiori_pricing_lab.data.bloomberg_bond_quote`` at module level. Importing
this probe therefore loads those modules; it never alters them and never
calls a pricing, Black-76 or persistence entry point from them. The
guarantee the phase actually needs -- no production behaviour changes, no
module under ``src/`` importing this file, no schema or field map touched --
holds.

**Everything about the client is discovered, never assumed.** The library
was renamed twice upstream and at least one official documentation example
is stale, so this probe assumes no import name, no function signature, no
enum member and no session-config key. It:

1. tries the candidate client module names in order and reports which one
   the workstation actually has, with its version;
2. opens a desktop session through whatever ``open_session``-shaped entry
   point the installed client exposes, and reports the resulting state;
3. introspects the installed package itself for the swaption-surface
   capability -- whether the module exists, what the ``Definition`` callable
   actually accepts (via :func:`inspect.signature`), and which
   volatility-type members its enums really have -- so the parameter names
   the operator must fill in are *read off the installed client*, not copied
   from a doc page;
4. probes only the RICs, fields and surface parameters the operator
   supplies.

**No RIC, field mnemonic, currency or surface parameter is invented here.**
There is no default ``--ric``, no default ``--field`` and no default
``--ipa-param``. The phase's own target coordinate is not silently used as
one either: a report that says "USD 1Y x 5Y ATM MID" because that is what
the phase targeted would make an unstated coordinate look stated. With no
candidate supplied, the value stage reports ``not_attempted`` and says why.

**What is documented upstream, and what that is worth here.** LSEG's own
reference guide and the published ``lseg-data`` package are the basis for
the candidate names this probe *tries* -- the ``lseg.data`` client, the
``ld.open_session()`` desktop default, ``ld.get_data``, and an
interest-rate swaption volatility-surface capability that exposes a
normal-vs-lognormal output choice and a price side. Every one of those is a
*candidate*, hinted in ``docs/35_market_data_reconnection_phase1_probe.md``
with its source, and every one is verified at runtime by this probe before
anything depends on it. A claim in a document is not evidence; an
introspection result from the installed package is.

**Four evidence classes, never mixed** -- the same vocabulary the Bloomberg
probe uses, so the two reports are directly comparable. In particular, a
returned number does not establish its own vol type or unit: "this field is
a normal vol quoted in bp" is a semantic claim about a field's meaning. This
probe reports the operator's declaration, plus whatever the installed client
publishes about its own enums and signatures, and never labels a value
"normal vol, bp" on the strength of its magnitude -- the silent unit
coercion Annex A.8.1 forbids.

**Local output only.** Reports go to a git-ignored directory
(``shiori_lseg_swaption_vol_probe_output/``). They can contain licensed
market values and the operator's own inputs; they are workstation evidence
and must never be committed or uploaded anywhere. Every external string goes
through this repository's existing no-leak sanitizer first.

**Commands, run at the LSEG-licensed workstation** (LSEG Workspace running
and logged in on the same machine, ``lseg-data`` installed for the Python
that runs this)::

    python tools/lseg_swaption_vol_probe.py --session-only

    python tools/lseg_swaption_vol_probe.py \\
        --ric "OPERATOR-SUPPLIED-RIC" \\
        --field OPERATOR_SUPPLIED_FIELD \\
        --expiry 1Y --tenor 5Y --strike ATM --quote-side MID

    python tools/lseg_swaption_vol_probe.py \\
        --ipa-param OPERATOR_SUPPLIED_PARAMETER=OPERATOR_SUPPLIED_VALUE \\
        --expiry-label 1Y --tenor-label 5Y

None of the placeholders above is real. See
``docs/35_market_data_reconnection_phase1_probe.md`` for the workstation
runbook, the discovery route for obtaining a RIC, and the failure-diagnosis
table.

**Deliberately not in this file.** No verdict, no candidate ranking, no
fuzzy label matching (an exact string match or a reported miss, never "the
closest column"). No surface construction, interpolation, calibration, vol
conversion, Black-76, auto-fill, persistence, or schema change. No *call*
into anything under ``shiori_pricing_lab.pricing`` or
``shiori_pricing_lab.data`` -- those modules are still *imported*
transitively by the reused helpers, which is a cost of reuse and not a
licence to call them.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import inspect
import json
import math
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# The bootstrap runs before the local tool imports below, not after them: the
# reused `bloomberg_input_sourcing_probe` (via `market_data_probe_evidence`)
# reaches `bloomberg_dapi_probe`, which imports `shiori_pricing_lab` at module
# level and has no bootstrap of its own. Same placement (and same reason) as
# ``tools/bloomberg_bond_yield_field_probe.py``. Note this does *not* import
# the LSEG client: that stays lazily discovered, so this file works on a
# workstation with no client installed at all.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from bloomberg_input_sourcing_probe import sanitize_external_text  # noqa: E402
from market_data_probe_evidence import (  # noqa: E402
    AppliedOverride,
    AuthorizationStatus,
    EvidenceClass,
    ReportItem,
    items_to_dict,
    sanitize_tree,
    utc_now,
)

PROBE_NAME = "lseg_swaption_vol_probe"
PROBE_VERSION = "PHASE1_V1"
PHASE = "MARKET_DATA_RECONNECTION_PHASE_1"

#: Candidate client module names, most-current first. These are *candidates*
#: this probe tries, in the order LSEG's own material suggests they were
#: renamed; whether any of them exists is reported, never assumed. The
#: canonical one upstream is ``lseg.data`` (import name ``lseg.data``).
CANDIDATE_CLIENT_MODULES: tuple[str, ...] = (
    "lseg.data",
    "refinitiv.data",
    "eikon",
)

#: Candidate module paths for a swaption volatility-surface capability, tried
#: only after a client module imported. A path that does not exist is
#: reported, not worked around.
CANDIDATE_SURFACE_MODULES: tuple[str, ...] = (
    "lseg.data.content.ipa.surfaces.swaption",
    "refinitiv.data.content.ipa.surfaces.swaption",
)

#: Candidate module paths that carry the IPA surface enums. Kept separate
#: from the surface module on purpose: the volatility-type enum a caller
#: needs is not necessarily the one the surface module re-exports, and this
#: probe reports which enum objects it actually found rather than choosing
#: one by name.
CANDIDATE_ENUM_MODULES: tuple[str, ...] = (
    "lseg.data.content.ipa.surfaces._enums",
    "refinitiv.data.content.ipa.surfaces._enums",
)

DEFAULT_OUTPUT_DIRNAME = "shiori_lseg_swaption_vol_probe_output"
JSON_FILENAME = "lseg_swaption_vol_probe.json"
MARKDOWN_FILENAME = "lseg_swaption_vol_probe.md"

#: Bounds on what a returned table may contribute to the report. A vol
#: surface can be large; the report keeps a bounded preview plus the labels,
#: which is what a human needs to name the coordinate -- not the whole grid.
MAX_PREVIEW_ROWS = 10
MAX_PREVIEW_COLUMNS = 40


@dataclass(frozen=True)
class ClientModule:
    """One candidate client module's import outcome."""

    module_name: str
    imported: bool
    version: str | None
    error: str | None


@dataclass(frozen=True)
class SessionOutcome:
    """What opening the desktop session produced."""

    attempted: bool
    entry_point: str | None
    open_state: str | None
    error_code: str | None
    error_message: str | None
    session_object: str | None


@dataclass(frozen=True)
class SurfaceCapability:
    """What the *installed* client says it offers for swaption vol surfaces."""

    client_module: str | None
    candidate_module_paths: tuple[str, ...]
    available_module: str | None
    definition_callable: str | None
    definition_signature: str | None
    enum_modules_found: tuple[str, ...]
    volatility_type_members: tuple[str, ...]
    discovery_callables: tuple[str, ...]
    notes: tuple[str, ...]


@dataclass(frozen=True)
class TargetCoordinate:
    """The coordinate the operator says he is asking for.

    Every member is ``None`` unless the operator stated it; nothing is
    defaulted to the phase's nominal target, and nothing is read off a
    returned number.
    """

    expiry: str | None = None
    tenor: str | None = None
    strike: str | None = None
    quote_side: str | None = None
    vol_type: str | None = None
    vol_unit: str | None = None
    data_as_of: str | None = None


@dataclass(frozen=True)
class TableSnapshot:
    """A bounded, sanitized view of one returned table."""

    source: str
    shape: tuple[int, int] | None
    columns: tuple[str, ...]
    row_labels: tuple[str, ...]
    preview: tuple[dict, ...]
    error_code: str | None = None
    error_message: str | None = None
    fetched_at: str = ""


@dataclass(frozen=True)
class LabelLookup:
    """An exact-string lookup of the declared coordinate in a returned table."""

    source: str
    expiry_label: str | None
    tenor_label: str | None
    matched: bool
    value: str | None
    available_labels: tuple[str, ...] = field(default_factory=tuple)
    #: Why the lookup did not match. Kept out of ``available_labels`` so that
    #: field means only one thing -- the labels the table really carries --
    #: and a report consumer keying on labels never receives prose.
    note: str | None = None


#: Substrings the client's own reported ``open_state`` uses when it is *not*
#: open. Only used to downgrade a claim, never to upgrade one: a state this
#: probe does not recognise stays "the opener returned without raising", which
#: is exactly what this run observed.
_CLOSED_STATE_MARKERS = (
    "closed",
    "disconnect",
    "expired",
    "not open",
    "no default session",
)


def _open_state_says_closed(open_state: str | None) -> bool:
    if not open_state:
        return False
    text = open_state.lower()
    return any(marker in text for marker in _CLOSED_STATE_MARKERS)


def _session_established(session: SessionOutcome) -> bool:
    """Whether this run established a session, as the client itself reports it.

    Three distinct ways this is false, and all three must be treated as
    false: no opener was found (``attempted`` is False), the opener raised, or
    the opener returned but the client's own ``open_state`` says the session is
    not open. Reporting "a session was established" for the third case would
    be the probe asserting something the client contradicts.
    """

    if not session.attempted or session.error_code or session.error_message:
        return False
    return not _open_state_says_closed(session.open_state)


def _is_finite_number(value) -> bool:
    """Whether a located cell carries a real magnitude.

    ``NaN`` is the normal way a vendor table says "this pillar is empty", and
    it is not a value: ``str(nan)`` is ``'nan'``, which would otherwise be
    reported as a matched pillar.
    """

    if value is None or isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        # Not a number at all (a date, a string) -- still a real cell content,
        # so it counts as populated. Only None and non-finite floats do not.
        return not isinstance(value, float)


def _returned_a_value(snapshot: TableSnapshot) -> bool:
    """Whether this read produced a vendor value worth calling evidence.

    Deliberately stricter than "the client did not raise". A vendor table with
    zero cells is the routine answer for a RIC/field pair that yields nothing,
    and a response this probe could not read is a probe limitation -- neither
    is a returned value, so neither may promote the phase's `value` or
    `authorization` items to ``VENDOR_RETURNED``.
    """

    if snapshot.error_code or snapshot.error_message:
        return False
    if snapshot.shape is None or snapshot.shape[0] <= 0 or snapshot.shape[1] <= 0:
        return False
    return not any("__preview_unavailable__" in row for row in snapshot.preview)


def discover_client_modules(candidate_names: tuple[str, ...]) -> tuple[ClientModule, ...]:
    """Import each candidate client module and report what is actually installed.

    Uses :mod:`importlib.util` to test availability first, so a missing
    module is reported as ``imported=False`` rather than raising -- the
    normal, expected outcome on a workstation that has only one of the three
    historical names, and on a machine with none.
    """

    outcomes: list[ClientModule] = []
    for module_name in candidate_names:
        try:
            if importlib.util.find_spec(module_name) is None:
                outcomes.append(
                    ClientModule(
                        module_name=module_name,
                        imported=False,
                        version=None,
                        error="module not found on this interpreter",
                    )
                )
                continue
        except (ImportError, ValueError, ModuleNotFoundError) as exc:
            outcomes.append(
                ClientModule(
                    module_name=module_name,
                    imported=False,
                    version=None,
                    error=sanitize_external_text(str(exc)),
                )
            )
            continue

        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 -- any import-time failure is the finding
            outcomes.append(
                ClientModule(
                    module_name=module_name,
                    imported=False,
                    version=None,
                    error=sanitize_external_text(f"{type(exc).__name__}: {exc}"),
                )
            )
            continue

        version = getattr(module, "__version__", None)
        if not isinstance(version, str):
            package_name = module_name.split(".")[0]
            try:
                version = importlib.metadata.version(package_name)
            except Exception:  # noqa: BLE001 -- version is informational only
                version = None

        outcomes.append(
            ClientModule(
                module_name=module_name,
                imported=True,
                version=version,
                error=None,
            )
        )
    return tuple(outcomes)


def _open_session_entry_point(client_module):
    """Find the installed client's own session-opening callable, or ``None``.

    Looks for the documented shape first (``open_session``) and then for a
    ``session`` namespace carrying one, and reports which was used. Nothing
    is called that was not found on the imported module.
    """

    opener = getattr(client_module, "open_session", None)
    if callable(opener):
        return "open_session", opener

    session_namespace = getattr(client_module, "session", None)
    opener = getattr(session_namespace, "open_session", None)
    if callable(opener):
        return "session.open_session", opener

    return None, None


def _session_state(client_module) -> str | None:
    """Best-effort read of the client's own default-session state."""

    try:
        session_namespace = getattr(client_module, "session", None)
        getter = getattr(session_namespace, "get_default", None)
        if not callable(getter):
            return None
        default = getter()
        if default is None:
            return "no default session"
        state = getattr(default, "open_state", None)
        return str(state) if state is not None else f"{type(default).__name__} (no open_state)"
    except Exception as exc:  # noqa: BLE001 -- state is evidence, not a control path
        return sanitize_external_text(f"unreadable: {type(exc).__name__}: {exc}")


def open_desktop_session(client_module_name: str | None) -> SessionOutcome:
    """Open the desktop session through the installed client's own entry point.

    Read-only by construction: the only call made is the session opener. No
    session config, app key, RIC or field is supplied here -- on a current
    install the desktop session is the shipped default, and this probe does
    not invent a credential or a config path to make it work.

    A failure is reported with the client's own error code and message where
    the exception exposes them (read via ``getattr``, never assumed), which
    is what makes the diagnosis table in ``docs/35`` actionable.
    """

    if client_module_name is None:
        return SessionOutcome(
            attempted=False,
            entry_point=None,
            open_state=None,
            error_code=None,
            error_message="no LSEG client module could be imported on this interpreter",
            session_object=None,
        )

    client_module = importlib.import_module(client_module_name)
    entry_point_name, opener = _open_session_entry_point(client_module)
    if opener is None:
        return SessionOutcome(
            attempted=False,
            entry_point=None,
            open_state=None,
            error_code=None,
            error_message=(
                f"{client_module_name} exposes no open_session-shaped entry point this probe "
                "recognises; nothing was called rather than guessing one"
            ),
            session_object=None,
        )

    try:
        opener()
    except Exception as exc:  # noqa: BLE001 -- every session failure is the finding
        return SessionOutcome(
            attempted=True,
            entry_point=entry_point_name,
            open_state=None,
            error_code=sanitize_external_text(_exception_code(exc)),
            error_message=sanitize_external_text(f"{type(exc).__name__}: {exc}"),
            session_object=None,
        )

    return SessionOutcome(
        attempted=True,
        entry_point=entry_point_name,
        open_state=sanitize_external_text(_session_state(client_module)),
        error_code=None,
        error_message=None,
        session_object=f"{client_module_name} (imported)",
    )


def _exception_code(exc: BaseException) -> str | None:
    """The client's own error code, when the exception object actually carries one."""

    for attribute in ("code", "error_code"):
        value = getattr(exc, attribute, None)
        if value is not None:
            return str(value)
    return None


def _safe_signature(callable_object) -> str | None:
    """``inspect.signature`` as a string, or ``None`` -- never a made-up shape."""

    try:
        return str(inspect.signature(callable_object))
    except (TypeError, ValueError) as exc:
        return f"<signature unavailable: {exc}>"


def _volatility_type_members(enum_module) -> tuple[str, ...]:
    """Every member of every volatility-named enum the installed package publishes.

    Reported as ``EnumClass.member -> value`` strings. The **class** name is
    what decides inclusion, not the member name: an enum called
    ``VolatilityType`` whose members are ``NORMAL`` / ``LOG_NORMAL`` is exactly
    the thing this phase needs, and filtering on member names would have
    dropped it and reported that the package publishes no such enum.

    Only members the package actually publishes appear here. When a
    volatility-named class yields nothing, it is reported in ``notes`` by the
    caller instead -- this function never puts probe-authored text into a list
    the report labels as vendor-returned evidence.
    """

    found: list[str] = []
    try:
        names = dir(enum_module)
    except Exception:  # noqa: BLE001
        return ()

    for name in names:
        if name.startswith("_"):
            continue
        if "volatil" not in name.lower():
            continue
        candidate = getattr(enum_module, name, None)
        if not inspect.isclass(candidate):
            continue
        try:
            members = list(candidate)
        except TypeError:
            # A volatility-named class that is not an iterable enum (a plain
            # class, or an alias). Nothing published to report.
            continue
        for member in members:
            member_name = getattr(member, "name", None)
            if not isinstance(member_name, str):
                continue
            found.append(f"{name}.{member_name} -> {getattr(member, 'value', None)!r}")

    return tuple(dict.fromkeys(found))


def _discovery_callables(client_module) -> tuple[str, ...]:
    """Names under the client's own ``discovery`` namespace, if it has one.

    Reported so the operator can see which symbol-search route the installed
    client really offers before using it -- this is how a RIC is *obtained*
    rather than guessed.
    """

    namespace = getattr(client_module, "discovery", None)
    if namespace is None:
        return ()
    names = [
        name
        for name in dir(namespace)
        if not name.startswith("_") and callable(getattr(namespace, name, None))
    ]
    return tuple(sorted(names))


def discover_surface_capability(client_module_name: str | None) -> SurfaceCapability:
    """Introspect the installed client for a swaption-surface capability.

    Read-only and side-effect-free apart from importing the candidate
    modules. Nothing is invoked: what is reported is what the installed
    package *exposes* -- module presence, the ``Definition`` callable's real
    signature, and the volatility-type enum members that actually exist. Any
    candidate path that is absent is listed as absent with the import error,
    never replaced by a guess.
    """

    if client_module_name is None:
        return SurfaceCapability(
            client_module=None,
            candidate_module_paths=CANDIDATE_SURFACE_MODULES,
            available_module=None,
            definition_callable=None,
            definition_signature=None,
            enum_modules_found=(),
            volatility_type_members=(),
            discovery_callables=(),
            notes=("no client module imported, so no capability was introspected",),
        )

    client_module = importlib.import_module(client_module_name)
    notes: list[str] = []

    available_module: str | None = None
    definition_name: str | None = None
    definition_signature: str | None = None

    for module_path in CANDIDATE_SURFACE_MODULES:
        try:
            if importlib.util.find_spec(module_path) is None:
                notes.append(f"{module_path}: module not found")
                continue
            module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            # A dotted candidate whose parent package is also absent. Reported
            # as absent rather than as an error: on a workstation without the
            # capability this is the ordinary, expected outcome.
            notes.append(f"{module_path}: module not found")
            continue
        except Exception as exc:  # noqa: BLE001
            notes.append(sanitize_external_text(f"{module_path}: {type(exc).__name__}: {exc}"))
            continue

        available_module = module_path
        for attribute_name in ("Definition", "definition"):
            candidate = getattr(module, attribute_name, None)
            if callable(candidate):
                definition_name = f"{module_path}.{attribute_name}"
                definition_signature = _safe_signature(candidate)
                break
        if definition_name is None:
            notes.append(
                f"{module_path}: imported but exposes no callable Definition; "
                "nothing was called rather than guessing the entry point"
            )
        break

    enum_modules_found: list[str] = []
    volatility_type_members: list[str] = []
    for module_path in CANDIDATE_ENUM_MODULES:
        try:
            if importlib.util.find_spec(module_path) is None:
                continue
            enum_module = importlib.import_module(module_path)
        except ModuleNotFoundError:
            continue
        except Exception as exc:  # noqa: BLE001
            notes.append(sanitize_external_text(f"{module_path}: {type(exc).__name__}: {exc}"))
            continue
        enum_modules_found.append(module_path)
        members = _volatility_type_members(enum_module)
        volatility_type_members.extend(members)
        if not members:
            # Reported as a probe note, not as a vendor-returned member: the
            # package published nothing here, and probe-authored text must not
            # appear in an item the report labels VENDOR_RETURNED.
            volatility_named = sorted(
                name
                for name in dir(enum_module)
                if not name.startswith("_")
                and "volatil" in name.lower()
                and inspect.isclass(getattr(enum_module, name, None))
            )
            if volatility_named:
                notes.append(
                    f"{module_path}: volatility-named classes {volatility_named} expose no "
                    "enumerable members to this probe"
                )

    return SurfaceCapability(
        client_module=client_module_name,
        candidate_module_paths=CANDIDATE_SURFACE_MODULES,
        available_module=available_module,
        definition_callable=definition_name,
        definition_signature=definition_signature,
        enum_modules_found=tuple(enum_modules_found),
        volatility_type_members=tuple(dict.fromkeys(volatility_type_members)),
        discovery_callables=_discovery_callables(client_module),
        notes=tuple(notes),
    )


def _frame_columns(frame) -> tuple[str, ...]:
    try:
        return tuple(str(column) for column in list(frame.columns)[:MAX_PREVIEW_COLUMNS])
    except Exception:  # noqa: BLE001
        return ()


def _frame_row_labels(frame) -> tuple[str, ...]:
    try:
        return tuple(str(label) for label in list(frame.index)[:MAX_PREVIEW_ROWS])
    except Exception:  # noqa: BLE001
        return ()


def _frame_preview(frame) -> tuple[dict, ...]:
    """A bounded, stringified preview -- never the whole surface, never coerced."""

    try:
        head = frame.head(MAX_PREVIEW_ROWS)
        trimmed = head.iloc[:, :MAX_PREVIEW_COLUMNS]
        records: list[dict] = []
        for label, row in trimmed.iterrows():
            record = {"__index__": str(label)}
            for column, value in row.items():
                record[str(column)] = None if value is None else str(value)
            records.append(record)
        return tuple(records)
    except Exception as exc:  # noqa: BLE001
        return ({"__preview_unavailable__": f"{type(exc).__name__}: {exc}"},)


def _read_table(
    client_module,
    *,
    universe,
    fields,
    expiry_label: str | None = None,
    tenor_label: str | None = None,
) -> tuple[TableSnapshot, LabelLookup | None]:
    """One ``get_data``-shaped read, reporting the client's own error verbatim.

    ``get_data(universe=..., fields=...)`` is the shape this probe *attempts*,
    not a shape it verified: the installed client's real signature is the
    thing being learned, and a client that spells these differently raises --
    which is recorded below as a finding rather than papered over. Nothing
    here inspects the signature first, because that would require deciding in
    advance which keyword names count as "the same" call.

    Returns the bounded snapshot and, when the operator named a coordinate,
    the exact-label lookup performed against *this same* returned frame. The
    lookup is deliberately not a second request: asking the vendor twice for
    one report would risk the two answers disagreeing while the report shows
    a single ``fetched_at``.
    """

    reader = getattr(client_module, "get_data", None)
    fetched_at = utc_now()
    if not callable(reader):
        return (
            TableSnapshot(
                source="get_data",
                shape=None,
                columns=(),
                row_labels=(),
                preview=(),
                error_message=(
                    "the installed client exposes no callable get_data, so no snapshot could be "
                    "requested; nothing was substituted"
                ),
                fetched_at=fetched_at,
            ),
            None,
        )

    try:
        frame = reader(universe=list(universe), fields=list(fields))
    except Exception as exc:  # noqa: BLE001 -- the client's own failure is the finding
        return (
            TableSnapshot(
                source="get_data",
                shape=None,
                columns=(),
                row_labels=(),
                preview=(),
                error_code=sanitize_external_text(_exception_code(exc)),
                error_message=sanitize_external_text(f"{type(exc).__name__}: {exc}"),
                fetched_at=fetched_at,
            ),
            None,
        )

    shape = None
    try:
        shape = (len(frame.index), len(frame.columns))
    except Exception:  # noqa: BLE001
        shape = None

    snapshot = TableSnapshot(
        source="get_data",
        shape=shape,
        columns=_frame_columns(frame),
        row_labels=_frame_row_labels(frame),
        preview=_frame_preview(frame),
        fetched_at=fetched_at,
    )
    lookup = (
        _lookup_label(frame, expiry_label, tenor_label)
        if expiry_label and tenor_label
        else None
    )
    return snapshot, lookup


def _lookup_label(frame, expiry_label: str | None, tenor_label: str | None) -> LabelLookup:
    """Exact-string location of the declared coordinate in a returned table.

    Exact match only. A near-miss is a ``NOT_FOUND`` that lists the labels
    the table actually carries, so the operator can name the coordinate
    correctly -- this probe never presents the closest-looking column as the
    requested one, which is the same rule the VCUB template parser already
    applies to screenshot cells.
    """

    if not expiry_label or not tenor_label:
        return LabelLookup(
            source="get_data",
            expiry_label=expiry_label,
            tenor_label=tenor_label,
            matched=False,
            value=None,
            available_labels=(),
        )

    available: list[str] = []
    try:
        index_labels = [str(label) for label in frame.index]
        column_labels = [str(column) for column in frame.columns]
        available = sorted(set(index_labels) | set(column_labels))

        # A duplicate label cannot be located unambiguously: two columns both
        # named "5Y" give a Series, not a pillar. Reported rather than
        # stringified into a "matched" value.
        duplicate_index = len(index_labels) != len(set(index_labels))
        duplicate_columns = len(column_labels) != len(set(column_labels))
        if duplicate_index or duplicate_columns:
            return LabelLookup(
                source="get_data",
                expiry_label=expiry_label,
                tenor_label=tenor_label,
                matched=False,
                value=None,
                available_labels=tuple(available[: MAX_PREVIEW_COLUMNS * 2]),
                note=(
                    "DUPLICATE LABELS: this table repeats "
                    f"{'index' if duplicate_index else 'column'} labels, so no single pillar can "
                    "be identified from a name alone"
                ),
            )

        index_positions = {label: position for position, label in enumerate(index_labels)}
        column_positions = {label: position for position, label in enumerate(column_labels)}

        # Orientation is not assumed: the declared pair may be (row, column) or
        # (column, row), and both are tried. Neither is preferred -- if both
        # resolve, the value must agree or the lookup is ambiguous.
        candidates: list[object] = []
        if expiry_label in index_positions and tenor_label in column_positions:
            candidates.append(
                frame.iat[index_positions[expiry_label], column_positions[tenor_label]]
            )
        if tenor_label in index_positions and expiry_label in column_positions:
            candidates.append(
                frame.iat[index_positions[tenor_label], column_positions[expiry_label]]
            )
        if not candidates:
            return LabelLookup(
                source="get_data",
                expiry_label=expiry_label,
                tenor_label=tenor_label,
                matched=False,
                value=None,
                available_labels=tuple(available[: MAX_PREVIEW_COLUMNS * 2]),
                note=(
                    "NOT_FOUND: no label in this table exactly matches the declared coordinate; "
                    "the labels it does carry are listed. No nearest-looking label was used."
                    + (f" (labels truncated to {MAX_PREVIEW_COLUMNS * 2})"
                       if len(available) > MAX_PREVIEW_COLUMNS * 2 else "")
                ),
            )

        # A pillar the vendor left empty (NaN/None) carries no value, so a
        # lookup that found the right cell must not report "nan" as the answer.
        unpopulated = [candidate for candidate in candidates if not _is_finite_number(candidate)]
        if unpopulated:
            return LabelLookup(
                source="get_data",
                expiry_label=expiry_label,
                tenor_label=tenor_label,
                matched=False,
                value=None,
                available_labels=(),
                note=(
                    "FOUND BUT EMPTY: the declared coordinate resolves to a cell this table "
                    "leaves unpopulated (NaN/None), so it carries no observed value"
                ),
            )

        rendered = {str(candidate) for candidate in candidates}
        if len(rendered) > 1:
            return LabelLookup(
                source="get_data",
                expiry_label=expiry_label,
                tenor_label=tenor_label,
                matched=False,
                value=None,
                available_labels=(),
                note=(
                    "AMBIGUOUS: both orientations resolved and disagree: "
                    f"{sorted(rendered)}; neither is presented as the answer"
                ),
            )
        return LabelLookup(
            source="get_data",
            expiry_label=expiry_label,
            tenor_label=tenor_label,
            matched=True,
            value=next(iter(rendered)),
            available_labels=(),
        )
    except Exception as exc:  # noqa: BLE001
        return LabelLookup(
            source="get_data",
            expiry_label=expiry_label,
            tenor_label=tenor_label,
            matched=False,
            value=None,
            available_labels=(),
            note=sanitize_external_text(
                f"the lookup failed internally ({type(exc).__name__}: {exc}); this is a probe "
                "failure, not a NOT_FOUND from the vendor"
            ),
        )


def run_surface_probe(
    capability: SurfaceCapability,
    parameters: tuple[tuple[str, str], ...],
    *,
    expiry_label: str | None = None,
    tenor_label: str | None = None,
) -> tuple[TableSnapshot, LabelLookup | None] | None:
    """Invoke the discovered surface entry point with operator-supplied parameters only.

    Returns ``None`` when there is nothing legitimate to call -- no discovered
    ``Definition``, or no operator-supplied parameter. Every parameter passed
    comes from the command line, verbatim; the callable and its keyword names
    are the ones the introspection above actually found. A call that fails is
    reported with the client's own error, which is how the real
    parameterisation gets learned.

    When the operator named a coordinate, the exact-label lookup runs against
    *this same* returned table -- never a second vendor call, so the report's
    single ``fetched_at`` describes both.
    """

    if capability.definition_callable is None or not parameters:
        return None

    module_path, _, attribute_name = capability.definition_callable.rpartition(".")
    definition = getattr(importlib.import_module(module_path), attribute_name, None)
    if not callable(definition):
        return None

    fetched_at = utc_now()
    # The exact call this run sent, name by name -- not a placeholder. The
    # values are sanitized below like every other field, so an operator-supplied
    # value that the guard knows about is redacted while the parameter *names*
    # survive as the provenance record.
    source = (
        f"{capability.definition_callable}("
        + ", ".join(f"{name}={value}" for name, value in parameters)
        + ")"
    )
    kwargs = dict(parameters)
    try:
        result = definition(**kwargs)
    except Exception as exc:  # noqa: BLE001 -- the real parameterisation is learned here
        return (
            TableSnapshot(
                source=source,
                shape=None,
                columns=(),
                row_labels=(),
                preview=(),
                error_code=sanitize_external_text(_exception_code(exc)),
                error_message=sanitize_external_text(f"{type(exc).__name__}: {exc}"),
                fetched_at=fetched_at,
            ),
            None,
        )

    frame = _extract_frame(result)
    if frame is None:
        rendered_repr = sanitize_external_text(repr(result))
        return (
            TableSnapshot(
                source=source,
                shape=None,
                columns=(),
                row_labels=(),
                preview=(),
                error_message=(
                    "the surface definition returned an object this probe cannot read as a "
                    "table; its repr is reported instead of guessing an accessor: "
                    + rendered_repr[:500]
                    + (f" [truncated at 500 of {len(rendered_repr)} characters]" 
                       if len(rendered_repr) > 500 else "")
                ),
                fetched_at=fetched_at,
            ),
            None,
        )

    shape = None
    try:
        shape = (len(frame.index), len(frame.columns))
    except Exception:  # noqa: BLE001
        shape = None
    snapshot = TableSnapshot(
        source=source,
        shape=shape,
        columns=_frame_columns(frame),
        row_labels=_frame_row_labels(frame),
        preview=_frame_preview(frame),
        fetched_at=fetched_at,
    )
    lookup = (
        _lookup_label(frame, expiry_label, tenor_label)
        if expiry_label and tenor_label
        else None
    )
    if lookup is not None:
        lookup = LabelLookup(
            source=source,
            expiry_label=lookup.expiry_label,
            tenor_label=lookup.tenor_label,
            matched=lookup.matched,
            value=lookup.value,
            available_labels=lookup.available_labels,
        )
    return snapshot, lookup


def _extract_frame(result):
    """The documented ``.data.df`` shape, or a plain frame -- tried, not assumed."""

    for candidate in (
        getattr(getattr(result, "data", None), "df", None),
        getattr(result, "df", None),
        result if hasattr(result, "columns") and hasattr(result, "index") else None,
    ):
        if candidate is not None:
            return candidate
    return None


def build_report_items(
    session: SessionOutcome,
    capability: SurfaceCapability,
    coordinate: TargetCoordinate,
    snapshots: tuple[TableSnapshot, ...],
    *,
    attempted_value_probe: bool,
    fetched_at: str,
) -> tuple[ReportItem, ...]:
    """The phase's report items for the LSEG route, with the evidence behind each."""

    session_established = _session_established(session)
    # A read counts as vendor evidence only if it actually carries cells --
    # see `_returned_a_value`. An empty table and an unreadable response are
    # both non-answers, and neither may promote an item to VENDOR_RETURNED.
    successful_reads = tuple(
        snapshot for snapshot in snapshots if _returned_a_value(snapshot)
    )
    if successful_reads:
        authorization_state = AuthorizationStatus.FIELD_LEVEL_OBSERVED
    elif session_established:
        authorization_state = AuthorizationStatus.SERVICE_LEVEL_ONLY
    else:
        authorization_state = AuthorizationStatus.NOT_ESTABLISHED

    if successful_reads and not session_established:
        authorization_detail = (
            f"status={authorization_state.value}. A read is reported below, but this run "
            "established no session, so the probe cannot say what authorised it; treat the "
            "read as unattributed rather than as field-level entitlement."
        )
    elif session_established:
        authorization_detail = (
            f"status={authorization_state.value}. A returned field is the only per-field "
            "entitlement evidence; a refused field is reported with the client's own error "
            "code and message. No blanket account entitlement is claimed."
        )
    else:
        authorization_detail = (
            f"status={authorization_state.value}. No session was established, so no "
            "entitlement evidence exists for this run."
        )

    items: list[ReportItem] = [
        ReportItem(
            item="connection",
            evidence_class=(
                EvidenceClass.PROBE_OBSERVED
                if session_established
                else EvidenceClass.NOT_ESTABLISHED
            ),
            detail=(
                f"the installed client's {session.entry_point} was called and did not raise; "
                f"reported session state: {session.open_state!r}. This is this run's own "
                "observation of the client, not proof the local Workspace session is entitled "
                "to any particular field."
                if session_established
                else (
                    "no desktop session was established: "
                    f"{session.error_message or 'not attempted'}"
                    + (f" (code {session.error_code})" if session.error_code else "")
                )
            ),
        ),
        ReportItem(
            item="authorization",
            # Both halves are required: a value the vendor returned *and* a
            # session this run actually opened to return it through.
            evidence_class=(
                EvidenceClass.VENDOR_RETURNED
                if successful_reads and session_established
                else EvidenceClass.NOT_ESTABLISHED
            ),
            detail=authorization_detail,
        ),
        ReportItem(
            item="value",
            evidence_class=(
                EvidenceClass.VENDOR_RETURNED if successful_reads else EvidenceClass.NOT_ESTABLISHED
            ),
            detail=(
                f"{len(successful_reads)} read(s) returned a non-empty table; each snapshot in "
                "table_snapshots carries a bounded preview, its shape and its labels."
                if successful_reads
                else (
                    "no read returned a usable table: either nothing was returned, the returned "
                    "table had no cells, or the response could not be read as a table. See "
                    "table_snapshots for which, with the client's own error where there was one."
                    if snapshots
                    else (
                        "no read was attempted."
                        if attempted_value_probe
                        else "no candidate was supplied, so no value was requested."
                    )
                )
            ),
        ),
    ]

    for item, declared in (
        ("vol_type", coordinate.vol_type),
        ("vol_unit", coordinate.vol_unit),
        ("expiry", coordinate.expiry),
        ("tenor", coordinate.tenor),
        ("quote_side", coordinate.quote_side),
        ("data_date", coordinate.data_as_of),
    ):
        if declared is None:
            items.append(
                ReportItem(
                    item=item,
                    evidence_class=EvidenceClass.NOT_ESTABLISHED,
                    detail=(
                        f"the operator did not state {item}; this probe never defaults it. A "
                        "returned number cannot establish it either -- it is a claim about the "
                        "field's meaning, not its magnitude."
                    ),
                )
            )
        else:
            items.append(
                ReportItem(
                    item=item,
                    evidence_class=EvidenceClass.OPERATOR_DECLARED,
                    detail=(
                        f"the operator stated {item}={declared!r}. This is his assertion, not "
                        "vendor evidence. Inspect surface_capability's enum members and "
                        "signature for what the installed client publishes about its own "
                        "volatility-type space."
                    ),
                )
            )

    items.append(
        ReportItem(
            item="fetched_at",
            evidence_class=EvidenceClass.PROBE_OBSERVED,
            detail=(
                f"{fetched_at} -- this run's own UTC clock. It is when the probe ran, never the "
                "market data's own date."
            ),
        )
    )

    if capability.volatility_type_members:
        items.append(
            ReportItem(
                item="client_published_volatility_types",
                evidence_class=EvidenceClass.VENDOR_RETURNED,
                detail=(
                    "the installed client's own enums publish these volatility-type members: "
                    f"{list(capability.volatility_type_members)}. Evidence about the client's "
                    "vocabulary -- not an assertion that any of them applies to a returned "
                    "number."
                ),
            )
        )

    return tuple(items)


def build_report(
    clients: tuple[ClientModule, ...],
    session: SessionOutcome,
    capability: SurfaceCapability,
    coordinate: TargetCoordinate,
    snapshots: tuple[TableSnapshot, ...],
    lookups: tuple[LabelLookup, ...],
    *,
    attempted_value_probe: bool,
    generated_at: str,
    applied_overrides: tuple[AppliedOverride, ...] = (),
) -> dict:
    """Assemble the whole run into one sanitized, JSON-serializable record."""

    applied = applied_overrides
    fetched_at = snapshots[-1].fetched_at if snapshots else generated_at
    items = build_report_items(
        session,
        capability,
        coordinate,
        snapshots,
        attempted_value_probe=attempted_value_probe,
        fetched_at=fetched_at,
    )

    payload = {
        "probe": PROBE_NAME,
        "probe_version": PROBE_VERSION,
        "phase": PHASE,
        "generated_at": generated_at,
        "read_only": True,
        "production_paths_touched": [],
        "client_modules": [sanitize_tree(asdict(item)) for item in clients],
        "session": sanitize_tree(asdict(session)),
        "surface_capability": sanitize_tree(asdict(capability)),
        "target_coordinate_declared_by_operator": sanitize_tree(asdict(coordinate)),
        "table_snapshots": [sanitize_tree(asdict(item)) for item in snapshots],
        "label_lookups": [sanitize_tree(asdict(item)) for item in lookups],
        "required_report_items": items_to_dict(items),
        "verdict": None,
        "verdict_assigned": False,
        "verdict_note": (
            "This probe assigns no verdict, and encodes that the same way the sibling tool "
            "does: verdict is null. It reports what the installed client exposes and what any "
            "operator-supplied read returned. Whether that constitutes a usable route to a USD "
            "swaption normal vol is Eddy's judgment on this report. A returned value is evidence "
            "of a value only -- the report deliberately does not label it a normal vol in bp, "
            "because that is a claim about the field's meaning that no returned number can "
            "settle (Annex A.8.1)."
        ),
    }

    # One last pass over the *whole* payload, with this run's operator inputs
    # registered as overrides first: `required_report_items` and the capability
    # block carry vendor-published text and the declared coordinate carries
    # operator input, none of which the per-block guards above cover.
    return sanitize_tree(payload, applied)


def render_markdown(data: dict) -> str:
    lines = [
        f"# {data['probe']} ({data['phase']})",
        "",
        f"- generated_at: {data['generated_at']}",
        f"- read_only: {data['read_only']}",
        f"- verdict: {json.dumps(data['verdict'])} (verdict_assigned: "
        f"{json.dumps(data['verdict_assigned'])}) -- this probe assigns no verdict",
        "",
        "## Client modules on this interpreter",
        "",
        "| module | imported | version | error |",
        "| --- | --- | --- | --- |",
    ]
    for client in data["client_modules"]:
        lines.append(
            f"| {client['module_name']} | {client['imported']} | {client['version'] or ''} | "
            f"{client['error'] or ''} |"
        )

    session = data["session"]
    lines += [
        "",
        "## Desktop session",
        "",
        f"- attempted: {session['attempted']}",
        f"- entry_point: {session['entry_point']}",
        f"- open_state: {session['open_state']}",
        f"- error_code: {session['error_code']}",
        f"- error_message: {session['error_message']}",
        "",
        "## Installed client's swaption-surface capability",
        "",
        f"- available_module: {data['surface_capability']['available_module']}",
        f"- definition_callable: {data['surface_capability']['definition_callable']}",
        f"- definition_signature: {data['surface_capability']['definition_signature']}",
        f"- enum_modules_found: {data['surface_capability']['enum_modules_found']}",
        f"- volatility_type_members: {data['surface_capability']['volatility_type_members']}",
        f"- discovery_callables: {data['surface_capability']['discovery_callables']}",
        "",
        "## Report items and the evidence behind each",
        "",
        "| item | evidence_class | detail |",
        "| --- | --- | --- |",
    ]
    for item in data["required_report_items"]:
        lines.append(f"| {item['item']} | {item['evidence_class']} | {item['detail']} |")

    lines += ["", "## Table snapshots", ""]
    if data["table_snapshots"]:
        for snapshot in data["table_snapshots"]:
            lines += [
                f"### {snapshot['source']}",
                "",
                f"- shape: {snapshot['shape']}",
                f"- columns: {snapshot['columns']}",
                f"- row_labels: {snapshot['row_labels']}",
                f"- error_code: {snapshot['error_code']}",
                f"- error_message: {snapshot['error_message']}",
                f"- fetched_at: {snapshot['fetched_at']}",
                "",
                "```json",
                json.dumps(snapshot["preview"], indent=2, ensure_ascii=False),
                "```",
                "",
            ]
    else:
        lines.append("- no table was returned")

    lines += ["", "## Exact label lookups", ""]
    if data["label_lookups"]:
        for lookup in data["label_lookups"]:
            lines.append(
                f"- {lookup['source']}: expiry={lookup['expiry_label']!r} "
                f"tenor={lookup['tenor_label']!r} matched={lookup['matched']} "
                f"value={lookup['value']} available={lookup['available_labels']} "
                f"note={lookup['note']}"
            )
    else:
        lines.append("- no label lookup was requested")

    lines += ["", "## Verdict note", "", data["verdict_note"], ""]
    return "\n".join(lines)


def render_json(data: dict) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False)


def write_report(data: dict, output_dir: Path) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / JSON_FILENAME
    markdown_path = output_dir / MARKDOWN_FILENAME
    json_path.write_text(render_json(data), encoding="utf-8")
    markdown_path.write_text(render_markdown(data), encoding="utf-8")
    return json_path, markdown_path


def _parse_parameter(raw: str) -> tuple[str, str]:
    if "=" not in raw:
        raise argparse.ArgumentTypeError(
            f"parameter {raw!r} must be NAME=VALUE -- neither a parameter name nor its value is "
            "guessed here; read surface_capability's definition_signature for the names"
        )
    name, value = raw.split("=", 1)
    if not name.strip():
        raise argparse.ArgumentTypeError(f"parameter {raw!r} has an empty name")
    return name.strip(), value


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read-only LSEG Workspace desktop-session probe for a USD swaption normal vol route "
            "(Market Data Reconnection Phase 1). Invents no RIC, field or surface parameter."
        )
    )
    parser.add_argument(
        "--session-only",
        action="store_true",
        help="Report client/session/capability evidence only; request no data",
    )
    parser.add_argument(
        "--client-module",
        default=None,
        help="Force one client module name (default: try each of "
        f"{list(CANDIDATE_CLIENT_MODULES)} in order)",
    )
    parser.add_argument(
        "--ric",
        action="append",
        default=None,
        help="Operator-supplied RIC (repeatable, REQUIRED for a get_data snapshot). Obtain it "
        "via the installed client's own discovery namespace or Workspace search; this probe "
        "has no default and will not guess one.",
    )
    parser.add_argument(
        "--field",
        action="append",
        default=None,
        help="Operator-supplied field mnemonic (repeatable, REQUIRED for a get_data snapshot). "
        "Read it from Workspace's Data Item Browser; this probe has no default.",
    )
    parser.add_argument(
        "--ipa-param",
        action="append",
        default=None,
        type=_parse_parameter,
        metavar="NAME=VALUE",
        help="Keyword parameter for the discovered swaption-surface definition (repeatable). "
        "Read the real names from the report's definition_signature -- none is guessed.",
    )
    parser.add_argument(
        "--expiry-label",
        default=None,
        help="Exact expiry label to locate in a returned table (exact match only)",
    )
    parser.add_argument(
        "--tenor-label",
        default=None,
        help="Exact tenor label to locate in a returned table (exact match only)",
    )
    parser.add_argument("--expiry", default=None, help="Declared expiry label, e.g. 1Y (operator)")
    parser.add_argument("--tenor", default=None, help="Declared swap tenor, e.g. 5Y (operator)")
    parser.add_argument("--strike", default=None, help="Declared strike, e.g. ATM (operator)")
    parser.add_argument(
        "--quote-side",
        default=None,
        choices=("BID", "MID", "OFFER", "ASK"),
        help="Declared quote side (operator)",
    )
    parser.add_argument(
        "--vol-type",
        default=None,
        help="Declared volatility space, e.g. Normal (operator assertion, never presented as "
        "vendor evidence)",
    )
    parser.add_argument(
        "--vol-unit",
        default=None,
        help="Declared volatility unit, e.g. bp (operator assertion, never presented as vendor "
        "evidence)",
    )
    parser.add_argument(
        "--data-as-of",
        default=None,
        help="Declared market data date/time (operator assertion; no generic as-of field is "
        "assumed to exist)",
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIRNAME,
        help=f"Local report directory (default: {DEFAULT_OUTPUT_DIRNAME})",
    )
    return parser.parse_args(argv)


def _reject_duplicate_parameter_names(parameters: tuple[tuple[str, str], ...]) -> None:
    """Refuse a repeated ``--ipa-param`` name rather than silently keeping the last.

    ``dict(parameters)`` would drop the earlier value without a word, and the
    report's provenance line would then show a call this run never made.
    """

    seen: list[str] = []
    for name, _value in parameters:
        if name in seen:
            raise SystemExit(
                f"error: --ipa-param {name} was supplied more than once; this probe will not "
                "guess which value you meant"
            )
        seen.append(name)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])

    rics = tuple(args.ric) if args.ric else ()
    fields = tuple(args.field) if args.field else ()
    ipa_parameters = tuple(args.ipa_param) if args.ipa_param else ()

    _reject_duplicate_parameter_names(ipa_parameters)

    # The operator-supplied surface parameters are registered as overrides, so
    # the no-leak guard scrubs them if the client echoes them back: an
    # `--ipa-param` value is operator input that can hold a proxy or app
    # credential, and without this it would be scrubbed only when it happened to
    # match the guard's credential-shaped pattern.
    #
    # Deliberately *not* `--ric`/`--field`: a RIC is a market identifier the
    # operator typed on his own command line, not a secret, and registering a
    # short token like `1Y` as an override value would redact that string
    # everywhere it legitimately appears in the report.
    applied_overrides = tuple(
        AppliedOverride(field=name, value=value, source="user_supplied")
        for name, value in ipa_parameters
    )

    coordinate = TargetCoordinate(
        expiry=args.expiry,
        tenor=args.tenor,
        strike=args.strike,
        quote_side=args.quote_side,
        vol_type=args.vol_type,
        vol_unit=args.vol_unit,
        data_as_of=args.data_as_of,
    )

    generated_at = utc_now()
    print(f"{PROBE_NAME} {PROBE_VERSION} ({PHASE})")

    clients = discover_client_modules(
        (args.client_module,) if args.client_module else CANDIDATE_CLIENT_MODULES
    )
    for client in clients:
        state = f"imported {client.version or ''}".strip() if client.imported else "absent"
        print(f"  {client.module_name}: {state}"
              + (f" ({client.error})" if client.error else ""))

    active_client = next((client.module_name for client in clients if client.imported), None)
    if active_client is None:
        print(
            "error: no LSEG client module is installed for this interpreter -- install "
            "`lseg-data` for the Python that runs this probe (see docs/35)",
            file=sys.stderr,
        )
        return 2
    print(f"  using: {active_client}")

    session = open_desktop_session(active_client)
    print(f"  session: attempted={session.attempted} entry_point={session.entry_point} "
          f"state={session.open_state}")
    if session.error_message:
        print(f"  session error: {session.error_code or ''} {session.error_message}",
              file=sys.stderr)

    capability = discover_surface_capability(active_client)
    print(f"  surface module: {capability.available_module}")
    print(f"  definition: {capability.definition_callable} {capability.definition_signature}")
    if capability.volatility_type_members:
        print(f"  client volatility types: {list(capability.volatility_type_members)}")

    snapshots: list[TableSnapshot] = []
    lookups: list[LabelLookup] = []

    if args.session_only:
        print("--session-only: requesting no data")
    else:
        client_module = importlib.import_module(active_client)
        if rics and fields:
            print(f"get_data: {len(rics)} RIC(s) x {len(fields)} field(s)")
            snapshot, lookup = _read_table(
                client_module,
                universe=rics,
                fields=fields,
                expiry_label=args.expiry_label,
                tenor_label=args.tenor_label,
            )
            snapshots.append(snapshot)
            if lookup is not None:
                lookups.append(lookup)
                print(f"  label lookup: {lookup}")
            if snapshot.error_message:
                print(f"  error: {snapshot.error_code or ''} {snapshot.error_message}",
                      file=sys.stderr)
            else:
                print(f"  shape={snapshot.shape} columns={snapshot.columns}")
        else:
            print("no --ric/--field supplied, so no snapshot was requested; "
                  "this probe never invents a RIC or a field")

        if ipa_parameters:
            print(f"surface definition: {capability.definition_callable} "
                  f"with {len(ipa_parameters)} operator-supplied parameter(s)")
            surface = run_surface_probe(
                capability,
                ipa_parameters,
                expiry_label=args.expiry_label,
                tenor_label=args.tenor_label,
            )
            if surface is None:
                print(
                    "  not attempted: no discovered definition, or no operator-supplied "
                    "parameter -- nothing was guessed",
                    file=sys.stderr,
                )
            else:
                surface_snapshot, surface_lookup = surface
                snapshots.append(surface_snapshot)
                if surface_lookup is not None:
                    lookups.append(surface_lookup)
                    print(f"  label lookup: {surface_lookup}")
                if surface_snapshot.error_message:
                    print(
                        f"  error: {surface_snapshot.error_code or ''} "
                        f"{surface_snapshot.error_message}",
                        file=sys.stderr,
                    )
                else:
                    print(
                        f"  shape={surface_snapshot.shape} "
                        f"columns={surface_snapshot.columns}"
                    )
        elif not ipa_parameters:
            print("no --ipa-param supplied, so the surface definition was not called; "
                  "its real parameter names are in the report's definition_signature")

    data = build_report(
        clients,
        session,
        capability,
        coordinate,
        tuple(snapshots),
        tuple(lookups),
        attempted_value_probe=bool(rics and fields) or bool(ipa_parameters),
        generated_at=generated_at,
        applied_overrides=applied_overrides,
    )
    json_path, markdown_path = write_report(data, Path(args.output_dir))

    print("")
    print(f"verdict: {json.dumps(data['verdict'])} (this probe assigns none -- see the report)")
    print(f"wrote {json_path}")
    print(f"wrote {markdown_path}")
    print(
        "note: these reports can contain licensed market values and operator inputs -- keep "
        "them on the approved workstation; the output directory is git-ignored"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
