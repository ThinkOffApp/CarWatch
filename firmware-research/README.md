# G900 firmware research (read-only)

Findings from reading official WOLFBOX firmware images (NO flashing done).
Source: https://eu.wolfbox.com/pages/firmware

## G900 TriPro (image 1.0.0.0.20260428, chip tag HC901)

**It is embedded Linux, not a closed RTOS.** Extracted signatures + strings:

- **SoC**: HiSilicon **Hi3519DV500** (rear sensor IMX678), A55 core, 128M RAM
- **Userland**: BusyBox, ubifs rootfs, standard `/etc/init.d/S##` init,
  `root:x:0:0::/root:/bin/sh`
- **WiFi AP**: `hostapd` present (the mirror hosts its own AP - matches the
  clip/stream pipeline assumption)
- **Kernel cmdline / flash layout** (from u-boot bootargs):
  `console=ttyAMA0,115200 ... ubi.mtd=2 root=ubi0:ubifs`
  partitions: `1M(u-boot.bin),15M(uImage-fdt-mini.gz),16M(rootfs.ubifs),
  32M(appfs.ubifs),1M(bl31.bin),...,1M(password)`

## Older G900 (non-TriPro) images

- `G900_HI3559__...` and `G900_HI3519__...` - Hi3559 / Hi3519 variants.

## Custom-UI feasibility

Possible in principle (Linux + writable 32M appfs), hard in practice:
- No telnet/ssh enabled by default; needs a root path (u-boot serial
  console over the board's UART is the classic entry).
- Flashing risks the unit -> use a SECOND G900 as the mule, never the
  daily driver.
- The camera clip + live stream we actually ship go over the wifi HTTP
  API and need none of this.

Status: someday/community track. Not on the product critical path.
