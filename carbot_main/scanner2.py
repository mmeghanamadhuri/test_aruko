import serial, time

def test_servo(ser, sid):
    # clear buff
    ser.reset_input_buffer()
    
    # Ping sid
    pkt = [255, 255, sid, 2, 1]
    pkt.append((~sum(pkt[2:])) & 0xFF)
    ser.write(bytes(pkt))
    ser.flush()
    
    start = time.time()
    resp = b""
    while time.time() - start < 0.1:
        if ser.in_waiting:
            resp += ser.read(ser.in_waiting)
        time.sleep(0.001)
    return resp

print("Scanning...")
for baud in [57600, 115200, 1000000, 2000000]:
    try:
        ser = serial.Serial('/dev/ttyUSB0', baud, timeout=0)
        for sid in range(10):  # scan 0 to 9
            resp = test_servo(ser, sid)
            
            # Check if this is an echo or a real response.
            # Real ping response: FF FF ID 02 ERROR_CODE CHECKSUM
            if len(resp) >= 6 and (resp[0] == 255 and resp[1] == 255):
                # Is it an echo?
                # A ping packet sent was FF FF ID 02 01 CHECKSUM
                # Response is FF FF ID 02 ERROR_CODE CHECKSUM
                # Wait, if ERROR_CODE is 0, then the packet is FF FF ID 02 00 CHECKSUM
                echo_pkt = [255, 255, sid, 2, 1, (~(sid+2+1)) & 0xFF]
                if resp == bytes(echo_pkt):
                    continue # It's just a loopback echo
                print(f"Found something at baud {baud}, ID {sid}: {resp.hex()}")
        ser.close()
    except Exception as e:
        print(f"Error at {baud}: {e}")
