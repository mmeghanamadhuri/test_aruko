**motor_driver --- ROS2 Humble**

JYQD-V7.3E2 · Jetson NX 8GB · 1 Motor (expandable to 2)

robot_ws/src/motor_driver

**Package Structure**

> motor_driver/
>
> ├── motor_driver/
>
> │ ├── \_\_init\_\_.py
>
> │ ├── motor_config.py ← all pins + tuning
>
> │ └── motor_driver_node.py ← ROS2 node
>
> ├── scripts/
>
> │ ├── motor_config.py ← copy for subprocess import
>
> │ ├── move_forward.py ← DIR locked HIGH at init
>
> │ └── move_reverse.py ← DIR locked LOW at init
>
> ├── launch/
>
> │ └── motor.launch.py
>
> ├── resource/motor_driver
>
> ├── package.xml
>
> ├── setup.py
>
> └── setup.cfg

**Hardware --- Pin Assignment (BOARD numbering)**

  --------------------- -------------------------------------------------
  **PWM_PIN = 33**      VR --- speed control (PWM 1kHz)

  **DIR_PIN = 7**       ZF --- direction (HIGH=forward, LOW=reverse)

  **SIG_PIN = 32**      M --- run signal (active LOW)

  **EN_PIN = 40**       EL --- enable (HIGH=enabled)
  --------------------- -------------------------------------------------

> *⚠ DIR pin latches at init due to internal 5V pull-up on JYQD-V7.3E2. Each direction uses a separate subprocess so GPIO is fully reset between direction changes.*

**How It Works**

teleop_twist_keyboard publishes /cmd_vel (Twist). motor_driver_node subscribes, maps linear.x to a PWM duty cycle, then spawns move_forward.py or move_reverse.py as a subprocess passing the duty cycle as an argument.

Each subprocess sets DIR at init before enabling the driver, ramps PWM up, then holds until killed by the node. On stop or direction change the node sends SIGTERM to the subprocess process group, which triggers the finally block in the script to ramp down and clean up GPIO before exiting.

**Signal Flow**

> teleop_twist_keyboard
>
> ↓ /cmd_vel (linear.x: +0.5 = forward, -0.5 = reverse, 0 = stop)
>
> motor_driver_node.py
>
> ↓ vel_to_duty() maps \|linear.x\| → 20--100% duty
>
> ↓ subprocess.Popen(\[move_forward.py, duty, motor_id\])
>
> move_forward.py / move_reverse.py
>
> ↓ Jetson GPIO + PWM
>
> JYQD-V7.3E2 driver → BLDC motor

**Speed Mapping**

  --------------------- -------------------------------------------------
  **MAX_LINEAR_VEL**    0.5 m/s (teleop default max)

  **MIN_DUTY**          20% (floor --- overcomes static friction)

  **MAX_DUTY**          100%

  **Formula**           duty = 20 + (\|linear.x\| / 0.5) × 80
  --------------------- -------------------------------------------------

**motor_config.py --- Full Source**

> MOTOR_1 = {
>
> \'name\' : \'motor_1\',
>
> \'PWM_PIN\': 33, \# VR
>
> \'DIR_PIN\': 7, \# ZF
>
> \'SIG_PIN\': 32, \# M
>
> \'EN_PIN\' : 40, \# EL
>
> }
>
> \# MOTOR_2 = { \'PWM_PIN\': xx, \'DIR_PIN\': xx, \... } ← add when ready
>
> PWM_FREQUENCY = 1000 \# Hz
>
> MAX_LINEAR_VEL = 0.5 \# m/s
>
> MIN_DUTY = 20 \# %
>
> MAX_DUTY = 100 \# %
>
> RAMP_STEP = 5 \# duty % per step
>
> RAMP_DELAY = 0.03 \# sec per step
>
> STOP_RAMP_STEP = 5
>
> STOP_RAMP_DELAY = 0.02
>
> DIR_SETTLE = 0.3 \# sec --- DIR pin latch time
>
> EN_SETTLE = 0.2 \# sec --- after EN HIGH before PWM
>
> CMD_VEL_DEADZONE = 0.01 \# linear.x below this = STOP

**motor_driver_node.py --- Full Source**

> import rclpy, subprocess, sys, os, signal, time
>
> from rclpy.node import Node
>
> from geometry_msgs.msg import Twist
>
> from std_msgs.msg import String
>
> from motor_driver.motor_config import MAX_LINEAR_VEL, MIN_DUTY, MAX_DUTY, CMD_VEL_DEADZONE
>
> from ament_index_python.packages import get_package_share_directory
>
> SCRIPTS_DIR = os.path.join(get_package_share_directory(\'motor_driver\'), \'scripts\')
>
> FORWARD_SCRIPT = os.path.join(SCRIPTS_DIR, \'move_forward.py\')
>
> REVERSE_SCRIPT = os.path.join(SCRIPTS_DIR, \'move_reverse.py\')
>
> RELAUNCH_COOLDOWN = 1.5 \# sec --- prevents jerk from rapid callbacks
>
> def vel_to_duty(linear_x):
>
> ratio = min(abs(linear_x) / MAX_LINEAR_VEL, 1.0)
>
> return int(MIN_DUTY + ratio \* (MAX_DUTY - MIN_DUTY))
>
> class MotorDriverNode(Node):
>
> def \_\_init\_\_(self):
>
> super().\_\_init\_\_(\'motor_driver_node\')
>
> self.motor_proc = None
>
> self.current_dir = None
>
> self.current_duty = 0
>
> self.last_launch_time = 0.0
>
> self.stop_requested = False
>
> self.create_subscription(Twist, \'/cmd_vel\', self.cmd_vel_callback, 10)
>
> self.status_pub = self.create_publisher(String, \'/motor_status\', 10)
>
> def cmd_vel_callback(self, msg):
>
> linear_x = msg.linear.x
>
> if abs(linear_x) \< CMD_VEL_DEADZONE:
>
> if self.motor_proc and not self.stop_requested:
>
> self.stop_requested = True
>
> self.stop_motor()
>
> return
>
> self.stop_requested = False
>
> direction = \'forward\' if linear_x \> 0 else \'reverse\'
>
> duty = vel_to_duty(linear_x)
>
> if direction == self.current_dir and duty == self.current_duty: return
>
> if (time.time() - self.last_launch_time) \< RELAUNCH_COOLDOWN: return
>
> if direction != self.current_dir and self.motor_proc:
>
> self.stop_motor()
>
> elif direction == self.current_dir and duty != self.current_duty:
>
> self.stop_motor()
>
> self.launch_motor(direction, duty)
>
> def launch_motor(self, direction, duty):
>
> script = FORWARD_SCRIPT if direction == \'forward\' else REVERSE_SCRIPT
>
> self.motor_proc = subprocess.Popen(
>
> \[sys.executable, script, str(duty), \'1\'\], preexec_fn=os.setsid)
>
> self.current_dir = direction
>
> self.current_duty = duty
>
> self.last_launch_time = time.time()
>
> def stop_motor(self):
>
> if not self.motor_proc: return
>
> try:
>
> os.killpg(os.getpgid(self.motor_proc.pid), signal.SIGTERM)
>
> self.motor_proc.wait(timeout=4)
>
> except subprocess.TimeoutExpired:
>
> os.killpg(os.getpgid(self.motor_proc.pid), signal.SIGKILL)
>
> finally:
>
> self.motor_proc = None
>
> self.current_dir = None
>
> self.current_duty = 0

**scripts/move_forward.py --- Full Source**

> import sys, os, time
>
> sys.path.insert(0, os.path.dirname(os.path.realpath(\_\_file\_\_)))
>
> from motor_config import MOTOR_1, PWM_FREQUENCY, RAMP_STEP, RAMP_DELAY,
>
> STOP_RAMP_STEP, STOP_RAMP_DELAY, DIR_SETTLE, EN_SETTLE
>
> import Jetson.GPIO as GPIO
>
> duty = int(sys.argv\[1\]) if len(sys.argv) \> 1 else 100
>
> motor_id = int(sys.argv\[2\]) if len(sys.argv) \> 2 else 1
>
> cfg = MOTOR_1
>
> PWM_PIN, DIR_PIN, SIG_PIN, EN_PIN = cfg\[\'PWM_PIN\'\], cfg\[\'DIR_PIN\'\], cfg\[\'SIG_PIN\'\], cfg\[\'EN_PIN\'\]
>
> duty = max(0, min(100, duty))
>
> GPIO.setmode(GPIO.BOARD)
>
> GPIO.setup(PWM_PIN, GPIO.OUT); GPIO.setup(DIR_PIN, GPIO.OUT)
>
> GPIO.setup(SIG_PIN, GPIO.OUT); GPIO.setup(EN_PIN, GPIO.OUT)
>
> GPIO.output(EN_PIN, GPIO.LOW); GPIO.output(SIG_PIN, GPIO.LOW)
>
> GPIO.output(DIR_PIN, GPIO.HIGH) \# ← FORWARD locked at init
>
> pwm = GPIO.PWM(PWM_PIN, PWM_FREQUENCY)
>
> pwm.start(0)
>
> try:
>
> time.sleep(DIR_SETTLE)
>
> GPIO.output(EN_PIN, GPIO.HIGH)
>
> time.sleep(EN_SETTLE)
>
> for dc in range(0, duty + 1, RAMP_STEP):
>
> pwm.ChangeDutyCycle(dc); time.sleep(RAMP_DELAY)
>
> while True: time.sleep(0.1) \# hold until SIGTERM
>
> except (KeyboardInterrupt, SystemExit): pass
>
> finally:
>
> for dc in range(duty, -1, -STOP_RAMP_STEP):
>
> pwm.ChangeDutyCycle(dc); time.sleep(STOP_RAMP_DELAY)
>
> try: pwm.stop()
>
> except: pass
>
> try: GPIO.output(EN_PIN, GPIO.LOW); GPIO.output(SIG_PIN, GPIO.HIGH)
>
> except: pass
>
> try: GPIO.cleanup()
>
> except OSError: pass \# known Jetson.GPIO 2.1.x bug
>
> *⚠ move_reverse.py is identical except GPIO.output(DIR_PIN, GPIO.LOW) --- direction locked REVERSE at init.*

**Build & Run**

**First Time Setup**

> \# Add GPIO permissions (run once)
>
> sudo usermod -aG gpio \$USER
>
> sudo reboot
>
> \# Build
>
> cd \~/robot_ws
>
> colcon build \--packages-select motor_driver
>
> source install/setup.bash
>
> \# Add alias to \~/.bashrc
>
> alias rmotor=\'source /opt/ros/humble/setup.bash && \\
>
> source /home/jnx/robot_ws/install/setup.bash && \\
>
> ros2 launch motor_driver motor.launch.py\'

**Running**

> \# Terminal 1 --- motor node (no sudo needed after gpio group + reboot)
>
> rmotor
>
> \# Terminal 2 --- teleop
>
> ros2 run teleop_twist_keyboard teleop_twist_keyboard

**Teleop Keys**

  --------------- ------------------ -------------------------------------------
  **Key**         **linear.x**       **Action**

  **i**           +0.5               Forward at full speed

  **,**           -0.5               Reverse at full speed

  **k / Space**   0.0                Stop --- ramp down + GPIO cleanup

  **w / x**       ±increment         Increase / decrease linear speed

  **e / c**       angular            Angular only --- motor ignores (linear=0)
  --------------- ------------------ -------------------------------------------

**Debugging**

> \# Check node is on the ROS graph
>
> ros2 node info /motor_driver_node
>
> \# Watch /cmd_vel live
>
> ros2 topic echo /cmd_vel
>
> \# Watch motor status
>
> ros2 topic echo /motor_status
>
> \# Manually send a forward command
>
> ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \"{linear: {x: 0.5}}\" \--once
>
> \# Manually send stop
>
> ros2 topic pub /cmd_vel geometry_msgs/msg/Twist \"{linear: {x: 0.0}}\" \--once

**Adding Motor 2 (Future)**

1\. Fill in MOTOR_2 in motor_config.py with the correct pins.

2\. In motor_driver_node.py, change motor_id argument from \'1\' to \'1\' or \'2\' based on which motor to drive.

3\. In move_forward.py and move_reverse.py, add: if motor_id == 2: cfg = MOTOR_2

4\. For differential drive, map angular.z from cmd_vel --- positive angular.z = left motor forward + right motor reverse.

**Known Issues & Fixes Applied**

  -------------------------------- ----------------------------------------------------------------------------------------------
  **Issue**                        **Fix**

  **DIR pin latch (2.6V floor)**   Separate subprocess per direction --- GPIO.cleanup() resets fully between launches

  **OSError on GPIO.cleanup()**    Wrap cleanup in try/except --- known Jetson.GPIO 2.1.x double-free bug

  **Motor jerk on key hold**       RELAUNCH_COOLDOWN = 1.5s --- ignores rapid repeated callbacks during ramp-up

  **k not stopping**               stop_requested flag --- ignores flood of linear.x=0 messages during ramp-down

  **Node not on ROS graph**        sudo -E strips env --- removed sudo, added jnx to gpio group instead

  **Scripts not found**            Colcon installs scripts to share/motor_driver/scripts/ --- use get_package_share_directory()
  -------------------------------- ----------------------------------------------------------------------------------------------
