import Jetson.GPIO as GPIO
import time
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')

class LinearActuator:
    def __init__(self, in3_pin=35, in4_pin=37):
        """
        Initializes the linear actuator L298N driver explicitly using BOARD pin numbering.
        """
        self.in3_pin = in3_pin
        self.in4_pin = in4_pin
        
        # Actuator Specifications: 12V DC, 188N force, 5mm/sec speed, 100mm max stroke length 
        self.speed_mm_per_sec = 5.0 
        self.max_length_mm = 100.0

        # Setup Jetson GPIO (BOARD orientation to match the 40-pin header diagram)
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self.in3_pin, GPIO.OUT, initial=GPIO.LOW)
        GPIO.setup(self.in4_pin, GPIO.OUT, initial=GPIO.LOW)
        logging.info(f"Initialized Linear Actuator on pins IN3={self.in3_pin}, IN4={self.in4_pin}")
        
    def stop(self):
        """Halts the actuator perfectly in place."""
        GPIO.output(self.in3_pin, GPIO.LOW)
        GPIO.output(self.in4_pin, GPIO.LOW)

    def extend(self, duration=None, distance_mm=None, wait=True):
        """
        Extends the actuator via OUT3 & OUT4.
        Can be controlled perfectly either by Distance (mm) or raw Time (seconds).
        If wait=True, this successfully acts as a "blocker" between frames until movement finishes!
        """
        if distance_mm is not None:
            # e.g., 20mm / 5 mm/s = 4.0 seconds
            duration = distance_mm / self.speed_mm_per_sec
        elif duration is None:
            # Safely default to full stroke extension if nothing provided
            duration = self.max_length_mm / self.speed_mm_per_sec

        logging.info(f"Extending actuator for {duration:.2f} seconds...")
        GPIO.output(self.in3_pin, GPIO.HIGH)
        GPIO.output(self.in4_pin, GPIO.LOW)

        if wait and duration > 0:
            time.sleep(duration)
            self.stop()
            
    def retract(self, duration=None, distance_mm=None, wait=True):
        """
        Retracts the actuator via OUT3 & OUT4.
        Can be controlled perfectly either by Distance (mm) or raw Time (seconds).
        """
        if distance_mm is not None:
            duration = distance_mm / self.speed_mm_per_sec
        elif duration is None:
            # Safely default to full stroke retraction 
            duration = self.max_length_mm / self.speed_mm_per_sec

        logging.info(f"Retracting actuator for {duration:.2f} seconds...")
        GPIO.output(self.in3_pin, GPIO.LOW)
        GPIO.output(self.in4_pin, GPIO.HIGH)

        if wait and duration > 0:
            time.sleep(duration)
            self.stop()

    def cleanup(self):
        """Safely releases the Jetson memory pins."""
        self.stop()
        GPIO.cleanup()


if __name__ == "__main__":
    # Quick module test
    try:
        arm = LinearActuator(in3_pin=35, in4_pin=37)
        
        print("Testing Step 1: Extending 20mm (Should take 4 seconds)")
        arm.extend(distance_mm=20)
        time.sleep(1)
        
        print("Testing Step 2: Retracting purely by time (3 seconds)")
        arm.retract(duration=3.0)
        
    except KeyboardInterrupt:
        print("Test stopped securely.")
    finally:
        arm.cleanup()




