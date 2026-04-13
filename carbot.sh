#!/bin/bash
# ── Carbot launcher ──────────────────────────────────────────────────────────
# Usage:
#   ./carbot.sh                          # interactive menu
#   ./carbot.sh front_left_window        # go straight to tracking

BUTTONS=(front_left_window front_right_window rear_left_window rear_right_window door_lock window_lock)

# ── Defaults (edit these to tune your robot) ─────────────────────────────────
TRACK_SPEED=800
SEARCH_SPEED=500
KP_X=0.65
KP_Y=0.65
KP_FAR=0.85
KP_MID=0.85
KP_NEAR=0.85
SMOOTH_ALPHA=0.90
MAX_DELTA=260
DEADZONE=50
CONFIDENCE=0.5
INVERT_PAN=1
INVERT_TILT=1
INFER_INTERVAL=0.10  # faster vision updates
PREVIEW=true         # set false to disable MJPEG stream
REALIGN_PX=80        # Eye (6,7) waits unless error > 80px
MOTION_HOST=127.0.0.1
MOTION_PORT=5000
# Multi-joint Approach Vector (Motors 1,2,3,4 reach forward)
# DIRECTIONS: S1(+), S2(-), S3(+), S4(+)
# Servo 5, 6, and 7 are now reserved for TRIPLE-AXIS TRACKING.
APPROACH_SERVOS="1,2,3,4"
APPROACH_DELTAS="15,-20,50,40"
APPROACH_DIR=1
APPROACH_AREA=0.25
APPROACH_SPD=450
APPROACH_PAUSE=0.4

# ── Approach: arm forward vector ──────────────────────────────────────────────
# APPROACH_SERVOS / APPROACH_DELTAS define DIRECTION, not destination.
# The arm advances this many counts per step on each servo, every inference tick
# that the button is centred.  Tune these to match your arm kinematics.
# (These are the same values carbot.sh already had, just clarified.)

# Pixel error below which an arm step is allowed to fire.
# If the button drifts above this, the step pauses until pan/tilt re-centres it.
APPROACH_ARM_THR=40

# Pixel error above which a pan/tilt micro-correction is sent during approach.
# Below this the pan/tilt hold steady (avoids jitter near centre).
APPROACH_PAN_THR=18

# Max pan/tilt delta (counts) during approach — smaller than normal tracking
# so corrections are gentle and don't swing the camera off target.
APPROACH_MAX_PAN=70

# Inference ticks to wait after each arm step before allowing the next one.
# Higher = slower but more stable.  3 ticks @ 0.12s interval = ~0.36s settle.
APPROACH_STEP_COOLDOWN=3

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
  MODEL_PATH="best.pt" \
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
  MOTION_HOST="$MOTION_HOST" \
  MOTION_PORT="$MOTION_PORT" \
  python3 -m vision.window_servo $PREVIEW_FLAG \
    --motion-host "$MOTION_HOST" \
    --motion-port "$MOTION_PORT"
}

# ── Entry point ───────────────────────────────────────────────────────────────
print_banner

# If a button name passed as argument, go straight to it
if [[ -n "$1" ]]; then
  # Validate it's a known button
  valid=false
  for b in "${BUTTONS[@]}"; do [[ "$b" == "$1" ]] && valid=true && break; done
  if [[ "$valid" == "true" ]]; then
    launch "$1"
  else
    echo -e "${RED}Unknown button '$1'. Valid options:${RESET}"
    printf '  %s\n' "${BUTTONS[@]}"
    exit 1
  fi
else
  pick_button
  launch "$BUTTON"
fi


