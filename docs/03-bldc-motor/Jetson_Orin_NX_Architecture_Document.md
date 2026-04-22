**TECHNICAL ARCHITECTURE REPORT**

**NVIDIA Jetson Orin NX 8GB**

on Tanna TechBiz Eagle-201 Carrier Board

Complete System Architecture & Boot Process

System Software: NVIDIA Jetson Linux (L4T) via NVIDIA SDK Manager

Document Classification: Engineering Technical Reference

Prepared For: Engineering Management

# Document Sources

This document is based on the following authoritative sources. Engineers referencing this report should consult the primary sources listed below for the most current specifications.

## Primary Technical References

-   NVIDIA Jetson Orin NX Series Data Sheet --- DS-10712-001 v0.5 (November 2022). Official hardware specification covering SoC architecture, electrical characteristics, pinout, and interface capabilities.

-   NVIDIA Jetson Linux (L4T) Developer Guide --- https://docs.nvidia.com/jetson/archives/ --- Software stack reference covering BSP, kernel configuration, device tree, boot flow, and driver architecture for Jetson platforms.

-   NVIDIA SDK Manager Documentation --- https://docs.nvidia.com/sdk-manager/install-with-sdkm-jetson/index.html --- Official flashing and development environment setup guide for Jetson modules.

-   NVIDIA JetPack SDK Documentation --- https://developer.nvidia.com/embedded/jetpack --- Covers CUDA, TensorRT, cuDNN, VPI, DeepStream, and multimedia API versioning per JetPack release.

## Carrier Board Documentation

-   Tanna TechBiz Eagle-201 Full System Product Page --- https://tannatechbiz.com/tanna-techbiz-eagle-201-full-system-with-nvidia-jetson-orin-nx-8gb-module.html

-   Tanna TechBiz Eagle-201 Carrier Board Datasheet --- https://drive.google.com/file/d/1tPPl0nMCTC6nd1un2eD-C_JbgUrRMSst/view

## Reference Architecture Documents

-   Arm Cortex-A78AE Technical Reference Manual --- Arm Limited. Covers core microarchitecture, cache hierarchy, exception model, and RAS features.

-   NVIDIA Ampere GPU Architecture Whitepaper --- NVIDIA Corporation. Covers SM architecture, Tensor Core generation, and compute capability.

-   NVIDIA Tegra Boot Flow Application Note --- Referenced for BootROM → MB1 → MB2 → UEFI boot stage descriptions.

## Standards Referenced

-   JEDEC LPDDR5 Standard (JESD209-5) --- Memory interface electrical and protocol specification.

-   PCIe Base Specification 4.0 --- PCI-SIG. Referenced for PCIe Gen4 electrical and protocol characteristics.

-   USB 3.2 Specification --- USB Implementers Forum. Referenced for USB SuperSpeed interface descriptions.

-   MIPI CSI-2 Specification v3.0 --- MIPI Alliance. Referenced for camera serial interface descriptions.

-   GlobalPlatform TEE System Architecture --- Referenced for TrustZone / OP-TEE security model descriptions.

# 1. System Overview

The Tanna TechBiz Eagle-201 system integrates the NVIDIA Jetson Orin NX 8GB System-on-Module (SoM) with a purpose-designed carrier board to deliver a compact, high-performance edge AI computing platform. This section describes the complete hardware architecture from the SoC level through to carrier board integration.

## 1.1 Jetson Orin NX 8GB Module

The Jetson Orin NX 8GB is a System-on-Module (SoM) measuring 69.6 mm × 45 mm and connecting to its carrier board via a 260-pin SO-DIMM edge connector. It integrates the NVIDIA Orin SoC, LPDDR5 memory, PMIC, and all necessary supporting circuitry, presenting a rich set of interfaces to the carrier board.

## 1.2 SoC Architecture

The NVIDIA Orin SoC is a heterogeneous multi-processor system integrating CPU, GPU, dedicated AI accelerators, media engines, and sensor processing, all interconnected by a high-bandwidth memory subsystem.

### 1.2.1 CPU Subsystem

The Jetson Orin NX 8GB contains six Arm Cortex-A78AE CPU cores running at up to 2 GHz. The CPU complex is derived from a multi-cluster quad-core architecture (the full Orin SoC supports up to eight cores across quad-core clusters; the 8GB module activates six cores). A 4 MB Last Level Cache (LLC) is shared across all active cores. Each core contains private L1 caches (64 KB I-cache + 64 KB D-cache) and a 256 KB L2 cache. The Cortex-A78AE implements the Armv8.2-A ISA with selected Armv8.3 through Armv8.5 extensions, supporting 40-bit physical addressing and GICv3 interrupt architecture.

### 1.2.2 GPU Subsystem

The NVIDIA Ampere architecture GPU features 1,024 CUDA cores and 32 third-generation Tensor Cores operating at up to 765 MHz, delivering 35 Dense INT8 TOPs. The GPU integrates multiple Texture Processing Clusters (TPC), each containing two Streaming Multiprocessors (SM), Polymorph engines, Texture Units, and RTCore ray-tracing units. It supports CUDA 11.x (JetPack 5) and CUDA 12.x (JetPack 6+), OpenGL 4.6, Vulkan 1.1, and structured sparsity for up to 2× inference acceleration.

### 1.2.3 AI Accelerators

One NVDLA (Deep Learning Accelerator) instance operates at up to 610 MHz, delivering 20 Sparse INT8 TOPs for CNN inference acceleration. The Programmable Vision Accelerator (PVA) v2 provides a dedicated vision DSP with dual Vector Processing Units (VPU) at up to 700 MHz, each with 384 KB triple-port memory, for deterministic low-latency computer vision operations.

### 1.2.4 Memory Subsystem

The 8 GB LPDDR5 DRAM operates on a 128-bit wide bus at up to 3,200 MHz, providing a theoretical peak bandwidth of 102 GB/s. The Memory Subsystem (MSS) provides a scalable coherence fabric with SMMU-500 address translation, TrustZone secure regions, and AES-XTS 128-bit encryption. The dedicated Memory Controller (MC) maximizes bandwidth utilization and minimizes latency for critical CPU and GPU requests via programmable arbitration.

### 1.2.5 Media and Sensor Subsystem

The SoC integrates NVDEC video decode (H.265/H.264/VP9/AV1 up to 8K30), NVENC video encode (H.265/H.264/AV1 up to 4K60), dual NVJPEG engines, Video Image Compositor (VIC), and a fifth-generation MIPI camera pipeline (NVCSI 2.0 + VI 5.0 + ISP 6.x). The ISP supports raw Bayer sensors up to 24 Mpixels at 1.75 GPixel/s.

## 1.3 Peripheral Interfaces

  --------------------------------------------------------------------------------------------------
  **Interface**      **Quantity**       **Standard / Speed**   **Notes**
  ------------------ ------------------ ---------------------- -------------------------------------
  USB 3.2 Gen2       3×                 10 Gbps                xHCI; USB0 also supports Device/RCM

  USB 2.0            3×                 480 Mbps               Shared with USB 3.2 ports

  PCIe Gen4          4 ctrl / 7 lanes   16 GT/s per lane       x4+x1+x2 configuration

  Gigabit Ethernet   1×                 10/100/1000BASE-T      IEEE 802.3ab MAC on-module

  MIPI CSI-2         4× 2-lane          20 Gbps aggregate      D-PHY 2.1

  HDMI / DP          1×                 HDMI 2.1 / DP 1.4a     Shared pins

  UART               3×                 Up to 12.5 Mbps        16450/16550 compatible

  SPI                2×                 Master/Slave           64×32 FIFO

  I2C                4×                 Up to 1 Mbit/s (Fm+)   Including Camera I2C

  CAN FD             1×                 Up to 8 Mbps           Bosch M_TTCAN v3.2

  I2S                2×                 Up to 24.576 MHz       TDM/PCM modes supported

  GPIO               15×                1.8V CMOS              PWM alternate on GPIO07/12/13/14
  --------------------------------------------------------------------------------------------------

## 1.4 Power System

The Eagle-201 carrier board supplies VDD_IN (5V--20V) to the module across pins 251--260. The on-module PMIC generates all internal power rails. The carrier board must assert POWER_EN only when VDD_IN is stable, and must monitor SHUTDOWN_REQ\* to gracefully handle thermal, software, or under-voltage shutdown requests from the module.

Three power states are supported: ON (full operation), SLEEP/SC7 (deep sleep with I/O state maintained), and OFF. Power modes for the Orin NX 8GB are 10W, 15W, and 20W, selectable via nvpmodel.

## 1.5 Hardware Architecture Block Diagram

The following diagram illustrates the top-level system architecture and the interconnects between the Jetson Orin NX module and the Eagle-201 carrier board:

**EAGLE-201 FULL SYSTEM --- TOP-LEVEL BLOCK DIAGRAM**

+---------------------------------------------------------------------------------------------------------------------------------------+
| **JETSON ORIN NX 8GB MODULE (69.6mm × 45mm \| 260-pin SO-DIMM)**                                                                      |
+=======================================================================================================================================+
| **NVIDIA ORIN SoC**                                                                                                                   |
|                                                                                                                                       |
|   ----------------------------------------------------------------------------------------------------------------------------------- |
|   **CPU 6× A78AE @ 2GHz**          **Ampere GPU 1024 CUDA 32 Tensor**    **NVDLA 1× \@610MHz**   **PVA 1× \@700MHz**         \...     |
|   -------------------------------- ------------------------------------- ----------------------- --------------------------- -------- |
|   **8GB LPDDR5 128-bit 102GB/s**   **NVDEC/NVENC H.265/H.264 AV1/VP9**   **ISP 24MP 1.75GP/s**   **NVCSI/VI 4×CSI 20Gbps**   SE PSC   |
|                                                                                                                                       |
|   ----------------------------------------------------------------------------------------------------------------------------------- |
+---------------------------------------------------------------------------------------------------------------------------------------+
| **PMIC \| PMC \| RTC (PMIC_BBAT) \| Boot Flash (QSPI/eMMC)**                                                                          |
+---------------------------------------------------------------------------------------------------------------------------------------+
| **EAGLE-201 CARRIER BOARD**                                                                                                           |
+---------------------------------------------------------------------------------------------------------------------------------------+
| USB3 \| PCIe/NVMe \| HDMI/DP \| GbE \| CSI Cameras \| GPIO/UART/SPI/I2C/CAN \| Power Regulation (5V--20V in)                          |
+---------------------------------------------------------------------------------------------------------------------------------------+

*Figure 1: Eagle-201 System Hardware Block Diagram*

# 2. Jetson Boot Flow Overview

The Jetson Orin NX employs a multi-stage secure boot process. Each stage is cryptographically verified by the previous stage, establishing a hardware root-of-trust chain from the immutable BootROM through to the Linux user space.

+--------+-------------------------------------------------------------------------------+--------+
|        | **POWER ON**                                                                  |        |
|        |                                                                               |        |
|        | VDD_IN stable → POWER_EN asserted by carrier board                            |        |
+========+===============================================================================+========+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **PMIC / HARDWARE RESET**                                                     |        |
|        |                                                                               |        |
|        | Power rails ramp in sequence; SYS_RESET\* deasserted                          |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **BootROM**                                                                   |        |
|        |                                                                               |        |
|        | Immutable ROM on SoC; reads BCT + MB1 from QSPI flash; verifies RSA signature |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **MB1 (MicroBootloader 1)**                                                   |        |
|        |                                                                               |        |
|        | Executes on BPMP; DRAM training; PMIC config; clock init; loads MB2           |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **MB2 (MicroBootloader 2)**                                                   |        |
|        |                                                                               |        |
|        | Loads ATF, TOS, BPMP firmware, UEFI/CBoot; security policy enforcement        |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **UEFI / CBoot**                                                              |        |
|        |                                                                               |        |
|        | Jetson bootloader; loads kernel + DTB; passes boot args via FDT               |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **Linux Kernel (Image / Image.gz)**                                           |        |
|        |                                                                               |        |
|        | Decompresses; parses device tree; initialises drivers; mounts rootfs          |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **Root Filesystem (ext4 on eMMC or NVMe)**                                    |        |
|        |                                                                               |        |
|        | initramfs optional; rootfs mounted; PID 1 launched                            |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | ▼                                                                             |        |
+--------+-------------------------------------------------------------------------------+--------+
|        | **User Space (systemd)**                                                      |        |
|        |                                                                               |        |
|        | Services started; NVIDIA GPU driver; JetPack stack; operational state         |        |
+--------+-------------------------------------------------------------------------------+--------+

*Figure 2: Jetson Orin NX Complete Boot Flow*

# 3. Power-On Hardware Initialization

When power is applied to the Eagle-201 carrier board, a carefully controlled hardware startup sequence ensures that all voltage rails are stable and that the Jetson module is properly initialized before software execution begins.

## 3.1 Power Rails and Sequencing

The carrier board provides VDD_IN (5V to 20V) to the Jetson module across ten parallel pins (pins 251--260). The module\'s PMIC internally generates all required rails. The MODULE_ID strap (pin 217) floating indicates the module supports 5V--20V input; if pulled to GND, only 5V is supported.

The carrier board must observe the following hardware constraints during power-up:

-   VDD_IN must reach its required stable voltage before asserting POWER_EN (pin 237).

-   All I/O pins must remain below 0.5V while SYS_RESET\* (pin 239) is asserted low.

-   The carrier board must wait for SYS_RESET\* to be deasserted HIGH by the module before enabling carrier board supplies.

-   PMIC_BBAT (pin 235) may be connected to a 1.75V--5.5V lithium cell to maintain RTC during power-off.

## 3.2 Power-On Sequence Diagram

+--------+----------------------------------------------------------------------------+--------+
|        | **Carrier Board: VDD_IN applied (5V--20V)**                                |        |
|        |                                                                            |        |
|        | External power source provides stable input                                |        |
+========+============================================================================+========+
|        | ▼                                                                          |        |
+--------+----------------------------------------------------------------------------+--------+
|        | **Module PMIC: Internal rails ramp**                                       |        |
|        |                                                                            |        |
|        | VDD_CPU, VDD_GPU, VDD_SOC, VDD_MEM sequenced                               |        |
+--------+----------------------------------------------------------------------------+--------+
|        | ▼                                                                          |        |
+--------+----------------------------------------------------------------------------+--------+
|        | **Carrier Board: POWER_EN asserted HIGH**                                  |        |
|        |                                                                            |        |
|        | Indicates to module that VDD_IN is stable                                  |        |
+--------+----------------------------------------------------------------------------+--------+
|        | ▼                                                                          |        |
+--------+----------------------------------------------------------------------------+--------+
|        | **Module: SYS_RESET\* deasserted HIGH**                                    |        |
|        |                                                                            |        |
|        | Module signals power-good to carrier board; carrier board supplies enabled |        |
+--------+----------------------------------------------------------------------------+--------+
|        | ▼                                                                          |        |
+--------+----------------------------------------------------------------------------+--------+
|        | **PMC: Resets released; clock PLLs locked**                                |        |
|        |                                                                            |        |
|        | OSC clock (38.4 MHz) → PLLs → system clocks distributed                    |        |
+--------+----------------------------------------------------------------------------+--------+
|        | ▼                                                                          |        |
+--------+----------------------------------------------------------------------------+--------+
|        | **BPMP: BootROM execution begins**                                         |        |
|        |                                                                            |        |
|        | Secure boot verification sequence initiated                                |        |
+--------+----------------------------------------------------------------------------+--------+

*Figure 3: Power-On Sequence*

## 3.3 Reset Logic

The SYS_RESET\* signal is bidirectional open-drain with a 10 kΩ pull-up to 1.8V on the module. When driven low by the carrier board, it holds the module in reset. When driven high by the module (after power sequencing is complete), it signals to the carrier board that it may enable its own supplies. This bidirectional handshake prevents race conditions during power-up and power-down.

The Power Management Controller (PMC) on the Orin SoC manages the internal reset tree, including enabling aggressive power-gating on idle sub-modules and controlling power domain transitions during sleep and deep-sleep modes.

# 4. BootROM Stage

## 4.1 What is BootROM?

The BootROM is a small, immutable piece of code permanently embedded in the Orin SoC during manufacture. It is the first software that executes on the BPMP (Boot and Power Management Processor) subsystem after reset is deasserted. Because it is mask-ROM, it cannot be modified and serves as the hardware root of trust for the entire secure boot chain.

## 4.2 BootROM Execution Flow

+--------+------------------------------------------------------+--------+
|        | **BPMP exits reset**                                 |        |
|        |                                                      |        |
|        | Internal ROM mapped to CPU address space             |        |
+========+======================================================+========+
|        | ▼                                                    |        |
+--------+------------------------------------------------------+--------+
|        | **BootROM reads fuses**                              |        |
|        |                                                      |        |
|        | Determines boot device priority and security policy  |        |
+--------+------------------------------------------------------+--------+
|        | ▼                                                    |        |
+--------+------------------------------------------------------+--------+
|        | **Initialize I/O controllers**                       |        |
|        |                                                      |        |
|        | Programs on-chip QSPI / eMMC / USB controller        |        |
+--------+------------------------------------------------------+--------+
|        | ▼                                                    |        |
+--------+------------------------------------------------------+--------+
|        | **Read Boot Configuration Table (BCT)**              |        |
|        |                                                      |        |
|        | BCT contains DRAM parameters and MB1 location        |        |
+--------+------------------------------------------------------+--------+
|        | ▼                                                    |        |
+--------+------------------------------------------------------+--------+
|        | **Load MB1 bootloader image**                        |        |
|        |                                                      |        |
|        | Read from primary boot device into SRAM              |        |
+--------+------------------------------------------------------+--------+
|        | ▼                                                    |        |
+--------+------------------------------------------------------+--------+
|        | **Verify MB1 RSA-2048/3072 signature**               |        |
|        |                                                      |        |
|        | Using public key fused into SBK/PKC fuses            |        |
+--------+------------------------------------------------------+--------+
|        | ▼                                                    |        |
+--------+------------------------------------------------------+--------+
|        | **Signature valid?**                                 |        |
|        |                                                      |        |
|        | If invalid or no BCT found → USB Recovery Mode (RCM) |        |
+--------+------------------------------------------------------+--------+
|        | ▼                                                    |        |
+--------+------------------------------------------------------+--------+
|        | **Transfer execution to MB1**                        |        |
|        |                                                      |        |
|        | BootROM exits; MB1 begins hardware initialization    |        |
+--------+------------------------------------------------------+--------+

*Figure 4: BootROM Execution Flow*

## 4.3 Boot Device Selection

The BootROM reads the Boot Configuration Table (BCT) from the primary boot device as configured for the platform via fuse settings and board design. Typical Jetson platforms use QSPI NOR flash to store the BCT and MB1; subsequent bootloader stages and the OS reside on eMMC or NVMe. The general boot device priority is:

-   Primary: QSPI NOR flash (contains BCT + MB1)

-   Secondary: eMMC (on-module) --- contains MB2, CBoot/UEFI, kernel, and root filesystem

-   External: NVMe SSD via PCIe x4 (Gen4) --- can host root filesystem and secondary OS images

-   Recovery: USB 2.0 port USB0 (USB RCM mode) --- used when FORCE_RECOVERY\* is held low during power-on

FORCE_RECOVERY\* (pin 214) held low when SYS_RESET\* transitions high forces the BootROM to enter USB Recovery Mode, bypassing the normal boot sequence and enabling re-flashing via the SDK Manager or tegraflash tool.

## 4.4 Secure Boot Verification

When Secure Boot is enabled via fuse programming, the BootROM verifies MB1 using RSA-2048 or RSA-3072 with a public key burned into the device fuses. The Platform Security Controller (PSC) assists in key management and monitors for hardware attacks such as voltage glitching or thermal anomalies. If verification fails, the BootROM enters RCM rather than executing potentially compromised firmware.

# 5. MB1 and MB2 Boot Stages

## 5.1 MB1 --- MicroBootloader 1

MB1 is the first fully configurable software stage and runs on the BPMP co-processor. Its primary function is to bring up the hardware to a state where the main CPU complex can initialize. MB1 is stored in QSPI flash alongside the BCT.

### 5.1.1 MB1 Responsibilities

-   DRAM Initialization: reads DRAM parameters from the BCT and programs the Memory Controller (MC) channels, including LPDDR5 training sequences for timing calibration.

-   Power and Clock Configuration: programs the PMIC for required voltage levels and configures all PLL lock sequences.

-   Security Engine (SE) Setup: initializes the AES/RSA hardware accelerator and establishes TrustZone (TZ) secure/non-secure memory partitions.

-   BPMP Firmware Loading: loads the BPMP runtime firmware into SRAM and transitions the BPMP to its operational role as the system power and clock manager.

-   Load and verify MB2: locates MB2 on eMMC/QSPI, verifies its cryptographic signature, and transfers control.

## 5.2 MB2 --- MicroBootloader 2

MB2 runs on the main CPU complex (Cortex-A78AE) and is responsible for loading and verifying all system firmware components before handing control to the primary bootloader (UEFI or CBoot).

### 5.2.1 MB2 Responsibilities

-   Arm Trusted Firmware (ATF/BL31): loads and verifies the ATF image that implements the Arm Secure Monitor (EL3).

-   Trusted OS (TOS/OP-TEE): loads the optionally present Trusted Execution Environment at Secure EL1.

-   BPMP Firmware: loads the final BPMP firmware image that remains resident throughout system operation, managing clocks, resets, and power domains.

-   UEFI / CBoot: loads and verifies the primary bootloader from eMMC partition.

-   Handoff: transfers execution to the bootloader, passing system information via structures in memory.

## 5.3 BPMP Architecture

The Boot and Power Management Processor (BPMP) is a dedicated Arm Cortex-R5 real-time co-processor integrated in every Jetson Orin SoC. It is the first processor to execute code on power-on and remains active throughout the entire system lifetime as an always-on resource manager.

### 5.3.1 BPMP Role Across Boot Stages

During early boot (BootROM → MB1), the BPMP core executes the BootROM and MB1 firmware itself --- the main Cortex-A78AE CPU cluster has not yet started. MB1 initializes DRAM, programs the PMIC, and loads the BPMP runtime firmware into SRAM. Once loaded, BPMP transitions to its operational firmware, at which point MB2 starts on Cortex-A78AE and completes the remaining firmware loading.

### 5.3.2 BPMP Runtime Responsibilities

-   Clock Management: all clock enable/disable/rate-change requests from the OS pass through BPMP. The Linux clk driver communicates via the BPMP IPC mailbox (bpmp-clks device). BPMP validates requests against thermal and power constraints before programming hardware PLLs and clock dividers.

-   Reset Control: peripheral resets (PCIe, USB, UARTE, cameras, etc.) are gated by BPMP. Linux drivers request assert/deassert via the tegra-bpmp reset API.

-   Power Domain Management: BPMP controls power domain on/off sequencing including RAM repairs, isolation, and retention. The Linux generic power domain (genpd) framework communicates with BPMP to gate power domains before clock/reset changes.

-   Dynamic Voltage and Frequency Scaling (DVFS): BPMP coordinates CPU, GPU, DLA, and EMC frequency and voltage together, ensuring Vmin/Fmax envelopes are never violated.

-   Thermal Management: BPMP reads all on-chip thermal sensors (TSENSE), applies NV power models, and issues thermal throttling requests back to CPU/GPU/DLA frequency governors.

-   OC (Overcurrent) / OT (Overtemperature) Signaling: BPMP monitors PMC interrupt lines for hardware fault events and initiates safe shutdown sequences.

### 5.3.3 BPMP IPC Communication

Linux communicates with BPMP via a shared-memory mailbox protocol using NVIDIA\'s BPMP IPC driver (drivers/firmware/tegra/bpmp.c in the L4T kernel). The protocol defines message channels per service class (MRQ --- Message Request): MRQ_CLK for clocks, MRQ_RESET for resets, MRQ_THERMAL for temperature queries, MRQ_POWERGATE for power domains, and others. All transfers are asynchronous; threads may block until BPMP acknowledges.

## 5.5 Boot Stage Interaction Diagram

## 5.4 Boot Stage Interaction Diagram

+--------+--------------------------------------------------------------------------+--------+
|        | **BootROM (BPMP core)**                                                  |        |
|        |                                                                          |        |
|        | Loads + verifies MB1 from QSPI → transfers to MB1                        |        |
+========+==========================================================================+========+
|        | ▼                                                                        |        |
+--------+--------------------------------------------------------------------------+--------+
|        | **MB1 (BPMP core)**                                                      |        |
|        |                                                                          |        |
|        | DRAM init \| PMIC cfg \| SE init \| Clock PLLs \| Loads+verifies MB2     |        |
+--------+--------------------------------------------------------------------------+--------+
|        | ▼                                                                        |        |
+--------+--------------------------------------------------------------------------+--------+
|        | **MB2 (Cortex-A78AE core 0)**                                            |        |
|        |                                                                          |        |
|        | ATF loaded \| TOS loaded \| BPMP FW loaded \| UEFI/CBoot loaded+verified |        |
+--------+--------------------------------------------------------------------------+--------+
|        | ▼                                                                        |        |
+--------+--------------------------------------------------------------------------+--------+
|        | **BPMP Firmware (running concurrently)**                                 |        |
|        |                                                                          |        |
|        | Manages clocks/resets/power via IPC mailboxes for all subsequent stages  |        |
+--------+--------------------------------------------------------------------------+--------+
|        | ▼                                                                        |        |
+--------+--------------------------------------------------------------------------+--------+
|        | **UEFI / CBoot (Cortex-A78AE)**                                          |        |
|        |                                                                          |        |
|        | Bootloader stage; loads kernel + DTB; passes cmdline                     |        |
+--------+--------------------------------------------------------------------------+--------+

*Figure 5: MB1/MB2/BPMP Boot Stage Interaction*

# 6. Bootloader Stage (UEFI / CBoot)

NVIDIA Jetson platforms use either CBoot (Chainloading Boot) for Jetson Linux / L4T, or UEFI for UEFI-based systems. On the Orin NX with JetPack/L4T, the primary bootloader is UEFI (EDK2-based), replacing the legacy CBoot used on earlier generations.

## 6.1 Bootloader Architecture

UEFI resides in the \'kernel\' or dedicated bootloader partition on eMMC. It implements the standard UEFI interface, allowing it to support both standard OS loaders and NVIDIA-specific Jetson boot extensions.

## 6.2 Bootloader Functions

-   Configuration Storage: reads extlinux.conf or EFI boot variables from the eMMC boot partition to determine the OS to boot.

-   Boot Arguments: assembles the Linux kernel command line including rootfs location (e.g., root=/dev/mmcblk0p1), hardware revision, firmware parameters, and security parameters.

-   Kernel Image Loading: reads the compressed kernel image (Image or Image.gz) from the kernel partition into DRAM.

-   Device Tree Loading: loads the appropriate Device Tree Blob (.dtb) for the specific hardware configuration (including Eagle-201 carrier board overlays).

-   Boot Device Detection: reads from eMMC by default; supports NVMe as root device if kernel command line specifies it.

-   UEFI Variable Services: provides persistent variable storage for kernel parameters and boot configuration.

## 6.3 Boot Device and OS Selection

The bootloader reads /boot/extlinux/extlinux.conf from the Linux root filesystem or uses UEFI boot entries. The extlinux.conf specifies the kernel image path, DTB path, initrd (if present), and kernel parameters. This allows A/B partition boot with automatic fallback in the event of a failed update.

## 6.4 Bootloader Architecture Diagram

+--------+----------------------------------------------------+--------+
|        | **UEFI Initialization**                            |        |
|        |                                                    |        |
|        | DXE phase; PCIe enum; USB init; storage drivers    |        |
+========+====================================================+========+
|        | ▼                                                  |        |
+--------+----------------------------------------------------+--------+
|        | **Read extlinux.conf / UEFI Boot Variables**       |        |
|        |                                                    |        |
|        | Parse boot configuration and kernel parameters     |        |
+--------+----------------------------------------------------+--------+
|        | ▼                                                  |        |
+--------+----------------------------------------------------+--------+
|        | **Load Kernel Image (Image/Image.gz)**             |        |
|        |                                                    |        |
|        | From eMMC kernel partition into DRAM               |        |
+--------+----------------------------------------------------+--------+
|        | ▼                                                  |        |
+--------+----------------------------------------------------+--------+
|        | **Load Device Tree Blob (.dtb)**                   |        |
|        |                                                    |        |
|        | Board-specific DTB with Eagle-201 overlays applied |        |
+--------+----------------------------------------------------+--------+
|        | ▼                                                  |        |
+--------+----------------------------------------------------+--------+
|        | **Assemble Kernel Command Line**                   |        |
|        |                                                    |        |
|        | root=, console=, nvidia params, security args      |        |
+--------+----------------------------------------------------+--------+
|        | ▼                                                  |        |
+--------+----------------------------------------------------+--------+
|        | **ExitBootServices()**                             |        |
|        |                                                    |        |
|        | UEFI releases hardware to kernel                   |        |
+--------+----------------------------------------------------+--------+
|        | ▼                                                  |        |
+--------+----------------------------------------------------+--------+
|        | **Jump to Kernel Entry Point**                     |        |
|        |                                                    |        |
|        | Kernel decompresses and begins arch/arm64 init     |        |
+--------+----------------------------------------------------+--------+

*Figure 6: Bootloader (UEFI) Execution Flow*

# 7. Linux Kernel Loading

NVIDIA Jetson Linux (L4T) is built on the mainline Linux kernel with NVIDIA-specific patches. The kernel image is a compressed ARM64 kernel binary (Image or Image.gz) with an embedded header describing the image size, load address, and flags.

## 7.1 Kernel Image Format

The L4T kernel is an AArch64 Linux kernel image conforming to the ARM64 boot protocol. The image is self-decompressing when in Image.gz format. The UEFI bootloader places the compressed image at the load address specified in the image header.

## 7.2 Device Tree Usage

The Device Tree Blob (DTB) is a data structure that describes the non-discoverable hardware to the kernel. For the Jetson Orin NX on the Eagle-201, the DTB encodes:

-   CPU topology and frequency tables

-   Memory map and reserved regions (firmware, secure memory)

-   All peripheral controllers (UART, SPI, I2C, USB, PCIe, CAN, CSI)

-   Power domains and PMIC regulators

-   GPIO pin mux configuration

-   Eagle-201 carrier-specific overlays (connected peripherals, camera modules)

The UEFI bootloader passes the DTB physical address in register x1 to the kernel at entry, per the ARM64 boot protocol.

## 7.3 Kernel Decompression

When using Image.gz, the kernel\'s head.S decompresses the image in-place using the embedded decompress_kernel() function. The decompressed Image is then executed. Virtual Memory is initialized early in arch/arm64/kernel/head.S, establishing the initial page tables before jumping to start_kernel().

## 7.4 Root Filesystem Mount

The kernel uses the root= kernel parameter (e.g., root=/dev/mmcblk0p1 or root=/dev/nvme0n1p1) to determine the root filesystem device. The rootfs is formatted as ext4 by default. If an initramfs is present (passed by the bootloader or embedded in the kernel), it is mounted first as the initial root, allowing disk drivers and decryption tools to initialize before the real rootfs is mounted.

# 8. Kernel Initialization

After decompression and entry via start_kernel(), the Linux kernel performs a structured initialization sequence that brings up subsystems in dependency order.

## 8.1 Early Initialization

-   Architecture initialization: MMU enabled, page tables established, interrupt vectors set.

-   Timer: ARM Arch Timer initialized from device tree; clocksource registered.

-   Console: Serial UART (UART0 on Jetson) registered as early_console for kernel messages.

-   Memory management: buddy allocator, slab allocator, vmalloc region initialized.

## 8.2 Device Tree Parsing

The kernel\'s of_platform_populate() walks the Device Tree and instantiates platform devices for every compatible node. For the Jetson Orin NX, this includes the Tegra194/Tegra234 SoC nodes, which trigger NVIDIA-specific driver probe sequences for the memory controller, clock and reset controller (CAR), BPMP IPC, and power domain controllers.

## 8.3 Hardware Driver Loading

Critical drivers initialize in the following general sequence through the kernel\'s initcall framework:

-   BPMP IPC: mailbox channel to BPMP co-processor; all subsequent clock/power calls use this channel.

-   Clock & Reset: tegra-clk driver registers all SoC clocks; enables required clocks for peripheral drivers.

-   Power Domains: GENPD provider registered; GPU, VIC, NVDEC power islands can be gated.

-   Pinmux: tegra-pinctrl driver applies default pinmux configuration from DTB.

-   GPIO: tegra186-gpio driver registers the GPIO controller.

-   I2C, SPI, UART: standard platform drivers probed; registered as /dev/i2c-N, /dev/spidevN.M, /dev/ttyTHS\*.

-   USB: xHCI driver; USB3.2 superspeed PHY initialized; USB2 enumeration begins.

-   PCIe: Tegra PCIe Gen4 driver; PHY training; root complex enumerated; NVMe probed if present.

-   Ethernet: eqos (Ethernet QOS) driver; PHY auto-negotiation; network interface registered.

-   CAN: m_ttcan driver; CAN bus enabled per DTB node.

## 8.4 GPU Driver Initialization

The NVIDIA GPU (nvgpu) kernel module initializes the Ampere GPU subsystem. This includes power domain enable, BPMP clock requests for GPC2CLK and HUBCLK, firmware loading (gpccs/fecs falcon firmware), and registration of the GPU device. The driver exposes /dev/nvgpu0 for user-space access.

On JetPack 5.x/6.x, the GPU driver is the NVIDIA open-source kernel module (nvidia-kernel-open). The CUDA driver, TensorRT, and cuDNN libraries in user space communicate with this kernel driver through ioctl calls.

# 9. User Space Startup

After the kernel mounts the root filesystem and launches PID 1, systemd orchestrates the transition to a fully operational user-space environment.

## 9.1 systemd Initialization

Jetson Linux uses systemd as PID 1. Systemd processes unit files in dependency order, parallelizing where possible:

-   sysinit.target: early mounts (sysfs, procfs, devtmpfs, cgroup hierarchies), udevd started.

-   basic.target: sockets, timers, paths activated; journal started.

-   network.target: NetworkManager or systemd-networkd brings up Ethernet interface (eth0).

-   multi-user.target: standard multi-user services including SSH, NTP, and NVIDIA system services.

-   graphical.target: display manager (GDM or lightdm) if a graphical environment is configured.

## 9.2 NVIDIA-Specific Services

-   nvpmodel.service: applies the configured power mode (10W/15W/20W) via nvpmodel tool.

-   jetson_clocks (optional): maximizes all clocks for benchmarking if enabled.

-   nv-l4t-usb-device-mode: configures USB device-mode if required.

-   nvidia-container-runtime: initializes NVIDIA container support for Docker/containerd AI workloads.

## 9.3 Device Nodes

The udev daemon populates /dev dynamically as drivers probe hardware. Key device nodes on the Jetson include:

-   /dev/nvgpu0 --- GPU access for CUDA and compute

-   /dev/video0..N --- V4L2 capture devices for CSI cameras

-   /dev/ttyTHS0 --- Primary UART (debug console / UART0)

-   /dev/mmcblk0 --- eMMC storage device

-   /dev/nvme0n1 --- NVMe SSD (if connected via PCIe)

-   /dev/can0 --- CAN bus interface

-   /dev/spidev0.0, /dev/i2c-0..N --- SPI and I2C bus devices

## 9.4 Operational State

The system reaches operational state when multi-user.target is active. At this point the NVIDIA JetPack software stack is available, CUDA applications can be launched, and the system is accessible via SSH. Typical boot time from power-on to operational state is approximately 20--40 seconds depending on the rootfs device (eMMC faster than NVMe cold start) and enabled services.

# 10. Jetson Software Stack

NVIDIA JetPack SDK provides a complete AI software ecosystem built on Jetson Linux (L4T). The stack is layered, with each layer building on the abstractions provided by the layer below.

## 10.1 Software Stack Layered Architecture

+-----------------------------------------------------------------------+
| **AI / Application Layer**                                            |
|                                                                       |
| ROS 2 \| OpenCV \| Custom AI Applications \| Edge AI Frameworks       |
+=======================================================================+
| **NVIDIA AI Frameworks**                                              |
|                                                                       |
| DeepStream SDK \| Isaac SDK \| Triton Inference Server                |
+-----------------------------------------------------------------------+
| **AI Inference Libraries**                                            |
|                                                                       |
| TensorRT \| cuDNN \| TensorFlow / PyTorch (CUDA backend)              |
+-----------------------------------------------------------------------+
| **Compute & Graphics APIs**                                           |
|                                                                       |
| CUDA \| OpenCL \| OpenGL 4.6 \| Vulkan 1.1 \| VPI (Vision)            |
+-----------------------------------------------------------------------+
| **Multimedia APIs**                                                   |
|                                                                       |
| GStreamer + NVMM plugins \| V4L2 (cameras) \| NVDEC/NVENC APIs        |
+-----------------------------------------------------------------------+
| **NVIDIA User-Space Drivers**                                         |
|                                                                       |
| libcuda.so \| libnvdla \| libnvvic \| tegra_multimedia_api            |
+-----------------------------------------------------------------------+
| **Linux Kernel + NVIDIA Modules**                                     |
|                                                                       |
| nvgpu.ko \| tegra-drm.ko \| nvdla.ko \| tegra-video.ko \| L4T BSP     |
+-----------------------------------------------------------------------+
| **Hardware (Orin SoC + Eagle-201 Carrier)**                           |
|                                                                       |
| GPU \| DLA \| PVA \| ISP \| NVDEC/NVENC \| CPU \| Peripherals         |
+-----------------------------------------------------------------------+

*Figure 7: JetPack Software Stack Architecture*

## 10.2 Key Components

  ---------------------------------------------------------------------------------------------------------
  **Component**           **Version (JetPack 6.x)**   **Purpose**
  ----------------------- --------------------------- -----------------------------------------------------
  CUDA                    12.x                        GPU parallel computing API and runtime

  TensorRT                8.x / 9.x                   High-performance deep learning inference optimizer

  cuDNN                   8.x / 9.x                   GPU-accelerated deep neural network primitives

  VPI                     3.x                         Vision Programming Interface (GPU/PVA/CPU backends)

  DeepStream              7.x                         Streaming analytics and video AI pipeline SDK

  Jetson Linux (L4T)      36.x                        BSP: kernel, bootloader, drivers, root filesystem

  OpenCV                  4.x (CUDA)                  Computer vision library with GPU acceleration

  GStreamer               1.x + NVMM                  Multimedia pipeline framework with NVDEC/NVENC
  ---------------------------------------------------------------------------------------------------------

# 11. Storage and Partition Layout

The Jetson Orin NX uses eMMC as the primary boot and system storage device. NVMe SSD (connected via PCIe x4 Gen4 on the Eagle-201) can be used as a secondary data storage or as the root filesystem after OS installation.

## 11.1 eMMC Partition Layout

  -------------------------------------------------------------------------------------------------------
  **Partition Name**   **Partition \#**   **Contents**                   **Notes**
  -------------------- ------------------ ------------------------------ --------------------------------
  BCT                  ---                Boot Configuration Table       Read by BootROM; DRAM params

  mb1                  gpt-1              MB1 bootloader                 BPMP stage 1

  mb2                  gpt-2              MB2 bootloader                 CPU stage 1

  UEFI / CBoot         gpt-3              Primary bootloader binary      UEFI EDK2 image

  kernel-dtb           gpt-4              Device Tree Blob (.dtb)        Board-specific DTB

  kernel               gpt-5              Linux kernel image             Image or Image.gz

  APP (rootfs)         gpt-6              Root filesystem (ext4)         Ubuntu 20.04/22.04 base

  \[A/B slots\]        varies             OTA update slot B partitions   Duplicate of kernel+dtb+rootfs
  -------------------------------------------------------------------------------------------------------

## 11.2 NVMe Usage

When an NVMe SSD is connected to the Eagle-201 PCIe x4 slot, it appears as /dev/nvme0n1 in Linux. The root filesystem can be migrated to NVMe using the rootOnNVMe script provided in the Jetson community tools. The kernel parameter root=/dev/nvme0n1p1 is set in extlinux.conf. NVMe offers substantially higher sequential read/write performance than eMMC (typically 2--3 GB/s vs. 200--400 MB/s).

## 11.3 Flashing with NVIDIA SDK Manager

SDK Manager (SDKM) is the recommended tool for flashing Jetson Linux (L4T) onto the Jetson Orin NX. The flashing process:

-   Step 1: Install SDK Manager on a host Ubuntu PC (20.04 or 22.04 LTS).

-   Step 2: Connect the Eagle-201 USB recovery port to the host PC USB port.

-   Step 3: Place the module in Force Recovery Mode by holding FORCE_RECOVERY\* low during power-on.

-   Step 4: SDK Manager detects the target in Recovery Mode via USB RCM.

-   Step 5: SDK Manager downloads the selected L4T BSP + JetPack components.

-   Step 6: tegraflash.py (invoked internally) programs the BCT, MB1, MB2, UEFI, DTB, kernel, and rootfs to eMMC via USB.

-   Step 7: SDK Manager optionally installs CUDA, TensorRT, cuDNN, etc., over network to the running target.

# 12. Security Architecture

The Jetson Orin NX implements a hardware-rooted secure boot chain that provides cryptographic verification from BootROM through to the Linux kernel. This ensures that only trusted firmware and software can execute on the platform.

## 12.1 Secure Boot Chain

+--------+-----------------------------------------------------------+--------+
|        | **Hardware Root of Trust**                                |        |
|        |                                                           |        |
|        | BootROM (mask-ROM) + PKC/SBK fuses (OEM-programmed)       |        |
+========+===========================================================+========+
|        | ▼                                                         |        |
+--------+-----------------------------------------------------------+--------+
|        | **PSC (Platform Security Controller)**                    |        |
|        |                                                           |        |
|        | Key management \| Attack detection \| Trusted time        |        |
+--------+-----------------------------------------------------------+--------+
|        | ▼                                                         |        |
+--------+-----------------------------------------------------------+--------+
|        | **BootROM verifies MB1**                                  |        |
|        |                                                           |        |
|        | RSA-2048/3072 signature check using public key from fuses |        |
+--------+-----------------------------------------------------------+--------+
|        | ▼                                                         |        |
+--------+-----------------------------------------------------------+--------+
|        | **MB1 verifies MB2**                                      |        |
|        |                                                           |        |
|        | Hash chain: each stage verifies the next                  |        |
+--------+-----------------------------------------------------------+--------+
|        | ▼                                                         |        |
+--------+-----------------------------------------------------------+--------+
|        | **MB2 verifies ATF, TOS, BPMP FW, UEFI**                  |        |
|        |                                                           |        |
|        | All firmware images signed by OEM key                     |        |
+--------+-----------------------------------------------------------+--------+
|        | ▼                                                         |        |
+--------+-----------------------------------------------------------+--------+
|        | **UEFI verifies Kernel**                                  |        |
|        |                                                           |        |
|        | UEFI Secure Boot or NVIDIA custom verification            |        |
+--------+-----------------------------------------------------------+--------+
|        | ▼                                                         |        |
+--------+-----------------------------------------------------------+--------+
|        | **Kernel verifies kernel modules (optional)**             |        |
|        |                                                           |        |
|        | Module signing with in-kernel keyring                     |        |
+--------+-----------------------------------------------------------+--------+

*Figure 8: Secure Boot Chain*

## 12.2 Cryptographic Components

  -----------------------------------------------------------------------------------------------
  **Component**           **Algorithm**           **Purpose**
  ----------------------- ----------------------- -----------------------------------------------
  BootROM verification    RSA-2048/RSA-3072       MB1 signature verification

  Security Engine (SE)    AES-256-XTS, AES-128    Storage encryption, key derivation

  Security Engine (SE)    SHA-256/SHA-384         Hash computation for image verification

  Security Engine (SE)    RSA-2048/RSA-3072/ECC   Asymmetric crypto; 16 key slots

  TrustZone               Arm TrustZone/OP-TEE    Secure world isolation; TEE services

  PSC                     TRNG                    True random number generation; key generation

  fuses (OTP)             PKC public key hash     Immutable binding of public key to device
  -----------------------------------------------------------------------------------------------

## 12.3 TrustZone Memory Partitioning

The Arm TrustZone architecture divides the system into Secure World (EL3/Secure EL1) and Normal World (EL2/EL1/EL0). The SMMU-500 enforces that Normal World software cannot access Secure World memory regions. The PSC manages the most sensitive keys and secrets, accessible only by secure firmware.

# 13. Peripheral Initialization

All peripheral initialization on the Jetson Orin NX is driven by the Device Tree. The pinmux configuration, clock sources, and device parameters are fully described in the DTB, allowing the Linux kernel and user space to configure peripherals without hardcoded knowledge of the hardware.

## 13.1 Device Tree and Pinmux

The tegra-pinctrl driver reads pinmux nodes from the DTB and programs the on-chip MPIO (Multi-Purpose I/O) pad controller registers. Each MPIO pad supports pull-up/pull-down configuration, drive strength, Schmitt trigger enable, and SFIO vs. GPIO selection. On reset, pads assume deterministic PoR states designed to minimize external pull resistor requirements.

## 13.2 Peripheral Initialization Details

  --------------------------------------------------------------------------------------------------
  **Peripheral**    **Linux Driver**       **Device Node**     **Notes**
  ----------------- ---------------------- ------------------- -------------------------------------
  GPIO              tegra186-gpio          /sys/class/gpio     15 dedicated GPIOs; 1.8V CMOS

  I2C (×4)          tegra-i2c              /dev/i2c-0..3       Up to 1 Mbit/s (Fm+)

  SPI (×2)          tegra-spi              /dev/spidev0.0      Master; 64×32 FIFO; modes 0-3

  UART (×3)         tegra-uart (8250)      /dev/ttyTHS0..2     Up to 12.5 Mbps; HW flow control

  USB 3.2 (×3)      tegra-xusb (xHCI)      /dev/bus/usb/\...   USB0: Host+Device+RCM; others: Host

  PCIe Gen4         tegra194-pcie          /dev/nvme0n1        x4+x1+x2; NVMe; Wi-Fi; other cards

  CAN FD            m_ttcan                /dev/can0           Bosch TTCAN; up to 8 Mbps

  Ethernet          dwc-eqos               eth0 / enP\*        10/100/1000 BASE-T; IEEE 802.3ab

  CSI Cameras       tegra-camrtc-capture   /dev/video0..       4×2-lane MIPI CSI-2; D-PHY 2.1

  I2S (×2)          tegra-i2s (ALSA)       /dev/snd/\...       PCM/TDM audio; up to 24.576 MHz
  --------------------------------------------------------------------------------------------------

## 13.3 PCIe Initialization

The Tegra PCIe Gen4 driver initializes four PCIe controllers. Controller 0 (x4) is typically used for NVMe SSD on the Eagle-201. The driver programs the PCIe PHY, asserts PERST# (PCIE0_RST\*), clears CLKREQ#, performs link training, and enumerates attached devices. NVMe devices appear as /dev/nvme0n1 and are managed by the standard Linux nvme driver.

# 14. Boot Debugging Methods

When the system fails to boot or behaves unexpectedly, the following debugging methods provide visibility into the boot process at each stage.

## 14.1 Serial Console Debugging

The primary debug interface is UART0 (pin 99/101) exposed on the Eagle-201 carrier board as a 3.3V serial port or USB-to-serial adapter. All boot stages output log messages to UART0 at 115200 baud by default.

+----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Serial Console Connection**                                                                                                                                                                                              |
|                                                                                                                                                                                                                            |
| Connect a USB-to-serial adapter to the Eagle-201 debug UART header. Use: screen /dev/ttyUSB0 115200 or minicom -D /dev/ttyUSB0 -b 115200 BootROM, MB1, MB2, UEFI, and Linux kernel all output to this console during boot. |
+============================================================================================================================================================================================================================+
+----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

## 14.2 Boot Log Levels

  --------------------------------------------------------------------------------------------------------
  **Stage**               **Log Visibility**              **Typical Output**
  ----------------------- ------------------------------- ------------------------------------------------
  BootROM                 Limited (silent unless error)   RCM prompt if no BCT found

  MB1                     UART0 @ 115200                  DRAM training results; PMIC config

  MB2                     UART0 @ 115200                  Firmware load/verify status; ATF messages

  UEFI/CBoot              UART0 @ 115200                  Partition table; kernel load address; DTB info

  Linux Kernel            UART0 @ 115200                  Full dmesg output; driver probe messages

  User Space              UART0 / SSH                     systemd journal; application logs
  --------------------------------------------------------------------------------------------------------

## 14.3 Kernel Log Retrieval

After boot, the full kernel log is accessible via: dmesg \| less

Persistent logs are stored by systemd-journald in /var/log/journal/. Filter for boot errors with: journalctl -b -p err

## 14.4 Bootloader Logs

UEFI logs including partition enumeration, kernel loading, and DTB selection are output on UART0 during the bootloader stage. Setting bootloader debug verbosity in extlinux.conf via the LINUX_KERNEL_CMDLINE variable allows passing loglevel=8 earlyprintk=uart8250,mmio32,0x\... for earliest possible kernel console output.

## 14.5 Recovery Mode

If the system cannot boot normally, Force Recovery Mode (RCM) is initiated by:

-   Holding FORCE_RECOVERY\* (pin 214) low when SYS_RESET\* transitions high during power-on.

-   On the Eagle-201, this is typically a dedicated push-button or test point on the board.

-   The BootROM will enumerate the USB0 port as a USB device, presenting the RCM protocol.

-   The host PC running SDK Manager or tegraflash.py can then re-flash the entire eMMC.

## 14.6 Re-flashing via SDK Manager

Full re-flash procedure:

-   1\. Connect Eagle-201 USB recovery port to Ubuntu host PC.

-   2\. PUT board FORCE_RECOVERY\* mode by shorting recovery and ground header pins.

-   3\. Verify RCM mode: lsusb on host should show NVIDIA Corp. APX device.

-   4\. Launch SDK Manager: sdkmanager from terminal.

-   5\. Select Jetson Orin NX 8GB as target, select JetPack version.

-   6\. SDK Manager will flash BCT, bootloaders, kernel, and rootfs.

-   7\. After flash completes, remove recovery jumper and power-cycle for normal boot.

+------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+
| **Tip: Manual Flash**                                                                                                                                                                                                                                      |
|                                                                                                                                                                                                                                                            |
| Advanced users can invoke tegraflash.py directly from the L4T BSP directory for automated/scripted flashing without the GUI. See NVIDIA L4T Documentation: https://docs.nvidia.com/jetson/archives/r38.4/ReleaseNotes/Jetson_Linux_Release_Notes_r38.4.pdf |
+============================================================================================================================================================================================================================================================+
+------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------+

# References

-   NVIDIA Jetson Orin NX Series Data Sheet, DS-10712-001 v0.5, November 2022

-   NVIDIA SDK Manager Documentation: https://docs.nvidia.com/sdk-manager/install-with-sdkm-jetson/index.html

-   Tanna TechBiz Eagle-201 Full System Product Page: https://tannatechbiz.com/tanna-techbiz-eagle-201-full-system-with-nvidia-jetson-orin-nx-8gb-module.html

-   Tanna TechBiz Eagle-201 Carrier Board Datasheet: https://drive.google.com/file/d/1tPPl0nMCTC6nd1un2eD-C_JbgUrRMSst/view

-   NVIDIA Jetson Linux (L4T) Developer Guide: https://docs.nvidia.com/jetson/archives/

-   NVIDIA JetPack SDK Documentation: https://developer.nvidia.com/embedded/jetpack

-   Arm Cortex-A78AE Technical Reference Manual, Arm Limited

-   NVIDIA Ampere Architecture Whitepaper, NVIDIA Corporation
