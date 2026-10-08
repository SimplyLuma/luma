# SPDX-License-Identifier: Apache-2.0
"""Shared bounds for an existing Android window restore or close request.

The CLI must retain its bus identity while the broker completes its bounded
fresh inventory, credential rechecks and Shell call. These are IO budgets,
not permission to extend Android's application-process attach deadline.
ActivateExisting/CloseExisting have launch=False and make one final Shell RPC.
Only the separate Launch method first calls CheckAndroidNativeCaller and then
the engine; launch/initialization has its own existing engine contract.
"""

CREDENTIAL_TIMEOUT_MS = 1000
REGISTRY_QUERY_SECONDS = 30
REGISTRY_CLEANUP_SECONDS = 2
SHELL_TIMEOUT_MS = 5000

# native_caller() reads UID and PID both before and after registry IO.
EXISTING_CREDENTIAL_CALLS = 4
EXISTING_WORK_TIMEOUT_MS = (
    EXISTING_CREDENTIAL_CALLS * CREDENTIAL_TIMEOUT_MS
    + (REGISTRY_QUERY_SECONDS + REGISTRY_CLEANUP_SECONDS) * 1000
    + SHELL_TIMEOUT_MS
)
# The broker admits at most two requests to two workers; there is no waiting
# queue. Allow bounded worker/main-loop dispatch and bus reply overhead.
EXISTING_REPLY_ALLOWANCE_MS = 4000
EXISTING_CLIENT_TIMEOUT_MS = EXISTING_WORK_TIMEOUT_MS + EXISTING_REPLY_ALLOWANCE_MS
