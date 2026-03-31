import Jetson.GPIO as GPIO
import time

PWM_PIN = 33
DIR_PIN = 7
SIG_PIN = 32
EN_PIN  = 40

GPIO.setmode(GPIO.BOARD)
GPIO.setup(PWM_PIN, GPIO.OUT)
GPIO.setup(DIR_PIN, GPIO.OUT)
GPIO.setup(SIG_PIN, GPIO.OUT)
GPIO.setup(EN_PIN,  GPIO.OUT)

# Safe initial state
GPIO.output(EN_PIN,  GPIO.LOW)
GPIO.output(SIG_PIN, GPIO.LOW)
GPIO.output(DIR_PIN, GPIO.HIGH)   # FORWARD locked at init

pwm = GPIO.PWM(PWM_PIN, 1000)
pwm.start(0)

try:
    time.sleep(0.3)               # let DIR latch
    GPIO.output(EN_PIN, GPIO.HIGH)
    time.sleep(0.2)

    # Ramp up
    for dc in range(0, 101, 5):
        pwm.ChangeDutyCycle(dc)
        time.sleep(0.03)

    print("FORWARD running")
    time.sleep(5)                 # run duration — adjust as needed

    # Ramp down
    for dc in range(100, -1, -5):
        pwm.ChangeDutyCycle(dc)
        time.sleep(0.03)

except KeyboardInterrupt:
    pass

finally:
    try:
        pwm.ChangeDutyCycle(0)
    except Exception:
        pass
    try:
        pwm.stop()
    except Exception:
        pass
    try:
        GPIO.output(EN_PIN,  GPIO.LOW)
        GPIO.output(SIG_PIN, GPIO.HIGH)
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except OSError:
        pass                          # known Jetson.GPIO 2.1.x bug — fd already closed
    print("FORWARD done")