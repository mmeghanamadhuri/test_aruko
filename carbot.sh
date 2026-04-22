
#!/bin/bash
# ── Carbot launcher ──────────────────────────────────────────────────────────
# Usage:
#   ./carbot.sh                          # interactive menu
#   ./carbot.sh front_left_window        # go straight to tracking




BUTTONS=(front_left_window front_right_window rear_left_window rear_right_window door_lock window_lock neutral)




# ── Defaults (edit these to tune your robot) ─────────────────────────────────
TRACK_SPEED=1023          # was 2000 (×1.2→2400) — clamped 1023 MX moving speed
SEARCH_SPEED=1023          # was 2200 (×1.2→2640) — clamped 1023
KP_X=0.65
KP_Y=0.65
KP_FAR=0.85
KP_MID=0.85
KP_NEAR=0.85
SMOOTH_ALPHA=0.90
MAX_DELTA=400             # was 260  — larger single-step corrections allowed
DEADZONE=40               # was 50   — tighter convergence zone (still stable)
CONFIDENCE=0.6
INVERT_PAN=1
INVERT_TILT=1
INFER_INTERVAL=0.0277778  # ÷1.5 vs prior — faster control period
ALIGN_STABLE_FRAMES=1     # min 1 — cannot reduce further
PREVIEW=true              # set false to disable MJPEG stream
REALIGN_PX=80             # Eye (6,7) waits unless error > 80px
# motion_server on HOST + carbot.sh inside DOCKER: 127.0.0.1 is the *container*, not the host.
# Use one of:  host.docker.internal  |  Linux: IP from "docker run --rm alpine ip route" default gw
# Or run the vision container with --network host (then 127.0.0.1 is correct).
MOTION_HOST="${MOTION_HOST:-127.0.0.1}"
MOTION_PORT=5000




# ── Motion: short → pick → vision; after each vision exit → wait → short → pick (loop).
# Revert (VISION_REVERT_JSON / revert_short.json) runs inside vision only — not replayed by carbot.sh.
# Requires motion_server running with MOTIONS_DIR containing these paths
# (typically start motion_server from carbot_main/).
STARTUP_JSON_DELAY_SEC=0.5555555 # ÷1.5 vs prior between startup clips
STARTUP_JSON_FILES="actions/short.json"
# Startup robustness (play_startup_sequence.py)
STARTUP_SERVER_WAIT_SEC=30 #90
STARTUP_PLAY_RETRIES=6
STARTUP_PLAYBACK_TIMEOUT_SEC=180
STARTUP_POST_STOP_DELAY_SEC=0.1111111 # ÷1.5 vs prior
# Pause after vision exits before short.json + next button selection
POST_CYCLE_DELAY_SEC=0.5555555 # ÷1.5 vs prior
# Multi-joint Approach Vector (Motors 1,2,3,4 reach forward)
# DIRECTIONS: S1(+), S2(-), S3(+), S4(+)
# Servo 5, 6, and 7 are now reserved for TRIPLE-AXIS TRACKING.
APPROACH_SERVOS="1,2,3,4"
APPROACH_DELTAS="15,-20,50,40"  
APPROACH_DIR=1
APPROACH_AREA=0.12
APPROACH_SPD=1023         # ×1.5 vs prior (capped 1023)
APPROACH_PAUSE=0.2222222 # ÷1.5 vs prior settle between approach steps
VISION_OFFSET_AFTER_APPROACH=1
VISION_OFFSET_V_ACTUATOR=extend   # or retract — whichever pushes toward the button
VISION_OFFSET_V_MM=70            # linear actuator: extend (mm), then wait, then retract same mm
VISION_OFFSET_V_WAIT_SEC=3.3333333 # ÷1.5 vs prior dwell at full extension (sec)
VISION_OFFSET_V_EXTRA_MM=0        # optional second actuator leg (mm); 0 = extend+dwell+retract only
# Before POST_V actuator extend: relative raw move on tilt servo (default S7, -600 ≈ 1 cm for many rigs)
VISION_PRE_ACTUATOR_TILT_DELTA=-2200
VISION_PRE_ACTUATOR_TILT_SERVO=7
VISION_PRE_ACTUATOR_TILT_SPEED=1023 # ×1.5 vs prior (capped 1023)
VISION_PRE_ACTUATOR_TILT_SETTLE_SEC=0.1944445 # ÷1.5 vs prior
VISION_PRESS_JSON=""              # empty = no press.json after approach
# revert_short is not played at end of vision when POST_CYCLE_BACK_JSON is set — use menu ``7. neutral``.
VISION_REVERT_JSON=""
VISION_POST_CYCLE_BACK_JSON="actions/back.json"
VISION_SEARCH_SWEEP_SEC=8.3333333 # ÷1.5 vs prior per-direction tilt sweep (sec)
VISION_SEARCH_BILATERAL=1         # 0 = legacy pan/tilt pattern




# # Fractional offset within bbox for aiming point (-0.5 to +0.5)
# # 0.0 = center, +0.5 = right/bottom edge, -0.5 = left/top edge
# VISION_AIM_OFFSET_X=-0.4   # tune this: e.g. -0.3 to aim left of center
# VISION_AIM_OFFSET_Y=0.0   # tune this: e.g. -0.3 to aim above center




# ── Approach: arm forward vector ──────────────────────────────────────────────
# APPROACH_SERVOS / APPROACH_DELTAS define DIRECTION, not destination.
# The arm advances this many counts per step on each servo, every inference tick
# that the button is centred.  Tune these to match your arm kinematics.
# (These are the same values carbot.sh already had, just clarified.)




# Pixel error below which an arm step is allowed to fire.
# If the button drifts above this, the step pauses until pan/tilt re-centres it.
APPROACH_ARM_THR=70       # was 40 — arm steps even with moderate drift (70px = 11% of 640px frame)




# Pixel error above which a pan/tilt micro-correction is sent during approach.
# Below this the pan/tilt hold steady (avoids jitter near centre).
APPROACH_PAN_THR=18




# Max pan/tilt delta (counts) during approach — smaller than normal tracking
# so corrections are gentle and don't swing the camera off target.
APPROACH_MAX_PAN=70




# Inference ticks to wait after each arm step before allowing the next one.
# Higher = slower but more stable.  1 tick @ 0.05s interval = ~50ms settle.
APPROACH_STEP_COOLDOWN=1  # was 3 — 3 ticks × 100ms = 300ms dead wait per step. 1 tick = 50ms.




cd "$(dirname "$0")"




# ── Colour helpers ────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; CYAN='\033[0;36m'
YELLOW='\033[1;33m'; BOLD='\033[1m'; RESET='\033[0m'




print_banner() {
  echo -e "${CYAN}"
  echo "  ╔══════════════════════════════╗"
  echo "  ║       CARBOT LAUNCHER        ║"
  echo "  ╚══════════════════════════════╝"
  echo -e "${RESET}"
}




# Play one or more motion JSON paths (space-separated). Same RPC path as initial startup.
play_motion_json_files() {
  local _title="$1"
  local _files="$2"
  local _done_msg="$3"
  if [[ -z "$_files" ]]; then
    return 0
  fi
  echo -e "${CYAN}${_title} (wait for each clip, then ${STARTUP_JSON_DELAY_SEC}s pause)…${RESET}"
  if ! MOTION_HOST="$MOTION_HOST" MOTION_PORT="$MOTION_PORT" \
       STARTUP_JSON_DELAY_SEC="$STARTUP_JSON_DELAY_SEC" \
       STARTUP_JSON_FILES="$_files" \
       STARTUP_SERVER_WAIT_SEC="$STARTUP_SERVER_WAIT_SEC" \
       STARTUP_PLAY_RETRIES="$STARTUP_PLAY_RETRIES" \
       STARTUP_PLAYBACK_TIMEOUT_SEC="$STARTUP_PLAYBACK_TIMEOUT_SEC" \
       STARTUP_POST_STOP_DELAY_SEC="$STARTUP_POST_STOP_DELAY_SEC" \
       python3 carbot_main/play_startup_sequence.py; then
    echo -e "${RED}Motion playback failed — is motion_server running from carbot_main?${RESET}"
    return 1
  fi
  echo -e "${GREEN}${_done_msg}${RESET}"
  echo ""
}

play_startup_sequence() {
  play_motion_json_files "Playing startup sequence" "$STARTUP_JSON_FILES" "Startup sequence complete."
}




pick_button() {
  echo -e "${BOLD}Select target button:${RESET}\n"
  for i in "${!BUTTONS[@]}"; do
    echo -e "  ${YELLOW}$((i+1))${RESET}. ${BUTTONS[$i]}"
  done
  echo ""
  while true; do
    read -rp "  Enter number (1-${#BUTTONS[@]}): " choice
    if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= ${#BUTTONS[@]} )); then
      BUTTON="${BUTTONS[$((choice-1))]}"
      break
    fi
    echo -e "  ${RED}Invalid choice, try again.${RESET}"
  done
}




launch() {
  local button="$1"
  echo ""
  echo -e "${GREEN}► Targeting:   ${BOLD}${button}${RESET}"
  echo -e "${GREEN}► Track speed: ${TRACK_SPEED}   KP: ${KP_X}/${KP_Y}${RESET}"
  [[ "$PREVIEW" == "true" ]] && echo -e "${GREEN}► MJPEG stream: http://$(hostname -I | awk '{print $1}'):8080/${RESET}"
  echo ""




  PREVIEW_FLAG=""
  [[ "$PREVIEW" == "true" ]] && PREVIEW_FLAG="--preview"




  VISION_LABEL_ALLOWLIST="$button" \
  VISION_RUNTIME="yolo" \
  VISION_MODEL_PATH="${VISION_MODEL_PATH:-best.pt}" \
  VISION_INVERT_PAN="$INVERT_PAN" \
  VISION_INVERT_TILT="$INVERT_TILT" \
  VISION_KP_X="$KP_X" \
  VISION_KP_Y="$KP_Y" \
  VISION_KP_FAR="$KP_FAR" \
  VISION_KP_MID="$KP_MID" \
  VISION_KP_NEAR="$KP_NEAR" \
  VISION_SMOOTH_ALPHA="$SMOOTH_ALPHA" \
  VISION_TRACK_SPEED="$TRACK_SPEED" \
  VISION_SEARCH_SPEED="$SEARCH_SPEED" \
  VISION_MAX_DELTA="$MAX_DELTA" \
  VISION_DEADZONE_PX="$DEADZONE" \
  VISION_ALIGN_STABLE_FRAMES="$ALIGN_STABLE_FRAMES" \
  VISION_CONFIDENCE="$CONFIDENCE" \
  VISION_INFER_INTERVAL_SEC="$INFER_INTERVAL" \
  VISION_REALIGN_PX="$REALIGN_PX" \
  VISION_APPROACH_SERVOS="$APPROACH_SERVOS" \
  VISION_APPROACH_DELTAS="$APPROACH_DELTAS" \
  VISION_APPROACH_DIR="$APPROACH_DIR" \
  VISION_APPROACH_AREA_FRAC="$APPROACH_AREA" \
  VISION_APPROACH_SPEED="$APPROACH_SPD" \
  VISION_APPROACH_PAUSE="$APPROACH_PAUSE" \
  VISION_APPROACH_ARM_THR="$APPROACH_ARM_THR" \
  VISION_APPROACH_PAN_THR="$APPROACH_PAN_THR" \
  VISION_APPROACH_MAX_PAN="$APPROACH_MAX_PAN" \
  VISION_APPROACH_STEP_COOLDOWN="$APPROACH_STEP_COOLDOWN" \
  VISION_OFFSET_AFTER_APPROACH="1" \
  VISION_OFFSET_V_ACTUATOR="$VISION_OFFSET_V_ACTUATOR" \
  VISION_CAMERA_PRESS_OFFSET_V_MM="$VISION_OFFSET_V_MM" \
  VISION_OFFSET_V_WAIT_SEC="$VISION_OFFSET_V_WAIT_SEC" \
  VISION_OFFSET_V_EXTRA_MM="$VISION_OFFSET_V_EXTRA_MM" \
  VISION_PRE_ACTUATOR_TILT_DELTA="$VISION_PRE_ACTUATOR_TILT_DELTA" \
  VISION_PRE_ACTUATOR_TILT_SERVO="$VISION_PRE_ACTUATOR_TILT_SERVO" \
  VISION_PRE_ACTUATOR_TILT_SPEED="$VISION_PRE_ACTUATOR_TILT_SPEED" \
  VISION_PRE_ACTUATOR_TILT_SETTLE_SEC="$VISION_PRE_ACTUATOR_TILT_SETTLE_SEC" \
  VISION_PRESS_JSON="$VISION_PRESS_JSON" \
  VISION_REVERT_JSON="$VISION_REVERT_JSON" \
  VISION_POST_CYCLE_BACK_JSON="$VISION_POST_CYCLE_BACK_JSON" \
  VISION_SEARCH_SWEEP_SEC="$VISION_SEARCH_SWEEP_SEC" \
  VISION_SEARCH_BILATERAL="$VISION_SEARCH_BILATERAL" \
  VISION_AIM_OFFSET_X="$VISION_AIM_OFFSET_X" \
  VISION_AIM_OFFSET_Y="$VISION_AIM_OFFSET_Y" \
  MOTION_HOST="$MOTION_HOST" \
  MOTION_PORT="$MOTION_PORT" \
  CARBOT_SHELL_LOOP=1 \
  PYTHONUNBUFFERED=1 \
  python3 -u -m vision.window_servo $PREVIEW_FLAG \
    --motion-host "$MOTION_HOST" \
    --motion-port "$MOTION_PORT"
}




# ── Entry point ───────────────────────────────────────────────────────────────
print_banner




# If a button name passed as argument, go straight to it (single cycle)
if [[ -n "$1" ]]; then
  if [[ "$1" == "neutral" ]]; then
    play_motion_json_files "Playing neutral (revert_short)" "actions/revert_short.json" "Neutral motion complete." || exit 1
    exit 0
  fi
  valid=false
  for b in "${BUTTONS[@]}"; do [[ "$b" == "$1" ]] && valid=true && break; done
  if [[ "$valid" != "true" ]]; then
    echo -e "${RED}Unknown button '$1'. Valid options:${RESET}"
    printf '  %s\n' "${BUTTONS[@]}"
    exit 1
  fi
  play_startup_sequence || exit 1
  launch "$1"
  exit $?
fi




# Interactive: short once at start → pick → vision (POST_EXIT plays back.json inside vision); then pick (no short replay).
# neutral → revert_short only.
play_startup_sequence || exit 1
pick_button
while true; do
  while [[ "$BUTTON" == "neutral" ]]; do
    echo -e "${CYAN}Neutral: playing actions/revert_short.json …${RESET}"
    play_motion_json_files "Playing neutral (revert_short)" "actions/revert_short.json" "Neutral motion complete." || exit 1
    pick_button
  done
  launch "$BUTTON" || true
  echo ""
  echo -e "${CYAN}Vision exited. Waiting ${POST_CYCLE_DELAY_SEC}s, then back.json → button selection…${RESET}"
  sleep "$POST_CYCLE_DELAY_SEC"
  pick_button
done


