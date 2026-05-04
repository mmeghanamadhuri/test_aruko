#!/usr/bin/env bash
# Brings up a Slamtec RPLIDAR S2E lidar on a Jetson running Ubuntu
# 22.04 / JetPack 6.x and verifies the Nina stack can talk to it.
#
# What this script does (in order):
#
#   1. Verifies we're on aarch64. On x86 / Mac the install hints
#      adapt to a plain `pip install pyrplidarsdk`.
#   2. apt-installs build deps used by pyrplidarsdk's nanobind
#      backend (build-essential, python3-dev, cmake, git) and a
#      couple of network-debug helpers (iputils-ping, nmap-utility).
#   3. pip-installs `pyrplidarsdk` (PyPI; with --break-system-packages
#      fallback for the JetPack 6 / PEP 668 case) - that's the
#      Python wrapper around Slamtec's official rplidar_sdk that
#      Nina's `nina.sensors.slamtec_s2e.SlamtecS2E` driver uses.
#   4. Probes the network: makes sure ONE Ethernet interface looks
#      like it can reach 192.168.11.2 (the lidar's factory default
#      IP). If nothing on the host is in 192.168.11.0/24, offers to
#      configure the first wired interface to 192.168.11.10/24 via
#      NetworkManager (preferred on JetPack desktop) or a tmpfs
#      systemd-networkd drop-in (fallback for headless images).
#   5. `ping -c 3 192.168.11.2` so a wiring fault is visible at
#      install time, not 20 minutes later when the operator is
#      already inside the GUI debugging "Lidar sim" pills.
#   6. Smoke-tests `pyrplidarsdk.RplidarDriver(ip_address=...)` end
#      to end - connect, get_device_info, start_scan, get_scan_data
#      x10, stop_scan, disconnect. If the S2E firmware is wedged in
#      protection mode (post-power-glitch, dust on the optics) this
#      surfaces it as a non-zero health code with a "send a reset
#      cycle" hint, instead of a silent failure at GUI launch.
#   7. Writes a sample env-var snippet for
#      `desktop/nina-ui-kiosk.service` so the kiosk picks the right
#      lidar model on next start.
#
# Why this is a separate script (not just the readme):
#
#   * The PEP 668 / `--break-system-packages` dance is the same
#     trip-up we already hit with breezyslam; new operators were
#     reporting "pip install pyrplidarsdk failed" without realising
#     JetPack 6 marks the system Python as externally-managed.
#   * The 192.168.11.0/24 static-IP step is non-obvious - the lidar
#     ships configured for that subnet but nothing on a fresh
#     Jetson is. Without this script most operators try DHCP and
#     wonder why ping never replies.
#   * Driver health (`get_health()`) is reported by the S2E
#     firmware in a status code that's easy to miss in the Python
#     library output. Surfacing it here keeps install-time
#     diagnostics honest.

set -euo pipefail

# --------------------------------------------------------------------
# Knobs (override via env)
# --------------------------------------------------------------------

LIDAR_IP="${LIDAR_IP:-192.168.11.2}"
LIDAR_UDP_PORT="${LIDAR_UDP_PORT:-8089}"
HOST_IP="${HOST_IP:-192.168.11.10}"
HOST_NETMASK="${HOST_NETMASK:-24}"
PIP_PACKAGE="${PIP_PACKAGE:-pyrplidarsdk}"

log()  { printf "\033[1;34m[s2e]\033[0m %s\n" "$*"; }
warn() { printf "\033[1;33m[s2e]\033[0m %s\n" "$*" >&2; }
die()  { printf "\033[1;31m[s2e]\033[0m %s\n" "$*" >&2; exit 1; }

# --------------------------------------------------------------------
# 1) Pre-flight
# --------------------------------------------------------------------

arch="$(uname -m)"
if [[ "${arch}" != "aarch64" ]]; then
    warn "this script is tuned for Jetson (aarch64); detected ${arch}.
Falling through to a best-effort pip install. On x86 / Mac:
    pip install ${PIP_PACKAGE}
should be enough; the network-config step below assumes Linux nmcli /
networkd and will skip silently."
fi

PYTHON_EXEC="$(command -v python3)"
if [[ -z "${PYTHON_EXEC}" ]]; then
    die "python3 not on PATH; install it first (sudo apt install -y python3)."
fi

PY_VERSION="$("${PYTHON_EXEC}" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
log "Using ${PYTHON_EXEC} (Python ${PY_VERSION})"

# --------------------------------------------------------------------
# 2) apt build deps
# --------------------------------------------------------------------

if [[ "${arch}" == "aarch64" ]]; then
    log "Installing apt build deps + network helpers (sudo password may be required)"
    sudo apt update
    sudo apt install -y \
        build-essential \
        python3-dev \
        python3-pip \
        cmake \
        git \
        iputils-ping \
        iproute2 \
        net-tools
fi

# --------------------------------------------------------------------
# 3) pip install pyrplidarsdk
# --------------------------------------------------------------------
#
# pyrplidarsdk ships pre-built wheels for Linux aarch64 / x86_64;
# the build-from-source path (used if no wheel matches) needs the
# apt deps installed above. JetPack 6 / Ubuntu 22.04 marks the
# system Python as externally-managed (PEP 668), so we try
# `pip install --user` first and retry with --break-system-packages
# on the EXTERNALLY-MANAGED rejection.

install_sdk() {
    local extra=("$@")
    log "pip install --user ${extra[*]} ${PIP_PACKAGE}"
    "${PYTHON_EXEC}" -m pip install --user "${extra[@]}" "${PIP_PACKAGE}"
}

if ! install_sdk ; then
    warn "pip install --user failed; retrying with --break-system-packages
(safe for user-only installs on JetPack 6 / Ubuntu 22.04 - we're not
modifying the system distro packages, only the user's site-packages)."
    install_sdk --break-system-packages
fi

# --------------------------------------------------------------------
# 4) Network configuration
# --------------------------------------------------------------------
#
# The S2E ships listening on 192.168.11.2 / UDP 8089. We need ONE
# host interface in that subnet. Three cases:
#
#   a) Already configured (HOST_IP already routable to LIDAR_IP) -
#      do nothing.
#   b) NetworkManager is running (default on JetPack desktop) -
#      use nmcli to add a static-IP profile for the wired conn.
#   c) systemd-networkd or nothing - drop a tmpfs networkd unit
#      and `networkctl reload`.

probe_existing_route() {
    # `ip route get LIDAR_IP` is too permissive on its own: on a
    # fresh Jetson with Wi-Fi up, the kernel returns the default
    # route (via wlan0 -> default gateway) and we'd falsely
    # conclude the lidar is reachable. We need the chosen source
    # address to be on the lidar's own /24, which only happens if
    # SOME interface is actually configured into 192.168.11.0/24.
    local src prefix
    src="$(ip route get "${LIDAR_IP}" 2>/dev/null \
            | sed -n 's/.*src \([0-9.]*\).*/\1/p')"
    if [[ -z "${src}" ]]; then
        return 1
    fi
    prefix="${LIDAR_IP%.*}."
    if [[ "${src}" != ${prefix}* ]]; then
        log "found a route to ${LIDAR_IP} via src=${src} - that's not"
        log "  on ${prefix}0/${HOST_NETMASK}, so it's the default-gateway"
        log "  fallback (Wi-Fi etc). Reconfiguring the wired interface."
        return 1
    fi
    return 0
}

# Pick the most-likely wired Ethernet interface, *deprefering*
# USB-tethered NICs (iPhone hotspot, Android tether, USB-Ethernet
# dongles) which all show up as `enx<MAC>` per the systemd
# predictable-name convention.
#
# Order of preference:
#   1. `enP*` (Jetson onboard - only the integrated 1GbE matches)
#   2. `eth*` / `eno*` / `ens*` / `enp*` (PCIe / motherboard NICs)
#   3. anything else starting with `e` *except* `enx*` (USB tether)
#
# We skip enx* entirely because routing a 192.168.11.0/24 static
# through someone's iPhone is never what's wanted, and worse, the
# iPhone subnet (172.20.10.0/28) provides a default route that
# masks the missing wired config in `ip route get LIDAR_IP`.
pick_wired_iface() {
    local ifaces
    mapfile -t ifaces < <(
        ip -o link \
            | awk -F': ' '$2 !~ /^lo|docker|veth|virbr|wl/ {print $2}'
    )
    local p1="" p2="" p3=""
    for i in "${ifaces[@]}"; do
        case "$i" in
            enP*)             p1="$i" ;;
            eth*|eno*|ens*|enp*) [[ -z "$p2" ]] && p2="$i" ;;
            enx*)             ;;   # USB tether - skip
            e*)               [[ -z "$p3" ]] && p3="$i" ;;
        esac
    done
    [[ -n "$p1" ]] && { echo "$p1"; return 0; }
    [[ -n "$p2" ]] && { echo "$p2"; return 0; }
    [[ -n "$p3" ]] && { echo "$p3"; return 0; }
    return 1
}

configure_via_nmcli() {
    if ! command -v nmcli >/dev/null 2>&1; then
        return 1
    fi
    local iface
    iface="$(pick_wired_iface)" || true
    if [[ -z "${iface}" ]]; then
        return 1
    fi

    # If a profile already exists for this iface, modify it. If not,
    # create a fresh dedicated profile - we name it `nina-lidar` so
    # it's obvious in `nmcli con show`.
    local con_name
    con_name="$(nmcli -g GENERAL.CONNECTION dev show "${iface}" 2>/dev/null \
        | head -n1)"
    if [[ -z "${con_name}" || "${con_name}" == "--" ]]; then
        con_name="nina-lidar"
        log "Creating NetworkManager profile '${con_name}' on ${iface} -> ${HOST_IP}/${HOST_NETMASK}"
        # Idempotent: delete an old profile of the same name first.
        sudo nmcli con delete "${con_name}" >/dev/null 2>&1 || true
        sudo nmcli con add type ethernet ifname "${iface}" \
            con-name "${con_name}" \
            ipv4.addresses "${HOST_IP}/${HOST_NETMASK}" \
            ipv4.method manual \
            ipv4.never-default yes \
            ipv6.method ignore
    else
        log "Modifying NetworkManager profile '${con_name}' on ${iface} -> ${HOST_IP}/${HOST_NETMASK}"
        sudo nmcli con mod "${con_name}" \
            ipv4.addresses "${HOST_IP}/${HOST_NETMASK}" \
            ipv4.method manual \
            ipv4.never-default yes \
            ipv6.method ignore
    fi
    sudo nmcli con up "${con_name}" >/dev/null
    return 0
}

configure_via_networkd() {
    local iface
    iface="$(pick_wired_iface)" || true
    if [[ -z "${iface}" ]]; then
        return 1
    fi
    log "Configuring systemd-networkd unit for ${iface} -> ${HOST_IP}/${HOST_NETMASK}"
    sudo tee "/etc/systemd/network/40-nina-lidar-${iface}.network" >/dev/null <<EOF
[Match]
Name=${iface}

[Network]
Address=${HOST_IP}/${HOST_NETMASK}
LinkLocalAddressing=no
EOF
    sudo systemctl enable --now systemd-networkd >/dev/null
    sudo networkctl reload >/dev/null 2>&1 || true
    return 0
}

if probe_existing_route ; then
    log "Host already has a route to ${LIDAR_IP}; skipping network config"
else
    log "No existing route to ${LIDAR_IP}; configuring host"
    if ! configure_via_nmcli ; then
        configure_via_networkd \
            || warn "could not auto-configure a wired interface;
manually ensure your Jetson Ethernet port is at ${HOST_IP}/${HOST_NETMASK}
and re-run this script."
    fi
    # Give the link a beat to settle.
    sleep 2
fi

# --------------------------------------------------------------------
# 5) Ping smoke test
# --------------------------------------------------------------------

log "ping -c 3 ${LIDAR_IP}"
if ! ping -c 3 -W 1 "${LIDAR_IP}" ; then
    # Surface the actual routing decision the kernel just made so
    # the operator doesn't have to guess which interface the ping
    # went out on.
    log "kernel routing decision for ${LIDAR_IP}:"
    ip route get "${LIDAR_IP}" 2>&1 | sed 's/^/    /' || true
    log "configured interfaces:"
    ip -4 -o addr show | awk '{print "    "$2"\t"$4}' || true
    die "no response from ${LIDAR_IP}.
Things to check before re-running:
  * The 12 V power adapter is connected to the lidar's barrel jack
    (USB will NOT power the S2E; the optics motor needs ~1 A @ 12 V).
  * The Ethernet cable is plugged in. The link LED on the lidar's
    Ethernet adapter board should be solid green. Confirm with
    \`ethtool <iface>\` - 'Link detected: yes' is the bare minimum.
  * The Jetson's Ethernet interface really is in 192.168.11.0/24.
    The 'configured interfaces' list above should contain a wired
    iface with \`${HOST_IP}/${HOST_NETMASK}\`. If it doesn't, the
    static-IP step earlier in this script silently no-op'd - usually
    because the wired iface wasn't 'active' to NetworkManager.
    Force it with:
        nmcli device status                      # find the wired iface
        sudo nmcli con add type ethernet ifname <iface> \\
            con-name nina-lidar \\
            ipv4.addresses ${HOST_IP}/${HOST_NETMASK} \\
            ipv4.method manual ipv4.never-default yes \\
            ipv6.method ignore
        sudo nmcli con up nina-lidar
    Then re-run this script."
fi

# --------------------------------------------------------------------
# 6) Driver smoke test
# --------------------------------------------------------------------
#
# pyrplidarsdk.RplidarDriver(ip_address=...) does the full SLAMTEC
# handshake (GET_INFO, GET_HEALTH, EXPRESS_SCAN). If any of those
# fail we bail with the same diagnostic the GUI's Map tab would
# eventually surface, just minutes earlier.

# --------------------------------------------------------------------
# 6a) UDP receive buffer tuning
# --------------------------------------------------------------------
#
# The S2E pumps scan data over UDP at ~3 MB/s in Boost mode. Linux's
# default `net.core.rmem_max` (212 992 B) is below one full sweep,
# so the kernel happily drops packets and the SDK returns
# "Failed to grab scan data." even though the lidar is streaming.
# We bump it to 8 MiB and persist it via /etc/sysctl.d so subsequent
# boots stay good. This is harmless on a Jetson - that buffer is
# only allocated on demand by sockets that actually use it.

log "Tuning UDP receive buffers (net.core.rmem_max -> 8 MiB)"
sudo tee /etc/sysctl.d/99-slamtec-s2e.conf >/dev/null <<'EOF'
# Raised for SLAMTEC S2E lidar (UDP scan stream ~3 MB/s, default
# 213 KB rmem_max drops sweep packets). Safe to leave on; the kernel
# only allocates this when a socket asks for it.
net.core.rmem_default = 1048576
net.core.rmem_max     = 8388608
EOF
sudo sysctl --quiet -p /etc/sysctl.d/99-slamtec-s2e.conf || true

# --------------------------------------------------------------------
# 6b) Driver smoke test
# --------------------------------------------------------------------

log "Smoke-testing the Python driver (8 s window for motor spin-up)"
"${PYTHON_EXEC}" - "${LIDAR_IP}" "${LIDAR_UDP_PORT}" <<'PY'
import sys, time

ip = sys.argv[1]
port = int(sys.argv[2])

try:
    import pyrplidarsdk
except Exception as exc:
    print(f"  IMPORT FAILED: {exc}")
    sys.exit(1)

drv = pyrplidarsdk.RplidarDriver(ip_address=ip, udp_port=port)
if not drv.connect():
    print(f"  CONNECT FAILED to udp://{ip}:{port}")
    sys.exit(1)

info = drv.get_device_info()
if info is not None:
    print(
        f"  device: model={info.model} fw={info.firmware_version} "
        f"hw={info.hardware_version} sn={info.serial_number}"
    )

# Health: status 0 = OK, 1 = Warning, 2 = Error. We surface the
# name, not the raw integer, so the operator doesn't need to grep
# the SDK headers.
_HEALTH_NAMES = {0: "OK", 1: "WARNING", 2: "ERROR"}
health = drv.get_health()
if health is not None:
    status = getattr(health, "status", 0)
    name = _HEALTH_NAMES.get(status, f"UNKNOWN({status})")
    err = getattr(health, "error_code", 0)
    print(f"  health: {name} (status={status}, error_code={err})")
    if status == 2:
        print("  ERROR-state health: the firmware is in protection mode.")
        print("  Power-cycle the lidar (12 V barrel jack) and re-run.")
        sys.exit(1)

if not drv.start_scan():
    print("  START_SCAN FAILED")
    sys.exit(1)

# 8-s window. Motor spin-up on a cold S2E is 0.5-1.5 s, then the
# SDK's grabScanDataHq() call blocks ~250 ms waiting for a complete
# sweep to accumulate. So we expect 0 batches for the first ~1 s,
# then ~30-50 batches for the remaining ~7 s.
n_batches = 0
n_points = 0
n_empty = 0
n_errors = 0
deadline = time.monotonic() + 8.0
warmed = False
warmup_logged = False
while time.monotonic() < deadline:
    try:
        batch = drv.get_scan_data()
    except Exception as exc:
        n_errors += 1
        time.sleep(0.05)
        continue
    if batch:
        try:
            angles, _ranges, _q = batch
        except Exception:
            angles = batch
        n_batches += 1
        n_points += len(angles)
        if not warmed:
            warmed = True
            print(f"  first sweep arrived at t={time.monotonic() - (deadline - 8.0):.1f}s")
    else:
        n_empty += 1
        # During warmup pyrplidarsdk prints 'Error: Failed to grab
        # scan data' on stderr for each empty grab. That's noise -
        # tell the operator they're cosmetic so they don't panic.
        if not warmed and not warmup_logged and n_empty >= 2:
            print("  (warmup: 'Failed to grab scan data' lines above are normal")
            print("   - the SDK's 250 ms grab times out until the motor is at speed)")
            warmup_logged = True
    time.sleep(0.05)

drv.stop_scan()
drv.disconnect()

if n_points == 0:
    print(f"  GOT 0 POINTS in 8 s ({n_empty} empty grabs, {n_errors} exceptions);")
    print(f"  lidar is reachable but never streamed a complete sweep.")
    print("")
    print("  Most likely fixes (in priority order):")
    print("   1. POWER-CYCLE THE LIDAR. Unplug the 12 V barrel jack for")
    print("      ~5 s, plug it back in, wait for the head to spin up,")
    print("      then re-run this script. Slamtec firmware can latch")
    print("      into a half-armed state after a connection drops")
    print("      during start_scan, and only a power cycle clears it.")
    print("   2. Confirm 12 V is actually present at the barrel jack.")
    print("      The S2E motor draws ~1 A; USB cannot power it.")
    print("   3. Watch the wire: in another terminal run")
    print(f"          sudo tcpdump -i any -n udp port {port} -c 20")
    print("      then re-run this script. If tcpdump prints zero")
    print("      packets the lidar's TX is dead (firmware / power).")
    print("      If it prints packets but the smoke test still gets")
    print("      0 points, the bottleneck is the host UDP buffer -")
    print("      check `sysctl net.core.rmem_max` is 8388608.")
    sys.exit(1)

rate_hz = n_batches / 8.0
pts_per_batch = n_points / max(n_batches, 1)
print(f"  OK - {n_points} points across {n_batches} batches in 8 s")
print(f"       ~{rate_hz:.1f} batches/s, ~{pts_per_batch:.0f} pts/batch")
if n_errors:
    print(f"       (saw {n_errors} transient grab errors - normal during spin-up)")
PY

# --------------------------------------------------------------------
# 7) Reminder: env vars for the kiosk
# --------------------------------------------------------------------

cat <<EOF

Slamtec S2E ready.

To make Nina use this lidar permanently, set NINA_LIDAR_MODEL=s2e
in the kiosk service file. The repo's desktop/nina-ui-kiosk.service
already takes the default; you only need to set this if you've
overridden NINA_LIDAR_MODEL=a1 elsewhere.

If you want to verify from inside the GUI: launch the Nina UI,
open the Map tab, and the SLAM pill should turn green within ~5 s
of motion. If it stays "Lidar sim" check:

    journalctl --user -u nina-ui-kiosk -f | grep -E 'slam|lidar|s2e'

The driver logs the lidar's serial number / firmware once on
connect, so a working bring-up shows up as something like
'Slamtec lidar info: model=... fw=... sn=...'.

Re-run this script any time you suspect the lidar - the smoke
test catches power, network and firmware issues separately.
EOF
