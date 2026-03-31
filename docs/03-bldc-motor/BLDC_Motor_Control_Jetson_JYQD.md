EMBEDDED SYSTEMS

**BLDC Motor Control**

NVIDIA Jetson Orin NX 8GB + JYQD V7.3E2 Motor Driver

+-----------------------+-----------------------+-----------------------+
| **CONTROLLER**        | **MOTOR DRIVER**      | **MOTOR**             |
|                       |                       |                       |
| Jetson Orin NX 8GB    | JYQD V7.3E2           | 24V BLDC Hub Motor    |
+-----------------------+-----------------------+-----------------------+

Technical Reference --- Embedded Systems Engineering

Control Interface: PWM + GPIO \| Supply: 24V DC \| Logic Level: 3.3V

**1. Jetson Expansion Header Configuration**

**1.1 Why GPIO Pins Must Be Configured Before Use**

The Jetson Orin NX 8GB SoC is a highly integrated device where every physical pin on the 40-pin expansion header can serve multiple functions. A single physical pin might be wired internally to a UART transmit line, an SPI clock, a PWM output, or a general-purpose GPIO output --- but it can only act as one of these at any time. The SoC uses a hardware multiplexer (pinmux) to route the selected internal function to the physical pin.

At power-on, the hardware configures all I/O pins according to the Device Tree Blob (DTB) that was flashed into the device. The initial default configuration does not enable hardware PWM on the header pins because PWM hardware is a shared resource also used by other subsystems. If software simply writes to the GPIO controller expecting PWM output, no signal will appear because the internal PWM hardware is not electrically connected to that pin until the pinmux is configured to route it there.

This means that before any GPIO or PWM pin is used in this motor control setup, the correct function must be configured in the pinmux and applied via a Device Tree Overlay --- otherwise the pins remain in their reset-state function and the motor driver receives no valid signals.

**1.2 How Jetson Pin Multiplexing Works**

Each pad on the Orin SoC has a dedicated Pad Control Register (PCR) that selects:

-   Function: which internal signal block is connected to this pad (GPIO, PWM, UART, SPI, I2C, etc.)

-   Direction: input or output

-   Pull configuration: pull-up, pull-down, or floating

-   Drive strength: how much current the output driver can source or sink

-   Schmitt trigger: enabled or disabled on inputs

The Device Tree describes each pad\'s desired state as a property set. During boot, the kernel reads the Device Tree and programs all PCRs accordingly. If a pin is assigned to a Special Function I/O (SFIO) such as PWM, the hardware PWM controller\'s output signal is physically routed to the pad. If it is assigned to GPIO, a software-controlled output register drives the pad instead.

+---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Key Concept: Static Configuration at Boot**                                                                                                                                                                                                                                                                                               |
|                                                                                                                                                                                                                                                                                                                                             |
| The Jetson Developer Guide states: \'The configuration of all of the I/O pins on Jetson developer kits is statically defined, and is programmed into the device when it is flashed.\' This means pin functions are not dynamically reconfigured at runtime --- the pinmux must be set in firmware (Device Tree) and takes effect on reboot. |
+---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**1.3 How Expansion Headers Are Configured**

NVIDIA provides two methods for changing pin configuration on developer systems:

Method 1 --- Pinmux Spreadsheet + Flash: For production systems, the engineer updates an official NVIDIA pinmux spreadsheet, regenerates the DTB, and re-flashes the device. This method is appropriate for finalized hardware but is slow and inconvenient during development.

Method 2 --- Jetson-IO Tool: NVIDIA\'s Jetson Expansion Header Tool (Jetson-IO) is a Python script that runs directly on the Jetson. It presents a text-based graphical interface to select pin functions, then generates and applies a Device Tree Overlay (DTBO) without requiring a full reflash. This is the recommended approach for development and is used in the steps below.

**1.4 Step-by-Step: Configuring Pins Using Jetson-IO**

**Step 1 --- Identify the Header Pins**

The 40-pin expansion header on the Jetson Orin NX is labeled J12. The following pins are used in this motor control setup:

  ---------------- ---------------------- ------------------------ ------------------------------
  **Header Pin**   **Default Function**   **Required Function**    **Usage in This Design**

  Pin 7            GPIO (GPIO09)          GPIO Output              Direction control (DIR / ZF)

  Pin 32           GPIO (GPIO07)          GPIO Output or PWM       Signal output (SIG)

  Pin 33           GPIO (GPIO13 / PWM1)   PWM Output               Speed control (PWM → VR)

  Pin 39           GND                    GND (no config needed)   Ground reference

  Pin 40           GPIO (GPIO01)          GPIO Output              Enable signal (EN)
  ---------------- ---------------------- ------------------------ ------------------------------

*Table 1-1: Expansion Header Pin Assignment for BLDC Motor Control*

**Step 2 --- Launch Jetson-IO**

Open a terminal on the Jetson and run the Jetson-IO tool with superuser permissions:

> sudo /opt/nvidia/jetson-io/jetson-io.py

Jetson-IO displays its main screen listing the expansion headers available on the Orin NX. On the Jetson Orin NX, the main screen shows an entry for the 40-pin expansion header.

**Step 3 --- Navigate to the 40-Pin Header**

Select \'Configure Jetson 40pin Header\' from the main menu using the arrow keys and Enter. Jetson-IO displays the header screen, which shows the current configuration of all 40 pins and offers two options:

-   Configure for compatible hardware --- selects a predefined configuration for known hardware modules

-   Configure header pins manually --- lets you manually select individual pin functions

Select \'Configure header pins manually\' to access the expansion header configuration screen.

**Step 4 --- Enable PWM Output on Pin 33**

In the expansion header configuration screen, use the arrow keys to highlight the PWM function. On the Jetson Orin NX, Pin 33 can be assigned to the hardware PWM controller (PWM1, sysfs path: /sys/devices/32c0000.pwm). Press Enter or Space to toggle it on.

Pin 33 when configured as PWM uses PWM chip at /sys/devices/32c0000.pwm, PWM ID 0 within the chip. This is the hardware PWM output that will drive the JYQD V7.3E2 VR (speed control) input.

**Step 5 --- Verify and Save Configuration**

Select \'Back\' to return to the header screen and confirm the new pin states are shown correctly. Then select \'Save and reboot to reconfigure pins\'. Jetson-IO will:

1.  Generate a Device Tree Overlay (.dtbo) file and place it in /boot/

2.  Modify the extlinux.conf bootloader configuration to apply the overlay at boot

3.  Prompt for a reboot

After the reboot, the new pinmux configuration is active.

**Step 6 --- Verify PWM After Reboot**

After rebooting, confirm the PWM subsystem is visible:

> \# List available PWM chips
>
> ls /sys/class/pwm/
>
> \# Expected output includes pwmchip for pin 33:
>
> \# pwmchip0 (or similar --- check against /sys/devices/32c0000.pwm)
>
> \# Export PWM channel 0
>
> echo 0 \> /sys/class/pwm/pwmchip\<N\>/export
>
> \# Set period to 50000 ns (20 kHz)
>
> echo 50000 \> /sys/class/pwm/pwmchip\<N\>/pwm0/period
>
> \# Set duty cycle to 25000 ns (50% duty cycle)
>
> echo 25000 \> /sys/class/pwm/pwmchip\<N\>/pwm0/duty_cycle
>
> \# Enable PWM output
>
> echo 1 \> /sys/class/pwm/pwmchip\<N\>/pwm0/enable

+-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Important: Hardware PWM Only**                                                                                                                                                                                                                                                                      |
|                                                                                                                                                                                                                                                                                                       |
| The Jetson.GPIO library supports PWM only on pins connected to hardware PWM controllers. Software-emulated PWM is not supported. For this design, pin 33 must be used for PWM speed control because it maps to a dedicated hardware PWM peripheral. Pin 32 is used as a GPIO output for the SIG line. |
+-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**2. Hardware Connections**

**2.1 Jetson to JYQD V7.3E2 Wiring**

The Jetson Orin NX communicates with the JYQD V7.3E2 motor driver using five signal lines from its 40-pin expansion header J12. Four are control signals (Enable, PWM speed, direction, and signal output) and one provides the common ground reference.

  -------------------- ----------------- --------------------- ----------------- -----------------------------
  **Jetson J12 Pin**   **Signal Name**   **JYQD V7.3E2 Pin**   **Direction**     **Purpose**

  Pin 40               GPIO01            EN (Enable)           Jetson → Driver   Motor enable / disable

  Pin 39               GND               GND                   Common            Shared ground reference

  Pin 33               PWM1              VR (Speed)            Jetson → Driver   PWM speed control signal

  Pin 32               GPIO07            SIG (Signal)          Jetson → Driver   Speed pulse signal output

  Pin 7                GPIO09            ZF (Direction)        Jetson → Driver   Forward / reverse direction
  -------------------- ----------------- --------------------- ----------------- -----------------------------

*Table 2-1: Complete Wiring from Jetson Expansion Header to JYQD V7.3E2*

**2.2 Signal Roles**

**2.2.1 PWM Speed Control --- Pin 33 → VR**

The VR input on the JYQD V7.3E2 accepts either an analog voltage (0.1V to 5V linear speed regulation) or a PWM signal. According to the JYQD V7.3E2 datasheet: \'Connect with GND when input PWM speed regulation; PWM frequency: 1--20 kHz; Duty cycle 0--100%\'.

In this design, Pin 33 outputs a hardware PWM signal from the Jetson. The PWM duty cycle controls motor speed --- a 0% duty cycle commands zero speed and 100% duty cycle commands full speed. The JYQD board requires the VR pin to be connected to GND through a series path when PWM mode is used; verify the VR input resistance of 20 kΩ (specified in the datasheet) is compatible with the Jetson\'s PWM output driver.

**2.2.2 Direction Control --- Pin 7 → ZF**

The ZF pin controls the motor\'s rotation direction. Per the JYQD V7.3E2 datasheet: \'Connect 5V high level or no connect = Forward direction. Connect 0V low level or connect to GND = Reverse direction.\'

+------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Voltage Level Compatibility Note**                                                                                                                                                                                                                                                                                                                                                                 |
|                                                                                                                                                                                                                                                                                                                                                                                                      |
| The Jetson GPIO logic level is 3.3V (not 5V). \'High\' on Pin 7 (GPIO09) outputs approximately 3.3V. The JYQD V7.3E2 datasheet specifies that the ZF pin interprets a high level as 5V. Verify with the motor driver that 3.3V is recognized as a valid logic high on the ZF pin. If the driver does not reliably detect 3.3V as HIGH, a level shifter (3.3V to 5V) should be inserted on this line. |
+------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**2.2.3 Enable Signal --- Pin 40 → EN**

The EN (EL) pin controls whether the motor driver outputs power to the motor. Per the JYQD V7.3E2 datasheet: \'Connect 5V or no connect to allow operation; connect GND to forbid operation.\' The Jetson drives this pin high (3.3V) to enable the driver, or low (0V / GND) to disable all motor output. This is the primary safety interlock --- setting EN low immediately stops all motor phase drive.

**2.2.4 Ground Reference --- Pin 39 → GND**

Pin 39 on the Jetson expansion header is a GND pin. It must be connected directly to the GND control port on the JYQD V7.3E2 board. This establishes the shared 0V reference for all logic signals (EN, VR, ZF, SIG). Without a common ground between the Jetson and the motor driver\'s control section, all logic levels are undefined.

**2.2.5 Signal Output --- Pin 32 → SIG**

The SIG pin on the JYQD V7.3E2 is described as a \'speed pulse signal output\' in the datasheet --- it is an output from the driver board back to the controller, providing a tachometer pulse proportional to motor speed. In this design, Pin 32 on the Jetson is connected to SIG to read back motor speed. Pin 32 should be configured as a GPIO input in software to count pulses for closed-loop speed feedback if required by the application.

**2.3 Motor Driver to BLDC Motor Connections**

The JYQD V7.3E2 drives the 24V BLDC hub motor through two sets of connections: three-phase power wires and three Hall sensor feedback wires.

  -------------------------- ---------------------- -------------------------- --------------------------------------------------
  **JYQD V7.3E2 Terminal**   **Motor Connection**   **Wire Color (Typical)**   **Purpose**

  MA                         Phase A                Yellow                     Three-phase motor drive --- Phase A

  MB                         Phase B                Blue                       Three-phase motor drive --- Phase B

  MC                         Phase C                Green                      Three-phase motor drive --- Phase C

  Ha                         Hall A                 Yellow (thin)              Hall sensor feedback --- Phase A

  Hb                         Hall B                 Blue (thin)                Hall sensor feedback --- Phase B

  Hc                         Hall C                 Green (thin)               Hall sensor feedback --- Phase C

  GND (Hall)                 Hall GND               Black                      Hall sensor ground return

  5V (Hall)                  Hall 5V                Red                        Hall sensor power supply (5V output from driver)
  -------------------------- ---------------------- -------------------------- --------------------------------------------------

*Table 2-2: JYQD V7.3E2 to BLDC Hub Motor Wiring*

The JYQD V7.3E2 datasheet notes: \'Applicable to hall brushless DC motor with Hall at 120°. Not all manufacturers\' Hall line sequence are corresponding --- you can adjust the Hall line sequence or motor three-phase line sequence according to the actual situation to achieve the best driving effect.\' This means if the motor spins in the wrong direction despite a correct ZF signal, or if commutation is abnormal, the MA/MB/MC or Ha/Hb/Hc wire order should be swapped.

**2.4 Power Supply Connections**

The 24V DC supply connects directly to the JYQD V7.3E2 power terminals. The motor driver\'s control logic and Hall sensor supply are internally regulated from this same supply.

  ---------------------------- -------------------------- --------------------------------------------
  **24V DC Supply Terminal**   **JYQD V7.3E2 Terminal**   **Purpose**

  Positive (+24V)              P+ (DC+)                   Motor drive supply positive

  Negative (0V)                P- (DC-)                   Motor drive supply negative / power ground
  ---------------------------- -------------------------- --------------------------------------------

*Table 2-3: 24V DC Power Supply Connection*

+-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Critical: Power Ground vs. Signal Ground**                                                                                                                                                                                                                                                                                                                            |
|                                                                                                                                                                                                                                                                                                                                                                         |
| The P- terminal is the power return for the 24V motor supply. The GND on the control port is the logic ground for control signals. These are internally connected on the JYQD V7.3E2 board. Do NOT connect the Jetson\'s 3.3V logic ground directly to the P- power terminal of the 24V supply --- always connect the Jetson GND only to the control port GND terminal. |
+-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**3. Motor Driver Overview --- JYQD V7.3E2**

**3.1 Purpose**

The JYQD V7.3E2 is a brushless DC (BLDC) motor driver board produced by Shanghai Juyi Electronic Technology Development Co., Ltd. Its purpose is to receive low-power control signals from a microcontroller or SBC (such as the Jetson) and drive a three-phase BLDC motor with high-current, high-voltage switched output. The driver handles all commutation logic internally using Hall sensor feedback, relieving the host controller of the need to implement three-phase commutation sequences.

**3.2 Electrical Specifications**

The following specifications are sourced directly from the JYQD V7.3E2 datasheet:

  ------------------------------ ----------------------------------------
  **Parameter**                  **Value**

  Model                          JYQD-V7.3E2

  Operating Voltage              12V -- 36V DC

  Maximum Current                16A

  Continuous Operating Current   15A

  Operating Temperature          -20°C to +85°C

  PWM Frequency (speed ctrl)     1 -- 20 kHz

  Analog Speed Range (VR)        0.1V -- 5V (linear)

  PWM Duty Cycle (speed ctrl)    0% -- 100%

  VR Input Resistance            20 kΩ

  Speed Pulse Output             Yes (SIG pin)

  Hall Sensor Type               120° Hall brushless DC motor
  ------------------------------ ----------------------------------------

*Table 3-1: JYQD V7.3E2 Electrical Specifications (Source: JYQD V7.3E2 Datasheet)*

The driver board is supplied without a housing or heatsink. Per the datasheet: \'It can drive the motor below 100 watts without any heatsink, but needs normal ventilation.\' For motors approaching or exceeding 100W continuous, an external heatsink must be fitted to the driver MOSFETs.

**3.3 Control Modes**

The JYQD V7.3E2 supports two speed control modes on the VR input:

-   PWM Mode: A PWM signal is applied to the VR pin. Per the datasheet, \'Connect with GND when input PWM speed regulation.\' This means the VR pin must be AC-coupled or the driver circuit must be designed for PWM input. The PWM frequency may be set between 1 kHz and 20 kHz. Duty cycle from 0--100% maps linearly to motor speed from stopped to maximum.

-   Analog Voltage Mode: A DC analog voltage between 0.1V and 5V is applied to the VR pin for continuous, smooth speed control. An external potentiometer connected between the on-board 5V output and GND can be used for manual speed adjustment.

In this Jetson-based design, PWM mode is used because the Jetson hardware PWM peripheral (on Pin 33) generates a clean digital PWM signal.

**3.4 Control Pin Reference**

All control pins are on a 2.54 mm pitch connector on the driver board. Their functions per the JYQD V7.3E2 datasheet are:

  -------------- --------------------- ---------------------------------------------------------------------------------------------------------------------
  **Pin Name**   **Datasheet Label**   **Function**

  5V             5V                    Internal 5V output. For external potentiometer or switch only. Do NOT connect external power equipment to this pin.

  ZF             Z/F                   Direction control. Connect 5V (high) or leave unconnected = Forward. Connect 0V (GND) = Reverse.

  VR             VR                    Speed control. Analog 0.1V--5V or PWM (1--20 kHz, 0--100% duty cycle). Connect VR to GND when using PWM mode.

  EL             EL                    Enable control. Connect 5V or leave unconnected = motor enabled. Connect GND = motor disabled.

  SIG            Signal                Speed pulse output from driver to controller. Provides tachometer feedback proportional to motor RPM.

  GND            GND                   Logic ground reference for all control signals.
  -------------- --------------------- ---------------------------------------------------------------------------------------------------------------------

*Table 3-2: JYQD V7.3E2 Control Pin Descriptions (Source: JYQD V7.3E2 Datasheet)*

**3.5 How the Driver Interprets Jetson Signals**

The Jetson\'s 3.3V GPIO and PWM signals interact with the motor driver control pins as follows:

-   EN (EL pin): Jetson Pin 40 drives this pin. When the Jetson asserts GPIO HIGH (3.3V), the driver interprets this as an enabled state and allows motor operation. GPIO LOW (0V) disables all motor output immediately.

-   VR pin (PWM speed): Jetson Pin 33 outputs a hardware PWM signal. The duty cycle of this signal determines motor speed. At 0% duty cycle, the motor is commanded to stop. At 100% duty cycle, the motor runs at the maximum speed permitted by the supply voltage and motor characteristics.

-   ZF pin (direction): Jetson Pin 7 drives this pin as a GPIO output. HIGH (3.3V from Jetson) = Forward rotation. LOW (0V) = Reverse rotation. The direction can only be changed reliably when the motor is stopped or at very low speed to avoid abrupt commutation reversal.

-   SIG pin (speed feedback): The driver outputs a tachometer pulse on this pin. Jetson Pin 32 reads this signal as a GPIO input. The host software can count rising edges to compute actual motor RPM for closed-loop speed control.

**4. Motor Control Logic**

**4.1 Direction Control --- Forward and Reverse**

The JYQD V7.3E2 uses the ZF (Z/F) pin to determine motor rotation direction. The logic is directly defined in the datasheet:

-   Forward: ZF = HIGH (5V or unconnected). In this design, Jetson Pin 7 asserts 3.3V logic HIGH.

-   Reverse: ZF = LOW (0V or GND). Jetson Pin 7 asserts 0V logic LOW.

When the ZF pin state changes, the motor driver reverses the commutation sequence applied to the phase outputs (MA, MB, MC). The Hall sensors (Ha, Hb, Hc) continuously feed back rotor position so the driver knows when to switch phases. In the forward direction, the driver energizes phases in one rotational sequence; in reverse, it energizes them in the opposite sequence.

+------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Direction Change Recommendation**                                                                                                                                                                                                                                                                                                |
|                                                                                                                                                                                                                                                                                                                                    |
| Do not change direction while the motor is spinning at high speed. The motor driver will attempt to reverse commutation immediately, causing large current spikes and potential motor damage or driver shutdown. Always ramp the speed down to near zero (set PWM duty cycle close to 0%) before changing the ZF direction signal. |
+------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**4.2 Speed Control via PWM**

Motor speed is controlled by the duty cycle of the PWM signal on the VR pin. The JYQD V7.3E2 datasheet specifies the PWM frequency range as 1--20 kHz and duty cycle range as 0--100%.

-   0% duty cycle → Motor commanded to minimum / stop speed

-   50% duty cycle → Approximately half of maximum speed

-   100% duty cycle → Maximum speed (limited by supply voltage and motor KV rating)

The hardware PWM peripheral on the Jetson (connected via Pin 33) generates the PWM waveform. To set a specific speed, the application software writes the desired duty cycle to the PWM sysfs interface or uses a GPIO library with PWM support.

Example using sysfs --- setting motor to approximately 30% speed with 20 kHz PWM:

> PWMCHIP=/sys/class/pwm/pwmchip\<N\> \# Replace \<N\> with actual chip number
>
> \# Set period: 50000 ns = 20 kHz
>
> echo 50000 \> \$PWMCHIP/pwm0/period
>
> \# Set duty cycle: 30% of 50000 ns = 15000 ns
>
> echo 15000 \> \$PWMCHIP/pwm0/duty_cycle
>
> \# Enable PWM output
>
> echo 1 \> \$PWMCHIP/pwm0/enable

Example using Python with Jetson.GPIO library for GPIO direction and enable control:

> import Jetson.GPIO as GPIO
>
> DIR_PIN = 7 \# Pin 7 → ZF (direction)
>
> ENABLE_PIN = 40 \# Pin 40 → EN (enable)
>
> GPIO.setmode(GPIO.BOARD)
>
> GPIO.setup(DIR_PIN, GPIO.OUT, initial=GPIO.HIGH) \# Forward
>
> GPIO.setup(ENABLE_PIN, GPIO.OUT, initial=GPIO.LOW) \# Disabled initially
>
> \# Enable the motor driver
>
> GPIO.output(ENABLE_PIN, GPIO.HIGH)
>
> \# Set direction to Forward
>
> GPIO.output(DIR_PIN, GPIO.HIGH)
>
> \# PWM speed is handled separately via sysfs (see above)

**4.3 Control Flow Diagram**

The complete control signal path from the Jetson to the motor is shown below:

+-----------------------------------------------------------------------+
| **Jetson Orin NX 8GB**                                                |
|                                                                       |
| Application software running in Linux user space                      |
+-----------------------------------------------------------------------+

▼

+-----------------------------------------------------------------------+
| **Linux PWM Subsystem (sysfs / Jetson.GPIO)**                         |
|                                                                       |
| PWM duty cycle → speed \| GPIO outputs → direction and enable         |
+-----------------------------------------------------------------------+

▼

+-------------------------------------------------------------------------------+
| **40-Pin Expansion Header J12**                                               |
|                                                                               |
| Pin 33 (PWM) \| Pin 7 (DIR) \| Pin 40 (EN) \| Pin 39 (GND) \| Pin 32 (SIG in) |
+-------------------------------------------------------------------------------+

▼

+----------------------------------------------------------------------------+
| **JYQD V7.3E2 Motor Driver**                                               |
|                                                                            |
| VR (speed) \| ZF (direction) \| EL (enable) \| GND \| SIG (tachometer out) |
+----------------------------------------------------------------------------+

▼

+--------------------------------------------------------------------------+
| **Hall Sensor Feedback (Ha / Hb / Hc)**                                  |
|                                                                          |
| Rotor position feedback to driver --- enables correct commutation timing |
+--------------------------------------------------------------------------+

▼

+-----------------------------------------------------------------------+
| **Three-Phase BLDC Drive (MA / MB / MC)**                             |
|                                                                       |
| High-current switched phase outputs to motor windings                 |
+-----------------------------------------------------------------------+

▼

+-----------------------------------------------------------------------+
| **24V BLDC Hub Motor**                                                |
|                                                                       |
| Physical rotation --- direction and speed as commanded                |
+-----------------------------------------------------------------------+

*Figure 4-1: Complete Motor Control Signal Flow*

**5. Complete System Operation**

This section describes the full operational sequence from initial setup through active motor control.

**Step 1 --- Configure Jetson Header Pins**

Before connecting any hardware, configure the Jetson expansion header using Jetson-IO as described in Section 1:

4.  Run sudo /opt/nvidia/jetson-io/jetson-io.py

5.  Select \'Configure Jetson 40pin Header\'

6.  Select \'Configure header pins manually\'

7.  Enable PWM function on Pin 33 (PWM1 / /sys/devices/32c0000.pwm)

8.  Save and reboot to reconfigure pins

9.  After reboot, verify /sys/class/pwm/ contains the expected PWM chip

**Step 2 --- Connect Hardware**

With the Jetson powered off and the 24V supply disconnected:

10. Connect Jetson Pin 39 (GND) to JYQD GND control terminal --- establish common ground first

11. Connect Jetson Pin 40 to JYQD EL (Enable) terminal

12. Connect Jetson Pin 33 to JYQD VR (Speed) terminal

13. Connect Jetson Pin 32 to JYQD SIG terminal

14. Connect Jetson Pin 7 to JYQD ZF (Direction) terminal

15. Connect motor Phase wires to MA / MB / MC on the JYQD board

16. Connect motor Hall sensor wires to Ha / Hb / Hc, 5V, and GND on the JYQD board

17. Connect 24V DC supply to JYQD P+ and P- power terminals

**Step 3 --- Generate PWM and Direction Signals**

Power on the Jetson. In software:

18. Initialize the GPIO library and configure Pin 7 (DIR) and Pin 40 (EN) as outputs

19. Set Pin 40 LOW (EN = disabled) initially --- keep motor disabled until ready

20. Set Pin 7 HIGH or LOW to select the desired direction (HIGH = Forward)

21. Export and configure the PWM channel on Pin 33 with the desired period and duty cycle

22. Power on the 24V supply to the motor driver

23. Set Pin 40 HIGH (EN = enabled) to allow the motor driver to operate

24. Set the PWM duty cycle to the desired speed value --- motor begins to run

**Step 4 --- Motor Driver Interprets Signals**

Once the Jetson is actively driving the control lines, the JYQD V7.3E2 continuously:

25. Monitors the EL pin --- if LOW, all phase outputs are inhibited immediately

26. Reads the ZF pin state to determine the commanded rotation direction

27. Reads the VR pin PWM duty cycle to determine commanded speed

28. Reads Hall sensor inputs (Ha, Hb, Hc) to determine actual rotor position

29. Energizes the appropriate motor phase (MA, MB, or MC) at the correct moment based on Hall position and direction command

30. Outputs a tachometer pulse on the SIG pin proportional to motor speed

**Step 5 --- Motor Rotates Forward or Reverse**

The BLDC hub motor responds to the commutated three-phase drive signal:

-   Forward: ZF = HIGH on Jetson Pin 7. The driver sequences phases MA → MB → MC in the forward commutation order. Motor rotates in the forward direction at the speed determined by VR duty cycle.

-   Reverse: ZF = LOW on Jetson Pin 7. The driver reverses the phase commutation sequence. Motor rotates in the opposite direction at the speed determined by VR duty cycle.

-   Speed: At 100% PWM duty cycle on Pin 33, the motor runs at maximum speed. Reducing duty cycle proportionally reduces speed. The motor coasts when duty cycle reaches 0% (the driver does not apply active braking in the default configuration).

**5.1 Speed Control Loop (Optional)**

The SIG pin from the JYQD V7.3E2 outputs a speed pulse proportional to motor RPM. If the application requires precise speed control, the Jetson can implement a software PID loop:

31. Read SIG pulses on Pin 32 using a GPIO interrupt handler or pulse counting timer

32. Calculate actual motor RPM from pulse frequency

33. Compare actual RPM to target RPM

34. Adjust PWM duty cycle on Pin 33 to reduce the error

**6. Safety Considerations**

**6.1 Voltage Level Compatibility --- 3.3V Logic**

The Jetson Orin NX expansion header GPIO pins operate at 3.3V logic levels. The JYQD V7.3E2 control pins (ZF, EL) reference 5V as their high-level specification. This creates a potential logic-level incompatibility:

-   EL (Enable): The datasheet states \'connect 5V to allow operation.\' A 3.3V HIGH from the Jetson may be interpreted as valid depending on the input threshold of the driver\'s logic circuit. If the motor fails to enable with 3.3V, a 3.3V-to-5V level shifter (such as a BSS138-based bidirectional translator or a 74HC logic buffer) must be placed between Jetson Pin 40 and the EL pin.

-   ZF (Direction): Similarly specified as a 5V logic input. Apply the same level-shifting consideration.

-   VR (PWM Speed): The VR pin accepts analog voltages up to 5V and PWM signals. A 3.3V-amplitude PWM signal has a lower effective voltage range but will still be a valid PWM input. The duty cycle response from 0--100% remains valid even with a 3.3V amplitude signal.

+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Recommended Practice**                                                                                                                                                                                                                                                                            |
|                                                                                                                                                                                                                                                                                                     |
| To avoid uncertainty over 3.3V logic compatibility, insert 3.3V → 5V level shifters on all Jetson GPIO outputs (EN, DIR) connected to the JYQD V7.3E2 control inputs. Use a dedicated level shifter IC (e.g., TXS0102, BSS138 + pull-up to 5V, or similar) rather than relying on voltage dividers. |
+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**6.2 Common Ground Requirement**

The Jetson\'s expansion header GND (Pin 39) must be connected to the JYQD V7.3E2 control GND terminal. Without this connection, all logic levels from the Jetson are referenced to an unknown potential and the motor driver control inputs receive undefined signals.

The power supply negative terminal (connected to JYQD P-) is also a ground, but it carries high motor current. Although these grounds are internally connected on the JYQD board, the Jetson signal ground wire must go to the dedicated control GND terminal --- not directly to the P- terminal or the 24V supply negative lead. Running large motor currents through the Jetson\'s signal ground path could cause ground voltage shifts that corrupt GPIO logic levels.

**6.3 Protecting Jetson GPIO Pins**

The Jetson GPIO pins have strict absolute maximum ratings. To avoid permanent damage:

-   Never apply voltages above 3.3V to any GPIO pin, even momentarily. The 5V rail on Pin 2/4 of the expansion header is NOT compatible with GPIO inputs.

-   Never connect the JYQD 5V output pin directly to any Jetson GPIO input. The JYQD board\'s 5V output is intended only for external potentiometers or switches.

-   The SIG tachometer output from the JYQD board is an output signal whose voltage level may be up to 5V. If Pin 32 is configured as a GPIO input, verify the SIG output voltage. If it is 5V, a voltage divider or level shifter must be placed between SIG and Jetson Pin 32 to bring the signal to 3.3V maximum.

-   Configure Pin 32 as a GPIO input (not output) to read the SIG signal. Driving an output into another output would damage both devices.

+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **GPIO Maximum Ratings**                                                                                                                                                                                                                                     |
|                                                                                                                                                                                                                                                              |
| Jetson GPIO pads are specified for 3.3V maximum input voltage. Applying 5V to a 3.3V GPIO pin can permanently damage the Orin SoC. Always verify signal voltage levels with a multimeter before making connections between the Jetson and external hardware. |
+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**6.4 Safe Power Wiring for the 24V Motor Supply**

The 24V DC supply connects to the JYQD V7.3E2 at high current. The following precautions apply:

-   Use appropriately rated wire gauge for the 24V supply leads and motor phase wires. The JYQD V7.3E2 supports up to 15A continuous. At 15A and 24V, supply wires must be rated for at least 16A (AWG 14 or heavier for longer runs).

-   Always power the Jetson first and configure all GPIO outputs to safe states (EN = LOW, motor disabled) before applying 24V to the motor driver. This prevents an uncontrolled motor start at power-on.

-   Keep the 24V supply negative (P-) and positive (P+) wires short and away from the Jetson signal cables to minimize electromagnetic interference that could corrupt PWM and GPIO signals.

-   Per the JYQD V7.3E2 datasheet: \'Use shielded wires if the driver board has more than 50 cm distance from the motor, otherwise it may lead to abnormal driving.\' For longer motor cable runs, use shielded three-phase cable for the MA/MB/MC connections.

-   If the motor driver or motor is to be enclosed, ensure adequate ventilation. The JYQD V7.3E2 requires \'normal ventilation\' and will reach elevated temperatures under continuous load.

-   Add a fuse or circuit breaker in series with the 24V positive supply, rated close to the motor\'s maximum operating current, to protect the wiring in the event of a motor stall or driver fault.

**6.5 Summary Safety Checklist**

  --------------------------- ------------------------------------------------------- ------------------------
  **Item**                    **Requirement**                                         **Status**

  Logic level on EN / ZF      Verify 3.3V is accepted as HIGH, or add level shifter   Verify before power-on

  SIG pin voltage             Measure SIG output voltage; add divider if \>3.3V       Verify before power-on

  Common ground               Jetson GND (Pin 39) connected to JYQD control GND       Required

  EN = LOW at power-on        Motor disabled until software explicitly enables        Required

  Supply fuse                 Fuse in series with 24V P+ supply                       Recommended

  Wire gauge (motor phases)   Rated for continuous motor current                      Required

  Motor cable shielding       Shielded cable if motor \>50 cm from driver             Per datasheet

  Driver ventilation          Heatsink or airflow for loads approaching 100W+         Per datasheet
  --------------------------- ------------------------------------------------------- ------------------------

*Table 6-1: Safety Checklist Before First Power-On*

**6.6 Sources**

-   NVIDIA Jetson Linux Developer Guide r36 --- Configuring the Jetson Expansion Headers: https://docs.nvidia.com/jetson/archives/r36.4.3/DeveloperGuide/HR/ConfiguringTheJetsonExpansionHeaders.html

-   JYQD V7.3E2 Brushless DC Motor Driver Board Datasheet --- Shanghai Juyi Electronic Technology Development Co., Ltd: https://www.laskakit.cz/user/related_files/jyqd-v7_3e2-english.pdf

-   NVIDIA/jetson-gpio Python library --- PWM and GPIO configuration notes: https://github.com/NVIDIA/jetson-gpio

-   JetsonHacks --- Jetson Orin Nano GPIO Header Pinout (PWM sysfs paths): https://jetsonhacks.com/nvidia-jetson-orin-nano-gpio-header-pinout/

**7. Python Motor Control Scripts**

Three Python scripts implement the complete motor control logic using the Jetson.GPIO library. Together they form a simple but safe layered architecture: a top-level orchestrator (motor_control.py) repeatedly invokes two direction-specific workers (move_forward.py and move_reverse.py) as subprocess calls. This separation ensures GPIO state is fully cleaned up between direction changes by the operating system itself --- not just by software cleanup routines.

**7.1 Script Architecture Overview**

  ------------------ ------------------------------------------------ ----------------------------------------
  **Script**         **Role**                                         **GPIO Responsibility**

  motor_control.py   Orchestrator --- runs the forward/reverse loop   None --- does not touch GPIO directly

  move_forward.py    Worker --- runs motor in forward direction       Full GPIO init, PWM ramp, safe cleanup

  move_reverse.py    Worker --- runs motor in reverse direction       Full GPIO init, PWM ramp, safe cleanup
  ------------------ ------------------------------------------------ ----------------------------------------

*Table 7-1: Script Roles and GPIO Responsibilities*

**7.2 motor_control.py --- Orchestrator**

This script is the entry point. It runs an infinite forward → reverse loop, launching each direction script as a child process via subprocess.run(). It does not import or configure GPIO directly.

> \# motor_control.py
>
> import subprocess, sys, time
>
> FORWARD_SCRIPT = \"move_forward.py\"
>
> REVERSE_SCRIPT = \"move_reverse.py\"
>
> def run_motor(script):
>
> print(f\"Launching: {script}\")
>
> result = subprocess.run(
>
> \[sys.executable, script\],
>
> timeout=30 \# safety cutoff --- adjust to run duration + margin
>
> )
>
> if result.returncode != 0:
>
> print(f\"WARNING: {script} exited with code {result.returncode}\")
>
> time.sleep(1) \# brief pause between direction changes
>
> try:
>
> while True:
>
> run_motor(FORWARD_SCRIPT)
>
> run_motor(REVERSE_SCRIPT)
>
> except KeyboardInterrupt:
>
> print(\"Controller stopped\")

Key design decisions in this script:

-   subprocess.run() with timeout=30: The subprocess call blocks until the child script finishes or the timeout expires. This acts as a hardware safety cutoff --- if a direction script hangs (motor stall, GPIO deadlock), the orchestrator terminates it after 30 seconds and continues. Adjust the timeout to match your intended run duration plus a safety margin.

-   time.sleep(1) between scripts: Adds a 1-second pause between the end of one direction and the start of the next. This gives the motor time to coast to a stop before the direction pin is changed, preventing abrupt commutation reversal under load.

-   returncode check: If a direction script exits with a non-zero return code (indicating an unhandled error), a warning is printed but the loop continues. Depending on application requirements, this could be changed to a hard stop.

**7.3 move_forward.py --- Forward Direction Worker**

This script configures all required GPIO pins, starts PWM, ramps the motor up to full speed, runs for a defined duration, then ramps back down and performs a complete GPIO cleanup before exiting. The direction pin (Pin 7 / ZF) is set HIGH for forward rotation.

> \# move_forward.py --- key sections
>
> \# Pin assignments (BOARD numbering)
>
> PWM_PIN = 33 \# Pin 33 → VR (hardware PWM speed control)
>
> DIR_PIN = 7 \# Pin 7 → ZF (direction: HIGH = forward)
>
> SIG_PIN = 32 \# Pin 32 → SIG (signal output)
>
> EN_PIN = 40 \# Pin 40 → EN (enable: HIGH = motor on)
>
> \# Safe initial state --- EN LOW, motor disabled before DIR is set
>
> GPIO.output(EN_PIN, GPIO.LOW)
>
> GPIO.output(DIR_PIN, GPIO.HIGH) \# FORWARD direction locked at init
>
> pwm = GPIO.PWM(PWM_PIN, 1000) \# 1 kHz PWM frequency
>
> pwm.start(0) \# Start at 0% duty cycle
>
> time.sleep(0.3) \# Allow DIR pin to latch on driver
>
> GPIO.output(EN_PIN, GPIO.HIGH) \# Enable motor driver
>
> time.sleep(0.2) \# Allow driver to initialize
>
> \# Ramp up: 0% → 100% in 5% steps, \~30 ms per step (\~630 ms total)
>
> for dc in range(0, 101, 5):
>
> pwm.ChangeDutyCycle(dc)
>
> time.sleep(0.03)
>
> time.sleep(5) \# Run at full speed for 5 seconds
>
> \# Ramp down: 100% → 0% in 5% steps
>
> for dc in range(100, -1, -5):
>
> pwm.ChangeDutyCycle(dc)
>
> time.sleep(0.03)

**7.4 move_reverse.py --- Reverse Direction Worker**

Identical in structure to move_forward.py with one critical difference: the direction pin (Pin 7 / ZF) is set LOW instead of HIGH at initialization, commanding reverse rotation from the JYQD V7.3E2.

> \# move_reverse.py --- direction difference only
>
> \# All pin assignments and ramp logic identical to move_forward.py
>
> \# Only this line differs:
>
> GPIO.output(DIR_PIN, GPIO.LOW) \# REVERSE direction --- ZF = LOW on JYQD
>
> \# Everything else (PWM ramp, run duration, cleanup) is identical

**7.5 GPIO Sequence --- Signal Timing**

Both direction scripts follow a deliberate signal ordering to ensure safe startup and shutdown. The sequence is:

  ---------- -------------- ------------ ---------------------------------------------
  **Step**   **Signal**     **State**    **Purpose**

  1          EN (Pin 40)    LOW          Disable driver before configuring direction

  2          DIR (Pin 7)    HIGH / LOW   Set direction while driver is disabled

  3          PWM (Pin 33)   0% duty      Start PWM at zero speed

  4          Wait 300 ms    ---          Allow direction pin to latch on JYQD driver

  5          EN (Pin 40)    HIGH         Enable motor driver

  6          Wait 200 ms    ---          Allow driver to stabilize after enable

  7          PWM (Pin 33)   0→100%       Ramp up speed over \~630 ms

  8          PWM (Pin 33)   100%         Run at full speed (5 seconds default)

  9          PWM (Pin 33)   100→0%       Ramp down speed over \~630 ms

  10         EN (Pin 40)    LOW          Disable driver

  11         GPIO cleanup   ---          Release all GPIO resources before exit
  ---------- -------------- ------------ ---------------------------------------------

*Table 7-2: GPIO Signal Sequence in move_forward.py and move_reverse.py*

**7.6 Cleanup and Error Handling**

Both direction scripts use a try/finally block to guarantee cleanup runs even if an exception occurs mid-run. The finally block executes these steps in order:

35. pwm.ChangeDutyCycle(0) --- zero the duty cycle before stopping PWM

36. pwm.stop() --- stop the PWM generator

37. GPIO.output(EN_PIN, GPIO.LOW) --- disable the motor driver

38. GPIO.output(SIG_PIN, GPIO.HIGH) --- set SIG pin to its idle high state

39. GPIO.cleanup() --- release all GPIO resources

The GPIO.cleanup() call is wrapped in an OSError catch. This handles a known bug in Jetson.GPIO 2.1.x where the file descriptor for a GPIO export may already be closed at cleanup time, raising an OSError that would otherwise mask a clean exit. The workaround is documented in the script comments.

+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Why subprocess.run() Instead of Direct Function Calls?**                                                                                                                                                                                                                                                                                                                                                            |
|                                                                                                                                                                                                                                                                                                                                                                                                                       |
| Running each direction script as a separate subprocess guarantees that the kernel fully releases all GPIO file descriptors when the child process exits --- regardless of whether GPIO.cleanup() succeeded inside the script. This is particularly important on Jetson platforms where the Jetson.GPIO library manages sysfs file descriptors that can persist across calls within the same process if cleanup fails. |
+-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

**7.7 Running the Scripts**

To run the complete forward/reverse motor cycle:

> \# Ensure Jetson-IO has been run and Pin 33 is configured as PWM
>
> \# Ensure the 24V supply is connected and the hardware wiring is verified
>
> \# Run the orchestrator (Ctrl+C to stop)
>
> sudo python3 motor_control.py

To run a single direction manually for testing:

> \# Test forward direction only
>
> sudo python3 move_forward.py
>
> \# Test reverse direction only
>
> sudo python3 move_reverse.py

+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **sudo Required**                                                                                                                                                                                                              |
|                                                                                                                                                                                                                                |
| GPIO access on the Jetson requires root privileges. Both the orchestrator and the direction scripts must be run with sudo, or the user must be added to the gpio group (sudo usermod -aG gpio \$USER) and the system rebooted. |
+--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
