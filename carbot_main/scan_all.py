import serial, time

def test_servo(ser, sid):
    ser.reset_input_buffer()
    pkt = [255, 255, sid, 2, 1]
    pkt.append((~sum(pkt[2:])) & 0xFF)
    ser.write(bytes(pkt))
    ser.flush()
    
    start = time.time()
    resp = b""
    while time.time() - start < 0.05:
        if ser.in_waiting:
            resp += ser.read(ser.in_waiting)
        time.sleep(0.001)
    return resp

print("Scanning ALL IDs 0-253 at 1000000 baud...")
try:
    ser = serial.Serial('/dev/ttyUSB0', 222222, timeout=0)
    found = []
    for sid in range(8):
        resp = test_servo(ser, sid)
        if len(resp) >= 6 and (resp[0] == 255 and resp[1] == 255):
            echo_pkt = [255, 255, sid, 2, 1, (~(sid+2+1)) & 0xFF]
            if resp != bytes(echo_pkt):
                found.append((sid, resp.hex()))
                print(f" -> Found servo at ID {sid}! (Response: {resp.hex()})")
    
    if not found:
        print("No servos found at all. Check power and USB connection.")
    else:
        print(f"Total servos found: {len(found)}")
    ser.close()
except Exception as e:
    print(f"Error: {e}")
