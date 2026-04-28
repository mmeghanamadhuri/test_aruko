# JYQD <-> Raspberry Pi pin map

This is the wiring used by the proven prototype. It matches what
`navigation_bldc.py` in this directory expects. Don't change without
also changing the constants in that file.

## Per-wheel signals

### Left wheel (JYQD-L)

| JYQD-L screw | Function          | Pi BCM | Pi physical pin |
|--------------|-------------------|--------|-----------------|
| EL           | enable / brake     | 18     | 12              |
| Z/F          | direction          | 25     | 22              |
| VR           | PWM speed input    | 12     | 32 (PWM0)       |
| 5V           | logic supply       | -      | 2 or 4          |
| GND          | logic ground       | -      | 39              |
| Signal       | (leave unconnected) | -     | -               |
| VCC (24 V)   | motor supply       | -      | external battery |

### Right wheel (JYQD-R)

| JYQD-R screw | Function          | Pi BCM | Pi physical pin |
|--------------|-------------------|--------|-----------------|
| EL           | enable / brake     | 10     | 19              |
| Z/F          | direction          | 22     | 15              |
| VR           | PWM speed input    | 13     | 33 (PWM1)       |
| 5V           | logic supply       | -      | 2 or 4          |
| GND          | logic ground       | -      | 34              |
| Signal       | (leave unconnected) | -     | -               |
| VCC (24 V)   | motor supply       | -      | external battery |

## Status LED (optional, RGB common-anode)

| LED  | Pi BCM | Pi physical pin |
|------|--------|-----------------|
| RED  | 21     | 40              |
| GREEN| 20     | 38              |
| BLUE | 16     | 36              |

The LED helpers in `navigation_bldc.py` are active-low (write 0 to
turn the LED on).

## Optional E-stop inputs

| Signal     | Pi BCM | Pi physical pin |
|------------|--------|-----------------|
| ESP1       | 17     | 11              |
| ESP2       | 5      | 29              |

These are read-only; the bridge does not currently act on them. Wire
them if/when you have a physical E-stop switch.

## Direction polarity

`control_speed("left",  "enable", speed, "front")` -> `L_DIR HIGH`
`control_speed("right", "enable", speed, "front")` -> `R_DIR LOW`  (mirrored)

If a wheel spins the wrong way after you swap a motor:

* preferred: set `NINA_NAV_INVERT_LEFT=1` (or RIGHT) on the **Jetson**
  side - the Pi stays unchanged.
* alternatively: flip the polarity in `control_speed()` for the
  affected side. (This is the only place in this directory that
  encodes polarity.)
