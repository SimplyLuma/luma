"""A bounded, source-owned request receiver. TLS authenticates peer identity.

An owning native process supplies explicitly scoped adapters. No arbitrary
method, D-Bus or executable forwarding is available. Local capability changes
must serialize with this receiver; active media cancellation is separate work.
"""
from . import transport
from .policy import Denied


class Receiver:
    def __init__(self, journal, adapters):
        self.journal = journal
        self.adapters = dict(adapters)

    def handle(self, authenticated_peer, request):
        def invoke(capability, payload):
            adapter = self.adapters.get(capability)
            if adapter is None:
                return {"error": "unavailable"}
            if capability in {"messages.send", "notifications.act"}:
                # One immutable operation ID across queue, journal and native sender.
                if "operation" in payload:
                    return {"error": "invalid-request"}
                payload = {**payload, "operation": request["id"]}
            try:
                return adapter(capability, payload)
            except (ValueError, KeyError):
                return {"error": "invalid-request"}
            except PermissionError:
                return {"error": "revoked"}
            # All other adapter failures keep the journal UNKNOWN. Error text can
            # contain private data and must not be returned or logged remotely.
        return self.journal.dispatch(authenticated_peer, request, invoke)

    def serve_one(self, stream, authenticated_peer):
        try:
            request = transport.receive(stream)
            result = self.handle(authenticated_peer, request)
        except Denied:
            result = {"state": "denied", "result": None}
        except (ValueError, EOFError):
            result = {"state": "invalid", "result": None}
        except Exception:
            result = {"state": "unknown", "result": None}
        transport.send(stream, result)
