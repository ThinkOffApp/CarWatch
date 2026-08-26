# G900 firmware research (read-only)

Findings from reading official WOLFBOX firmware images (NO flashing done).
Source: https://eu.wolfbox.com/pages/firmware

## Getting the images

The firmware images themselves are **not** redistributed here - they are
WOLFBOX's files, and shipping 48 MB of someone else's binaries in a repo
people clone is both a licence question and a tax on every clone. Download
them from the vendor link above and verify:

| File | Bytes | SHA-256 |
|------|-------|---------|
| `g900-tripro-20260512.zip` | 22392468 | `a1d219e00188f86d6ab64d77c1f1d72cadcebf6538989d1ed36b18f7d4400b75` |
| `upgrade_HC901_95ebca64ba504df24273aa6c34be78c6_1.0.0.0.20260428.appsw` | 28195732 | `c33b4282e8436c1c85999725d1a5662d11ed5f7330537d65184ea25713acb0e8` |

```bash
shasum -a 256 g900-tripro-20260512.zip
```

Everything below was read from exactly those two images. The findings are the
point; the blobs are just where they came from.

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
