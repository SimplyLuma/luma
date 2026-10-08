#!/bin/sh
# SPDX-License-Identifier: Apache-2.0

set -eu

repo_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
daemon="$repo_root/src/imsd/implementations/main.cpp"
policy="$repo_root/scripts/mobile/net.catcrafts.IMS1-luma.conf"
greeter="$repo_root/src/luma-greeter/luma-greeter.c"

check() {
  label=$1
  shift
  if "$@"; then
    printf 'PASS  %s\n' "$label"
  else
    printf 'FAIL  %s\n' "$label" >&2
    exit 1
  fi
}

check 'daemon classifies restricted emergency targets server-side' grep -Fq \
  'TheEngine->IsEmergencyNumber(target)' "$daemon"
check 'daemon records only emergency-created opaque call ids' grep -Fq \
  'EmergencyCalls.insert(uni)' "$daemon"
check 'restricted hangup checks call provenance' grep -Fq \
  'if (!EmergencyCalls.contains(target))' "$daemon"
check 'greeter policy permits emergency dial only' grep -Fq \
  'send_member="DialEmergency"' "$policy"
check 'greeter policy permits emergency state only' grep -Fq \
  'send_member="GetEmergencyCallState"' "$policy"
check 'greeter policy permits emergency hangup only' grep -Fq \
  'send_member="HangUpEmergency"' "$policy"
check 'greeter policy does not grant the ordinary interface wholesale' sh -c \
  'block=$(sed -n "/<policy user=\"greetd\">/,/<\\/policy>/p" "$1"); ! printf "%s" "$block" | grep -Eq "<allow send_destination=\"net.catcrafts.IMS1\"/>"' _ "$policy"
check 'one tap enters dialer and never places a call' sh -c \
  'start=$(grep -n "^static void emergency_clicked" "$1" | head -1 | cut -d: -f1); end=$(grep -n "^static gboolean emergency_service_available(void) {" "$1" | head -1 | cut -d: -f1); block=$(sed -n "${start},${end}p" "$1"); printf "%s" "$block" | grep -Fq "\"emergency\"" && ! printf "%s" "$block" | grep -Fq "DialEmergency"' _ "$greeter"
check 'greeter never requests ordinary call enumeration' sh -c \
  '! grep -Fq "GetCalls" "$1"' _ "$greeter"
