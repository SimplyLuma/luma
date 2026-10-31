#!/bin/sh
# Started by luma-esim after a verified profile enable/disable. The CP loads
# the newly enabled profile only after a card power cycle on stack 0 (the
# tmo2 switch): stop ModemManager, SET_SIM_CARD_POWER 0/1, start it again and
# let the radio unit switch the radio back on. The SIT helper's output is
# discarded (it prints modem identifiers).
log() { echo "luma-fold-esim-switch: $*"; }
log "reloading the card after a profile switch"
systemctl stop ModemManager.service
timeout -s KILL 120 python3 /var/lib/luma-fold/modem/luma-fold-modem-sit.py --slot-map --sim-cycle >/dev/null 2>&1
log "card power cycle finished (status $?)"
systemctl start ModemManager.service
systemctl try-restart luma-fold-modem-radio.service
log "ModemManager restarted"
