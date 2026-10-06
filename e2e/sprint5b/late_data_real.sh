#!/bin/bash
# Sprint 5b acceptance (d), REAL path: virtual plant --scenario F (F1) -> edge gateway -> uplink proxy -> MQTT ->
# server. The gateway's uplink (a TCP proxy in front of the broker) is cut mid-run while the plant keeps running,
# then restored after the engine already processed earlier blocks; the buffered readings must be ingested and every
# complete block / usage / recompute must equal the uninterrupted golden values (e2e/sprint5b/late_data_check.py).
#
# Needs a QA server (SEED_DEMO_PLANT=0, SOURCE_TS_MAX_AGE_S=31536000, PREDICTIVE_TICK_S=10) on API with its own
# broker on BROKER_PORT, already set up with `e2e/sprint5/scenario_f_real/f_real.py setup` (STATE).
#   API=http://127.0.0.1:8175 STATE=/tmp/x/f_state.json BROKER_PORT=18876 SPEED=150 RUN_S=7400 CUT_AT=3000 \
#     RESTORE_AT=3600 bash e2e/sprint5b/late_data_real.sh
# QA 5b: SPEED=150 (≈70 readings/s) passes; SPEED=500 (≈230 readings/s) does not recover (edge buffer, see report).
set -u
cd "$(dirname "$0")/../.."
P=.venv-edge/bin/python
PX=${PROXY_PORT:-$((BROKER_PORT + 1))}
D=$(dirname "$STATE")
$P e2e/sprint5b/uplink_proxy.py "$PX" "$BROKER_PORT" > "$D/proxy.log" 2>&1 & PXPID=$!
sleep 1; T0=$(date +%s)
BROKER_PORT=$PX $P e2e/sprint5/scenario_f_real/f_real.py run F1 "${SPEED:-150}" 0 "${VP_PORT:-8150}" "${CONTROL_PORT:-8152}" "${RUN_S:-7400}" > "$D/runF1.log" 2>&1 & RUN=$!
at() { while [ $(( $(date +%s) - T0 )) -lt "$1" ]; do sleep 5; done; }
sleep 25; $P e2e/sprint5/scenario_f_real/f_real.py monitor F1
at "${CUT_AT:-3000}"; kill $PXPID; echo "uplink cut at +$(( $(date +%s) - T0 )) s"
at "${RESTORE_AT:-3600}"; $P e2e/sprint5b/uplink_proxy.py "$PX" "$BROKER_PORT" >> "$D/proxy.log" 2>&1 & PXPID=$!
echo "uplink restored at +$(( $(date +%s) - T0 )) s"
wait $RUN; sleep 60; kill $PXPID
echo "now: TRUTH_CYCLES=<last value of the b3 counter in Influx> API=$API STATE=$STATE .venv/bin/python e2e/sprint5b/late_data_check.py"
