from motion_core import DynamixelBus, MotionPlayer
import logging
logging.basicConfig(level=logging.DEBUG)

bus = DynamixelBus(port="/dev/ttyUSB0", baudrate=1000000)
bus.connect()
print(f"Ping 2: {bus.ping(2)}")
print(f"Ping 5: {bus.ping(5)}")
