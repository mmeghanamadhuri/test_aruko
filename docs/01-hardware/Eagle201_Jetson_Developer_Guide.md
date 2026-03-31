**Eagle 201 + Jetson Orin NX**

**Hardware Developer Reference**

*JetPack R36.5 · Tegra234 · Ubuntu 22.04 · aarch64*

Generated: Mon Mar 30 2026

**1. Board Identity & Discovery**

The Eagle 201 carrier board from Tanna Techbiz has no public datasheet. It mounts the NVIDIA Jetson Orin NX 8GB module and boots using the NVIDIA Jetson Orin NX Engineering Reference Devkit device tree (p3768-0000+p3767-0001). This means pinmux configuration in the DTB describes devkit-compatible signal routing. The physical connector positions on the Eagle 201 header may differ --- always verify with a multimeter before connecting hardware for the first time.

**1.1 Confirmed Identity**

  ----------------------- -----------------------------------------------------------
  **JetPack version**     R36.5 (JetPack 6.x)

  **Linux kernel**        5.15.185-tegra (OOT variant)

  **OS**                  Ubuntu 22.04 LTS (aarch64)

  **SoC**                 Tegra234 (Cortex-A78AE, ARMv8.2-A)

  **DTB in use**          tegra234-p3768-0000+p3767-0001-nv.dtb

  **Board model (DT)**    NVIDIA Jetson Orin NX Engineering Reference Developer Kit

  **Compatible string**   nvidia,p3768-0000+p3767-0001 / nvidia,tegra234

  **GPIO chip 0**         tegra234-gpio --- 164 lines (gpiochip0)

  **GPIO chip 1**         tegra234-gpio-aon --- 32 lines (gpiochip1)

  **Built-in UARTs**      /dev/ttyTHS1, /dev/ttyTHS2 (confirmed present at boot)
  ----------------------- -----------------------------------------------------------

**2. 40-Pin Expansion Header**

  ---------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **NOTE**   Pin mapping derived from tegra234-p3767-0000+p3509-a02-hdr40.dtbo and DTB aliases. The Eagle 201 uses the same Tegra234 SoC signal routing as the NVIDIA Orin NX devkit. Verify physical pin positions on the Eagle 201 connector with a multimeter before first use --- Tanna Techbiz may have repositioned the connector relative to the devkit layout.

  ---------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**2.1 Signal type legend**

  --------- --------- --------- ---------- --------- --------- ---------- --------- ---------
  **PWR**   **GND**   **I2C**   **UART**   **SPI**   **I2S**   **GPIO**   **CLK**   **PWM**

  --------- --------- --------- ---------- --------- --------- ---------- --------- ---------

**2.2 Complete pin reference**

  ------------ ---------- ------------------- ------------------- --------------- ---------- ---------------------------------------
  **Pin \#**   **Type**   **Function**        **SoC pad**         **GPIO chip**   **Port**   **Notes**

  **1**        **PWR**    3.3V                ---                 ---             ---        *3.3V power rail*

  **2**        **PWR**    5V                  ---                 ---             ---        *5V power rail*

  **3**        **I2C**    I2C_SDA (i2c8)      gen8_i2c_sda_pdd2   gpiochip1       PDD.02     *i2c-7 = hdr40_i2c1 (c250000)*

  **4**        **PWR**    5V                  ---                 ---             ---        *5V power rail*

  **5**        **I2C**    I2C_SCL (i2c8)      gen8_i2c_scl_pdd1   gpiochip1       PDD.01     *i2c-7 = hdr40_i2c1 (c250000)*

  **6**        **GND**    GND                 ---                 ---             ---        *Ground*

  **7**        **CLK**    AUD_MCLK            soc_gpio59_pac6     gpiochip1       PAC.06     *Audio master clock*

  **8**        **UART**   UART1_TX            uart1_tx_pr2        gpiochip0       PR.02      *ttyTHS1 TX (serial@3100000 = uarta)*

  **9**        **GND**    GND                 ---                 ---             ---        *Ground*

  **10**       **UART**   UART1_RX            uart1_rx_pr3        gpiochip0       PR.03      *ttyTHS1 RX*

  **11**       **UART**   UART1_RTS           uart1_rts_pr4       gpiochip0       PR.04      *ttyTHS1 RTS*

  **12**       **I2S**    I2S2_SCLK           soc_gpio41_ph7      gpiochip0       PH.07      *I2S2 bit clock*

  **13**       **SPI**    SPI3_SCK            spi3_sck_py0        gpiochip0       PY.00      *SPI3 clock*

  **14**       **GND**    GND                 ---                 ---             ---        *Ground*

  **15**       **PWM**    PWM1                soc_gpio39_pn1      gpiochip0       PN.01      *PWM1 output*

  **16**       **SPI**    SPI3_CS1            spi3_cs1_py4        gpiochip0       PY.04      *SPI3 chip select 1*

  **17**       **PWR**    3.3V                ---                 ---             ---        *3.3V power rail*

  **18**       **SPI**    SPI3_CS0            spi3_cs0_py3        gpiochip0       PY.03      *SPI3 chip select 0*

  **19**       **SPI**    SPI1_MOSI           spi1_mosi_pz5       gpiochip0       PZ.05      *SPI1 MOSI (dout)*

  **20**       **GND**    GND                 ---                 ---             ---        *Ground*

  **21**       **SPI**    SPI1_MISO           spi1_miso_pz4       gpiochip0       PZ.04      *SPI1 MISO (din)*

  **22**       **SPI**    SPI3_MISO           spi3_miso_py1       gpiochip0       PY.01      *SPI3 MISO (din)*

  **23**       **SPI**    SPI1_SCK            spi1_sck_pz3        gpiochip0       PZ.03      *SPI1 clock*

  **24**       **SPI**    SPI1_CS0            spi1_cs0_pz6        gpiochip0       PZ.06      *SPI1 chip select 0*

  **25**       **GND**    GND                 ---                 ---             ---        *Ground*

  **26**       **SPI**    SPI1_CS1            spi1_cs1_pz7        gpiochip0       PZ.07      *SPI1 chip select 1*

  **27**       **I2C**    I2C2_SDA            gen2_i2c_sda_pdd0   gpiochip1       PDD.00     *i2c-1 (c240000) SDA*

  **28**       **I2C**    I2C2_SCL            gen2_i2c_scl_pcc7   gpiochip1       PCC.07     *i2c-1 (c240000) SCL*

  **29**       **GPIO**   GPIO01 / CAM_CLK3   soc_gpio32_pq5      gpiochip0       PQ.05      *extperiph3 clock / GPIO*

  **30**       **GND**    GND                 ---                 ---             ---        *Ground*

  **31**       **GPIO**   GPIO11 / CAM_CLK4   soc_gpio33_pq6      gpiochip0       PQ.06      *extperiph4 clock / GPIO*

  **32**       **PWM**    PWM7                soc_gpio19_pg6      gpiochip0       PG.06      *PWM7 output*

  **33**       **PWM**    PWM5                soc_gpio21_ph0      gpiochip0       PH.00      *PWM5 output*

  **34**       **GND**    GND                 ---                 ---             ---        *Ground*

  **35**       **I2S**    I2S2_FS             soc_gpio44_pi2      gpiochip0       PI.02      *I2S2 frame sync*

  **36**       **UART**   UART1_CTS           uart1_cts_pr5       gpiochip0       PR.05      *ttyTHS1 CTS*

  **37**       **SPI**    SPI3_MOSI           spi3_mosi_py2       gpiochip0       PY.02      *SPI3 MOSI (dout)*

  **38**       **I2S**    I2S2_DIN            soc_gpio43_pi1      gpiochip0       PI.01      *I2S2 data in*

  **39**       **GND**    GND                 ---                 ---             ---        *Ground*

  **40**       **I2S**    I2S2_DOUT           soc_gpio42_pi0      gpiochip0       PI.00      *I2S2 data out*
  ------------ ---------- ------------------- ------------------- --------------- ---------- ---------------------------------------

**3. I2C Buses**

Five I2C buses are available for user peripherals. The primary sensor bus exposed on the 40-pin header is i2c-7. Do not use i2c-4 (BPMP) or i2c-9 (SOC internal).

**3.1 Bus reference**

  ----------- -------------- --------------- ------------ ------------------------------------------------------------------
  **Bus**     **SoC addr**   **Alias**       **Status**   **Notes**

  i2c-0       3160000        gen1_i2c        okay         Module EEPROM at 0x50. General use.

  i2c-1       c240000        gen2_i2c        okay         Header pins 27/28. FUSB USB-C controller internal.

  i2c-2       3180000        cam_i2c         okay         Camera I2C bus. General use.

  i2c-3       3190000        dp_aux_ch1      disabled     DisplayPort AUX. Not usable.

  i2c-4       BPMP           BPMP internal   ---          DO NOT USE --- NVIDIA internal power management.

  i2c-5       31b0000        dp_aux_ch0      okay         DisplayPort AUX. General use if DP not active.

  i2c-6       31c0000        dp_aux_ch2      disabled     DisplayPort AUX. Not usable.

  **i2c-7**   c250000        hdr40_i2c1      okay         PRIMARY USER I2C --- header pins 3/5. Use for IMU, ToF, sensors.

  i2c-8       31e0000        dp_aux_ch3      disabled     Not usable.

  i2c-9       SOC            SOC internal    ---          DO NOT USE --- NVIDIA internal.
  ----------- -------------- --------------- ------------ ------------------------------------------------------------------

**3.2 Discovery commands**

Run after connecting sensor hardware:

> \# List all I2C buses
>
> i2cdetect -l
>
> \# Scan primary header bus (i2c-7) --- use this for IMU, ToF
>
> i2cdetect -y 7
>
> \# Scan all user buses to find an unknown device
>
> for b in 0 1 2 5 7; do echo \"=== bus \$b ===\"; i2cdetect -y \$b; done
>
> \# Known device addresses
>
> \# 0x68 / 0x69 → MPU9250 / MPU6500 IMU
>
> \# 0x29 → VL53L0X time-of-flight
>
> \# 0x50 → Module EEPROM (already on i2c-0)

**3.3 I2C pin usage table**

  ---------------- ------------ ----------- ------------------- -------------------------------------------------------
  **Header pin**   **Signal**   **Bus**     **SoC pad**         **Usage**

  **3**            SDA          **i2c-7**   gen8_i2c_sda_pdd2   PRIMARY --- connect IMU, ToF, sensors here

  **5**            SCL          **i2c-7**   gen8_i2c_scl_pdd1   PRIMARY --- connect IMU, ToF, sensors here

  **27**           SDA          i2c-1       gen2_i2c_sda_pdd0   Secondary. Internal USB-C controller shares this bus.

  **28**           SCL          i2c-1       gen2_i2c_scl_pcc7   Secondary. Internal USB-C controller shares this bus.
  ---------------- ------------ ----------- ------------------- -------------------------------------------------------

**4. UART / Serial**

Three serial controllers are active. The 40-pin header exposes UART1 (ttyTHS0). The debug console (ttyTCU0) is available via the micro-USB debug port or dedicated UART pins.

  ------------- -------------- ----------- ------------------------ ----------------------------------------------------------
  **Device**    **SoC addr**   **Alias**   **Header pins**          **Notes**

  **ttyTHS0**   3100000        uarta       Header pins 8/10/11/36   UART1 --- primary user UART on 40-pin header

  **ttyTHS1**   3140000        uarte       Internal only            UART5 --- appears as ttyTHS1 on this board (uarte alias)

  **ttyTCU0**   31d0000        uarti       Debug console            ARM SBSA UART --- serial console at 115200 baud
  ------------- -------------- ----------- ------------------------ ----------------------------------------------------------

  ---------- ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **UART**   For Dynamixel motors via USB2Dynamixel adapter: plug into USB, device appears as /dev/ttyUSB0 (FTDI FT232 chip). Do NOT use the header UART for Dynamixel --- use the USB adapter.

  ---------- ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**5. GPIO**

The Tegra234 exposes GPIO through two chips. Use libgpiod (not Jetson.GPIO Python library --- it issues warnings on Eagle 201 and cannot be trusted for pin numbering on this carrier).

**5.1 GPIO chips**

  --------------- ------------------- ------------- -------------------------------------------------------------------------------------------------------------------------
  **Chip**        **Name**            **Lines**     **Notes**

  **gpiochip0**   tegra234-gpio       164           Main SoC GPIO. All header GPIO pins. Lines named PA.xx through PAG.xx.

  **gpiochip1**   tegra234-gpio-aon   32            Always-on GPIO bank. Lines named PAA.xx through PGG.xx. Some used by board (PAA.05 = PCIe 3.3V, PEE.04 = Power button).
  --------------- ------------------- ------------- -------------------------------------------------------------------------------------------------------------------------

**5.2 Reserved lines --- do not use**

  ----------- ------------- ---------- ------------------------ -------------------------------------------------------
  **Chip**    **Line \#**   **Port**   **Label**                **Reason**

  gpiochip0   **0**         PA.00      regulator-vdd-3v3-sd     SD card 3.3V power rail

  gpiochip0   **35**        PG.00      Force Recovery           Recovery mode button --- toggling reboots to recovery

  gpiochip0   **56**        PI.05      (kernel)                 Kernel-owned, function unknown

  gpiochip0   **74**        PL.02      nvidia,pex-wake          PCIe wake signal

  gpiochip0   **76**        PM.00      (kernel)                 Kernel-owned, function unknown

  gpiochip0   **114**       PX.00      (kernel)                 Kernel-owned, function unknown

  gpiochip0   **115**       PX.01      (kernel)                 Kernel-owned, function unknown

  gpiochip1   **5**         PAA.05     regulator-vdd-3v3-pcie   PCIe 3.3V power rail

  gpiochip1   **27**        PEE.04     Power                    Power button --- do not toggle
  ----------- ------------- ---------- ------------------------ -------------------------------------------------------

**5.3 Safe user GPIO candidates (header-exposed)**

  ---------------- --------------- ---------- ------------------- ------------------------------
  **Header pin**   **GPIO chip**   **Port**   **gpiochip line**   **Alternate function**

  **29**           gpiochip0       PQ.05      105                 extperiph3_clk / GPIO

  **31**           gpiochip0       PQ.06      106                 extperiph4_clk / GPIO

  **32**           gpiochip0       PG.06      38                  PWM7 (pwm@32e0000)

  **33**           gpiochip0       PH.00      43                  PWM5 (pwm@32c0000)

  **15**           gpiochip0       PN.01      85                  PWM1 (pwm@3280000)
  ---------------- --------------- ---------- ------------------- ------------------------------

**5.4 libgpiod usage (correct method)**

> \# Install
>
> sudo apt install libgpiod-dev gpiod
>
> \# List all GPIO chips
>
> gpiodetect
>
> \# Show all lines on chip0
>
> gpioinfo gpiochip0
>
> \# Find unused lines only
>
> gpioinfo gpiochip0 \| grep unused
>
> \# Set line 105 (PQ.05, header pin 29) HIGH --- verify 3.3V with multimeter
>
> gpioset gpiochip0 105=1
>
> \# Read line 106 (PQ.06, header pin 31)
>
> gpioget gpiochip0 106
>
> \# C++ example (libgpiod)
>
> gpiod_chip \*chip = gpiod_chip_open_by_name(\"gpiochip0\");
>
> gpiod_line \*line = gpiod_chip_get_line(chip, 105);
>
> gpiod_line_request_output(line, \"dmr\", 0);
>
> gpiod_line_set_value(line, 1);

  ------------- -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **WARNING**   Do NOT use Jetson.GPIO Python library for Eagle 201. It prints \"Carrier board is not from a Jetson Developer Kit\" and its BOARD pin numbering maps to the NVIDIA devkit header, which may not match Eagle 201 physical positions.

  ------------- -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

**6. Dynamixel Motor Control**

**6.1 USB2Dynamixel adapter**

The USB2Dynamixel adapter (FTDI FT232 chip) connects via USB and appears as /dev/ttyUSB0. It does not power motors --- a separate 12V supply is required.

  ----------------------------- --------------------------------------------------------
  **USB vendor ID**             0403 (FTDI) --- confirmed by udev rule

  **Device node**               /dev/ttyUSB0 (after plugging in)

  **Kernel driver**             ftdi_sio

  **Mode switch**               TTL for AX/MX series, RS485 for RX series

  **Motor power**               External supply required --- USB does not power motors

  **Protocol 1.0**              AX-12, AX-18, RX-28, RX-64, EX series

  **Protocol 2.0**              XL, XM, XH, XD, MX (2.0 mode) series

  **Default baud (AX/MX)**      57600

  **Default baud (X-series)**   57600 (can be changed to 1000000)
  ----------------------------- --------------------------------------------------------

**6.2 Setup commands**

> \# udev rule --- allows non-root access, creates symlink
>
> echo \'SUBSYSTEM==\"tty\", ATTRS{idVendor}==\"0403\", MODE=\"0666\", SYMLINK+=\"ttyDynamixel\"\' \\
>
> \| sudo tee /etc/udev/rules.d/99-dynamixel.rules
>
> sudo udevadm control \--reload-rules
>
> \# Add user to dialout group
>
> sudo usermod -aG dialout \$USER
>
> \# (log out and back in after this)
>
> \# Verify adapter after plugging in
>
> dmesg \| grep -i \"ftdi\\\|tty\" \| tail -5
>
> ls -la /dev/ttyUSB\* /dev/ttyDynamixel
>
> \# Build Dynamixel SDK for aarch64
>
> git clone https://github.com/ROBOTIS-GIT/DynamixelSDK
>
> cd DynamixelSDK/c++/build/linux_aarch64
>
> make -j\$(nproc) && sudo make install
>
> \# Installs to /usr/local/lib/libdxl_aarch64_cpp.so

**7. dmr_config.h Reference**

All hardware configuration for the DMR stack goes through dmr_config.h. Fill in values after running discovery commands with hardware connected.

> #pragma once
>
> #include \<string\>
>
> // ── Dynamixel ─────────────────────────────────────────────────────
>
> // Verify: dmesg \| grep tty \| tail -5 after plugging in USB2Dynamixel
>
> #define DMR_DXL_PORT \"/dev/ttyUSB0\"
>
> #define DMR_DXL_BAUD 57600 // AX/MX default; 1000000 for X-series
>
> #define DMR_DXL_PROTOCOL 1.0f // 1.0=AX/RX/MX 2.0=XL/XM/XH/XD
>
> // ── I2C ───────────────────────────────────────────────────────────
>
> // PRIMARY bus: i2c-7 (c250000) on header pins 3/5
>
> // Scan: i2cdetect -y 7 after connecting sensor
>
> #define DMR_I2C_BUS_IMU 7 // MPU9250 at 0x68/0x69 on i2c-7
>
> #define DMR_I2C_BUS_TOF 7 // VL53L0X at 0x29 on i2c-7
>
> #define DMR_MPU9250_ADDR 0x68 // AD0 low=0x68, AD0 high=0x69
>
> #define DMR_VL53L0X_ADDR 0x29
>
> // ── GPIO ──────────────────────────────────────────────────────────
>
> // Use libgpiod with gpiochip0 line numbers
>
> // Verify: gpioset gpiochip0 \<line\>=1, measure 3.3V on header
>
> #define DMR_GPIO_CHIP \"gpiochip0\"
>
> #define DMR_GPIO_IR_TX -1 // VERIFY: which header pin physically?
>
> #define DMR_GPIO_IR_RX -1 // VERIFY: which header pin physically?
>
> // ── Runtime guards ────────────────────────────────────────────────
>
> inline bool dmr_check_i2c(int bus) {
>
> if (bus \< 0) {
>
> fprintf(stderr, \"\[DMR\] I2C not configured. Run: i2cdetect -y 7\\n\");
>
> return false;
>
> }
>
> return true;
>
> }
>
> inline bool dmr_check_gpio(int line) {
>
> if (line \< 0) {
>
> fprintf(stderr, \"\[DMR\] GPIO not configured. See dmr_config.h\\n\");
>
> return false;
>
> }
>
> return true;
>
> }

**8. Quick Reference Card**

**Discovery command sequence (run on Jetson)**

> \# 1. Full hardware scan --- run before any development session
>
> i2cdetect -l \# list all I2C buses
>
> i2cdetect -y 7 \# scan primary header bus
>
> gpiodetect \# list GPIO chips
>
> gpioinfo gpiochip0 \| grep unused \| head -30 \# free GPIO lines
>
> ls /dev/ttyUSB\* /dev/ttyTHS\* 2\>/dev/null \# serial ports
>
> dmesg \| grep -i \"usb\\\|ftdi\\\|tty\" \| tail -10 \# USB device log
>
> \# 2. Verify a GPIO line reaches the physical header
>
> gpioset gpiochip0 105=1 \# pin 29 HIGH → measure 3.3V with multimeter
>
> gpioset gpiochip0 105=0 \# back LOW
>
> \# 3. After connecting USB2Dynamixel
>
> dmesg \| grep tty \| tail -5 \# should show ttyUSB0
>
> ls /dev/ttyUSB0 \# confirm device exists
>
> \# 4. After connecting I2C sensor on pins 3/5
>
> i2cdetect -y 7 \# look for 0x68 (IMU) or 0x29 (ToF)

**Pin cross-reference: signal → header pin**

  --------------------- --------- --------------------- --------------------------- -------------------- ----------
  **Signal**            **Pin**   **Signal**            **Pin**                     **Signal**           **Pin**

  I2C-7 SDA (primary)   **3**     I2C-7 SCL (primary)   **5**                       UART1 TX (ttyTHS0)   **8**

  UART1 RX              **10**    UART1 RTS             **11**                      UART1 CTS            **36**

  SPI1 MOSI             **19**    SPI1 MISO             **21**                      SPI1 SCK             **23**

  SPI1 CS0              **24**    SPI1 CS1              **26**                      SPI3 SCK             **13**

  SPI3 MOSI             **37**    SPI3 MISO             **22**                      SPI3 CS0             **18**

  PWM1                  **15**    PWM5                  **33**                      PWM7                 **32**

  GPIO (PQ.05)          **29**    GPIO (PQ.06)          **31**                      AUD_MCLK             **7**

  I2C-1 SDA             **27**    I2C-1 SCL             **28**                      3.3V                 **1,17**

  5V                    **2,4**   GND                   **6,9,14,20,25,30,34,39**   ---                  **---**
  --------------------- --------- --------------------- --------------------------- -------------------- ----------

**9. Known Issues & Warnings**

  ---------------------------- ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **NO EAGLE 201 DATASHEET**   Tanna Techbiz provides no schematic or pinout documentation. All pin assignments in this document are derived from the Jetson Orin NX devkit DTB. Physical header positions on Eagle 201 must be verified with a multimeter.

  ---------------------------- ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

  ------------------------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **DEVKIT DTB MISMATCH**   Eagle 201 boots using NVIDIA\'s p3768+p3767-0001 devkit DTB --- not a custom Eagle 201 DTB. The SoC signals are correctly described but physical connector layout may differ from the devkit.

  ------------------------- -----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

  ------------------------- --------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **JETSON.GPIO WARNING**   \"Carrier board is not from a Jetson Developer Kit\" --- expected. The library loads but BOARD pin numbering is unreliable on Eagle 201. Use libgpiod exclusively.

  ------------------------- --------------------------------------------------------------------------------------------------------------------------------------------------------------------

  ----------------------------- ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **SUSPICIOUS OUTPUT LINES**   Lines PH.06 (49), PK.04 (68), PK.05 (69), PQ.03 (103), PAC.00 (138) on gpiochip0 and PAA.04 (4), PBB.03 (11), PCC.00--03 (12--15) on gpiochip1 are configured as outputs with no kernel label. These are likely used by Eagle 201 onboard hardware. Measure voltage before using any of these lines.

  ----------------------------- ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------

  --------------------- ----------------------------------------------------------------------------------------------------------------------------------------------------------------
  **I2C-4 AND I2C-9**   These are internal NVIDIA buses (BPMP power management and SOC internal). Never open /dev/i2c-4 or /dev/i2c-9 from userspace --- can cause system instability.

  --------------------- ----------------------------------------------------------------------------------------------------------------------------------------------------------------

  ----------------- -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
  **MOTOR POWER**   USB2Dynamixel does not power motors. Separate 12V (AX/MX) supply required on the adapter\'s power terminals. Zero motors responding is usually a power issue, not a software issue.

  ----------------- -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------
