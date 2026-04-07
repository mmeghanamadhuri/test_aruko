import time
import sys

try:
    from actuator import LinearActuator
except ImportError:
    print("Error: Could not import actuator.py. Make sure you are in the carbot_main directory.")
    sys.exit(1)

def main():
    print("Initializing Linear Actuator on Jetson Pins 35 and 37...")
    arm = LinearActuator(in3_pin=35, in4_pin=37)
    
    try:
        while True:
            print("\n" + "="*30)
            print("   ACTUATOR TEST MENU")
            print("="*30)
            print(" e  - Extend  (Custom Distance)")
            print(" r  - Retract (Custom Distance)")
            print(" E  - Extend  Fully (100mm / 20sec)")
            print(" R  - Retract Fully (100mm / 20sec)")
            print(" q  - Quit and safely release pins")
            print("="*30)
            
            choice = input("Select an action: ").strip()
            
            if choice == 'e':
                try:
                    dist = float(input("  Enter distance to extend (mm): "))
                    arm.extend(distance_mm=dist)
                except ValueError:
                    print("  Invalid distance entered.")
            elif choice == 'r':
                try:
                    dist = float(input("  Enter distance to retract (mm): "))
                    arm.retract(distance_mm=dist)
                except ValueError:
                    print("  Invalid distance entered.")
            elif choice == 'E':
                arm.extend(distance_mm=100)
            elif choice == 'R':
                arm.retract(distance_mm=100)
            elif choice == 'q':
                print("\nExiting safe test module.")
                break
            else:
                print("Invalid character selected. Please try again.")
                
    except KeyboardInterrupt:
        print("\nTest manually interrupted via Ctrl+C.")
    finally:
        arm.cleanup()
        print("GPIO Memory strictly flushed and cleaned up.")

if __name__ == "__main__":
    main()
