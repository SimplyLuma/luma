"""Explicit account-independent local bootstrap; no service autostart."""
import argparse
from pathlib import Path
from .bootstrap import create_identity, approve_peer
from .policy import Journal, CAPABILITIES
from .transport import fingerprint


def main():
    parser = argparse.ArgumentParser(description="Luma Connect experimental device bootstrap")
    commands = parser.add_subparsers(dest="command", required=True)
    identity = commands.add_parser("identity", help="create a new private TLS device identity")
    identity.add_argument("directory", type=Path)
    show = commands.add_parser("fingerprint", help="show a public device certificate fingerprint")
    show.add_argument("certificate", type=Path)
    approve = commands.add_parser("approve", help="approve a peer after comparing its full fingerprint on the other device")
    approve.add_argument("directory", type=Path)
    approve.add_argument("certificate", type=Path)
    approve.add_argument("--verified-fingerprint", required=True)
    approve.add_argument("--epoch", required=True, help="fresh 32-hex pairing identifier agreed on both devices")
    approve.add_argument("--grant", action="append", choices=sorted(CAPABILITIES), required=True)
    revoke = commands.add_parser("revoke", help="immediately reject further operations from this device")
    revoke.add_argument("directory", type=Path)
    revoke.add_argument("fingerprint")
    offer = commands.add_parser('offer', help='create a ten-minute local message pairing invitation (public JSON on stdout)')
    offer.add_argument('directory', type=Path)
    offer.add_argument('--request', action='append', choices=['messages.read', 'messages.send'], default=[])
    offer.add_argument('--allow-peer', action='append', choices=['messages.read', 'messages.send'], default=[])
    accept = commands.add_parser('accept-offer', help='accept only after comparing the inviter fingerprint on its own device')
    accept.add_argument('directory', type=Path); accept.add_argument('document', type=Path)
    accept.add_argument('--verified-inviter-fingerprint', required=True)
    accept.add_argument('--allow-incoming', action='append', choices=['messages.read', 'messages.send'], default=[])
    accept.add_argument('--allow-outgoing', action='append', choices=['messages.read', 'messages.send'], default=[])
    accept.add_argument('--replace-existing', action='store_true')
    finish = commands.add_parser('finish-offer', help='finish only after comparing the accepting device fingerprint on that device')
    finish.add_argument('directory', type=Path); finish.add_argument('document', type=Path)
    finish.add_argument('--verified-acceptor-fingerprint', required=True)
    finish.add_argument('--replace-existing', action='store_true')
    args = parser.parse_args()
    if args.command == "identity": print(create_identity(args.directory))
    elif args.command == "fingerprint": print(fingerprint(args.certificate.read_text()))
    elif args.command == "approve":
        print(approve_peer(args.directory, args.certificate, args.verified_fingerprint, args.epoch, args.grant))
    elif args.command == 'revoke':
        journal = Journal(args.directory / "continuity.db")
        try: journal.revoke(args.fingerprint)
        finally: journal.close()
        print("Device revoked")
    else:
        from .pairing import create_offer, accept_offer, finish_offer
        import json
        if args.command == 'offer':
            result = create_offer(args.directory, a_to_b=args.request, b_to_a=args.allow_peer)
        else:
            with args.document.open('rb') as file: document = file.read(32769)
            if args.command == 'accept-offer':
                result = accept_offer(args.directory, document, verified_a_pin=args.verified_inviter_fingerprint,
                    a_to_b=args.allow_incoming, b_to_a=args.allow_outgoing, replace=args.replace_existing)
            else:
                result = finish_offer(args.directory, document, verified_b_pin=args.verified_acceptor_fingerprint,
                                      replace=args.replace_existing)
        print(result.decode() if isinstance(result, bytes) else json.dumps(result, sort_keys=True))


if __name__ == "__main__": main()
