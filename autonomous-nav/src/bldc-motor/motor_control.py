import subprocess
import sys
import time

FORWARD_SCRIPT = "move_forward.py"
REVERSE_SCRIPT = "move_reverse.py"

def run_motor(script):
    print(f"Launching: {script}")
    result = subprocess.run(
        [sys.executable, script],
        timeout=30           # safety cutoff — adjust to your run duration + margin
    )
    if result.returncode != 0:
        print(f"WARNING: {script} exited with code {result.returncode}")
    time.sleep(1)            # brief pause between direction changes

try:
    while True:
        run_motor(FORWARD_SCRIPT)
        run_motor(REVERSE_SCRIPT)

except KeyboardInterrupt:
    print("Controller stopped")