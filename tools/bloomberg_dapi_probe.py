"""Standalone Bloomberg Desktop API (DAPI) connectivity probe.

Power-on test only (Issue #6): proves that `blpapi` can start a Desktop API
session against a local, logged-in Bloomberg Terminal and retrieve one
ordinary reference-data field. This is not the production Bloomberg
adapter and is intentionally kept out of `src/shiori_pricing_lab`.

Run on the Bloomberg workstation:
    python tools/bloomberg_dapi_probe.py
"""

from __future__ import annotations

import sys

SECURITY = "91282CQX Govt"
FIELD = "PX_LAST"
HOST = "localhost"
PORT = 8194
REQUEST_TIMEOUT_MS = 10_000

try:
    import blpapi
except ImportError as exc:
    print(f"IMPORT_FAILED: could not import blpapi: {exc}")
    sys.exit(1)


def main() -> int:
    # Desktop API mode: connect to the local Terminal on 127.0.0.1:8194
    # with no authentication options. Setting auth options would switch
    # this into Server API / B-PIPE mode, which is out of scope here.
    session_options = blpapi.SessionOptions()
    session_options.setServerHost(HOST)
    session_options.setServerPort(PORT)

    session = blpapi.Session(session_options)

    if not session.start():
        print("CONNECTED: FAILED")
        return 1
    print("CONNECTED: OK")

    try:
        if not session.openService("//blp/refdata"):
            print("SERVICE_OPENED: FAILED")
            return 1
        print("SERVICE_OPENED: OK")

        service = session.getService("//blp/refdata")
        request = service.createRequest("ReferenceDataRequest")
        request.append("securities", SECURITY)
        request.append("fields", FIELD)

        session.sendRequest(request)

        exit_code = 0
        done = False
        while not done:
            event = session.nextEvent(REQUEST_TIMEOUT_MS)

            if event.eventType() not in (
                blpapi.Event.PARTIAL_RESPONSE,
                blpapi.Event.RESPONSE,
            ):
                continue

            for msg in event:
                if msg.hasElement("responseError"):
                    print(f"REQUEST_FAILED: {msg.getElement('responseError')}")
                    exit_code = 1
                    continue

                security_data_array = msg.getElement("securityData")
                for i in range(security_data_array.numValues()):
                    security_data = security_data_array.getValueAsElement(i)
                    security_name = security_data.getElementAsString("security")
                    print(f"SECURITY: {security_name}")

                    if security_data.hasElement("securityError"):
                        print(f"SECURITY_ERROR: {security_data.getElement('securityError')}")
                        exit_code = 1
                        continue

                    field_data = security_data.getElement("fieldData")
                    if field_data.hasElement(FIELD):
                        value = field_data.getElement(FIELD).getValueAsString()
                        print(f"{FIELD}: {value}")
                    else:
                        print(f"{FIELD}: MISSING")
                        exit_code = 1

                    if security_data.hasElement("fieldExceptions"):
                        field_exceptions = security_data.getElement("fieldExceptions")
                        for j in range(field_exceptions.numValues()):
                            print(f"FIELD_EXCEPTION: {field_exceptions.getValueAsElement(j)}")
                            exit_code = 1

            if event.eventType() == blpapi.Event.RESPONSE:
                done = True

        return exit_code
    finally:
        session.stop()


if __name__ == "__main__":
    sys.exit(main())
