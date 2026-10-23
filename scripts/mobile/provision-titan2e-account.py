#!/usr/bin/python3
# SPDX-License-Identifier: Apache-2.0
"""The FP6 Alpha 1 default account on an unbooted Titan 2 Elite deployment.

The same shared steps the Alpha 1 image builder runs (seeded_account.py): the
luma account and its groups into the deployment's /etc, then the documented
initial credential (1234, change after login) through the image's own
shadow-utils and PAM. greetd already starts luma-phosh-session (no first-user
wizard).
  provision-titan2e-account.py DEPLOYMENT NORMAL_GREETD
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from seeded_account import materialize_seeded_account, provision_initial_account, shipped_keyrings  # noqa: E402

deployment, normal_greetd = map(pathlib.Path, sys.argv[1:3])
seeded = materialize_seeded_account(deployment)
account = provision_initial_account(deployment, normal_greetd)
keyrings = shipped_keyrings(deployment.parent.parent / 'var/home', deployment / 'etc/skel')
if keyrings:
    sys.exit('an image must ship no keyring: ' + ', '.join(keyrings))
print(json.dumps({'seeded': seeded, 'account': account}, sort_keys=True))
