# Ethernet Setup Guide for Carbot Motion Server

This robotic motion server is hosted on the Jetson NX and communicates over a direct Point-to-Point (P2P) Ethernet connection. 

The Jetson NX has been configured with the static IP address **`192.168.99.1`**. The motion server listens for TCP JSON connections on **port `5000`**.

To connect a laptop to this system, you must configure the laptop's wired network interface to utilize a static IP address on the same `192.168.99.x` subnet.

---

### Step 1: Physical Connection
Connect a Cat5e/Cat6 Ethernet cable directly from your laptop's Ethernet port (or USB-to-Ethernet adapter) to the Jetson NX's Ethernet port. 

> [!NOTE] 
> Because this is a direct connection with no DHCP server (router) in between, your laptop will likely label the connection as an "Unidentified Network". This is expected.

---

### Step 2: Configure Laptop Network Settings (Static IP)

You will need to manually set a static IP for your laptop's wired adapter.

#### For Windows:
1. Open the **Control Panel** and go to **Network and Sharing Center**. (Alternatively, press `Win + R`, type `ncpa.cpl` and hit enter).
2. Right-click your Ethernet connection adapter and select **Properties**.
3. Select **Internet Protocol Version 4 (TCP/IPv4)** and click the **Properties** button.
4. Select the radio button for **"Use the following IP address"** and enter:
   - **IP address**: `192.168.99.2` *(Any address from .2 to .254 is fine)*
   - **Subnet mask**: `255.255.255.0`
   - **Default gateway**: leave blank
5. Click **OK** on both windows to apply the settings.

#### For macOS:
1. Open **System Settings**.
2. Navigate to **Network** and select your Ethernet/USB LAN interface.
3. Click on the **Details...** button.
4. Navigate to the **TCP/IP** tab.
5. Next to **"Configure IPv4"**, change the dropdown to **Manually**.
6. Enter the following details:
   - **IP Address**: `192.168.99.2`
   - **Subnet Mask**: `255.255.255.0`
   - **Router**: leave blank
7. Click **OK** and then **Apply**.

#### For Linux (Ubuntu/Debian via GUI):
1. Open your system **Settings** and go to **Network**.
2. Next to your Wired connection, click the **Settings (gear) icon**.
3. Go to the **IPv4** tab.
4. Change the IPv4 Method to **Manual**.
5. In the Addresses section, add:
   - **Address**: `192.168.99.2`
   - **Netmask**: `255.255.255.0`
   - **Gateway**: leave blank
6. Click **Apply**.

*(For Linux CLI, you can use `nmcli`: `sudo nmcli con add type ethernet ifname eth0 con-name "Jetson-Direct" ipv4.method manual ipv4.addresses 192.168.99.2/24`)*

---

### Step 3: Verify the Connection

To confirm that the physical connection and IP configuration are correct, test the network connectivity by pinging the Jetson NX.

1. Open your terminal or command prompt.
2. Run the ping command:
   ```bash
   ping 192.168.99.1
   ```
3. You should see successful replies (e.g., `Reply from 192.168.99.1: bytes=32 time<1ms TTL=64`). If you see "Request timed out" or "Destination host unreachable," double-check your IP configuration.

---

### Step 4: Interacting with the Motion Server

Once network communication is verified, you can interface with the robotic motion server on **TCP port 5000**.

For example, to test simple commands manually, you can use `netcat` (`nc`) or `telnet` from your laptop terminal:

```bash
nc 192.168.99.1 5000
```

Once connected, you can send raw JSON commands directly to the robot. Press `Enter` to send the command string:
```json
{"cmd": "status"}
```

You should receive a JSON response containing the current state and servo positions. You can now use your custom Python, Node, or frontend interface to stream standard commands (like `play`, `record`, and `servo_move`) to `192.168.99.1:5000`.
