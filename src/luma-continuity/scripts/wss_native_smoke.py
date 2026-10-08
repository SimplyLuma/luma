"""Cross-component synthetic native SMS over actual cloud-owned TLS/WSS relay.

Requires precreated disposable identities and freshly minted operator tickets.
No live personal store, modem, EDS, notification owner or phone is accessed.
"""
import argparse
import json
from pathlib import Path
import secrets
import ssl
import socket
import sys
import threading
from websockets.sync.client import connect
from luma_continuity import transport
from luma_continuity.relay import TLSRelayStream
from luma_continuity.policy import Journal
from luma_continuity.native import NativeMessages
from luma_continuity.session import Receiver

parser = argparse.ArgumentParser()
parser.add_argument('--identities', type=Path, required=True)
parser.add_argument('--tickets', type=Path, required=True)
parser.add_argument('--native-source', type=Path, required=True)
parser.add_argument('--ca', type=Path, required=True)
parser.add_argument('--url', default='wss://api.connect.test:18443/v1/relay/')
parser.add_argument('--dial-host', default='127.0.0.1')
parser.add_argument('--dial-port', default=18443, type=int)
args = parser.parse_args()
sys.path.insert(0, str(args.native_source))
from prairie_apps.messages_backend import MessageStore
from prairie_apps.messages_reply import ReplySender

tickets = json.loads(args.tickets.read_text())
root = args.identities
phone, desktop = root / 'phone', root / 'desktop'
phone_pin = transport.fingerprint((phone / 'device.pem').read_text())
desktop_pin = transport.fingerprint((desktop / 'device.pem').read_text())
if 'tickets' in tickets:
    for endpoint, fingerprint in tickets['fingerprints'].items():
        if fingerprint == phone_pin: tickets['phone_ticket'] = tickets['tickets'][endpoint]
        elif fingerprint == desktop_pin: tickets['desktop_ticket'] = tickets['tickets'][endpoint]
epoch = secrets.token_hex(16)
sent, failures, results = [], [], []
class Modem:
    def inspect(self):
        class Capability: available = True
        return Capability()
    def send(self, address, body):
        sent.append((address, body)); return 'synthetic-modem-id'


def stream(local, peer, ticket, server):
    # Exact test CA and host override are explicit only in this lab script.
    outer = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    outer.minimum_version = ssl.TLSVersion.TLSv1_2
    outer.load_verify_locations(cafile=str(args.ca))
    ws = connect(args.url + tickets['session_id'], ssl=outer,
        sock=socket.create_connection((args.dial_host, args.dial_port), timeout=20), server_hostname='api.connect.test',
        additional_headers={'X-Luma-Relay-Ticket': ticket}, proxy=None,
        compression=None, max_size=65536, max_queue=4, open_timeout=20, close_timeout=2)
    context = transport.context(local / 'device.pem', local / 'device.key', peer / 'device.pem', server=server)
    inner = TLSRelayStream(ws, context, transport.fingerprint((peer / 'device.pem').read_text()), server=server)
    inner.handshake()
    return inner


def receiver():
    store = MessageStore(phone / 'synthetic-messages.db')
    journal = Journal(phone / 'continuity.db')
    inner = None
    try:
        journal.approve(desktop_pin, epoch, ['messages.send', 'messages.read'])
        adapter = NativeMessages(store, ReplySender(store.path, Modem), pair_token=epoch, authorized=lambda: True)
        inner = stream(phone, desktop, tickets['phone_ticket'], True)
        endpoint = Receiver(journal, {'messages.send': adapter, 'messages.read': adapter})
        for _ in range(3): endpoint.serve_one(inner, desktop_pin)
    except Exception as error:
        failures.append(type(error).__name__)
    finally:
        if inner: inner.close()
        journal.close(); store.close()

worker = threading.Thread(target=receiver)
worker.start()
inner = None
try:
    import time
    inner = stream(desktop, phone, tickets['desktop_ticket'], False)
    request = dict(version=1, epoch=epoch, id=secrets.token_hex(16), account=None,
        capability='messages.send', expires=int(time.time())+60,
        payload={'address': '+12025550123', 'body': 'Synthetic WSS native SMS'})
    for _ in range(2):
        transport.send(inner, request); results.append(transport.receive(inner))
    query = {**request, 'id': secrets.token_hex(16), 'capability': 'messages.read',
             'payload': {'address': '+12025550123', 'limit': 10}}
    transport.send(inner, query); results.append(transport.receive(inner))
finally:
    if inner: inner.close()
    worker.join(30)
assert not worker.is_alive() and not failures, failures
assert len(sent) == 1, 'native send count'
assert results[0] == results[1], 'deduplicated result'
assert results[0]['result']['state'] == 'sent', 'native fixture send'
assert results[2]['result']['messages'][0]['body'] == 'Synthetic WSS native SMS', 'native read'
print(json.dumps({'passed':True, 'outer':'actual TLS/WSS cloud relay in disposable Linux VM',
    'inner':'TLS1.3 mTLS exact pins', 'native_sends':len(sent), 'duplicate_requests':2,
    'native_read':True, 'physical_device':False, 'internet_path':False}))
