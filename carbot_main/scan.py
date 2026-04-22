import serial
import time
def _checksum(pkt): return (~sum(pkt[2:])) & 0xFF
def _build(sid, instr):
    pkt = [0xFF, 0xFF, sid, 2, instr]
    pkt.append(_checksum(pkt))
    return bytes(pkt)
def scan(port='/dev/ttyUSB0'):
    for baud in [57600, 115200, 222222, 1000000, 2000000]:
        print(f"Scanning baudrate {baud}...")
        try:
            ser = serial.Serial(port, baud, timeout=0.1)
            for sid in [1, 8]:
                ser.write(_build(sid, 1))
                time.sleep(0.05)
                res = ser.read(10)
                if res:
                    print(f"  -> Found servo {sid} at {baud}!")
            ser.close()
        except Exception as e:
            print(f"  Error: {e}")
scan()
