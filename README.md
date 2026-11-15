# MMTX · MultiMedia Transfer

A desktop app for getting footage off camera cards and onto archive drives without ever wondering if a file made it. Every file is checksummed on the way in, read back from the destination disk, and compared. If MMTX says it's done, it's bit-perfect.

![Offload](docs/1_offload.png)

## What it does

| Tab | From → To | Grouped by |
|---|---|---|
| **Offload card** | SD card / camera → working drive (NVMe) | **Shooting date** → `YYYY_MM_DD_<Title>` folders |
| **Archive** | Working drive → archive drive (SATA) | **Folder**, sorted into `Archive/YYYY/` (or `YYYY/YYYY_MM/`, or flat) |
| **Verify** | Any folder or a whole archive drive | Re-reads everything and compares against the recorded checksums |

## Quick start

**Windows:** double-click `run_mmtx.bat`. First launch creates a local `.venv` and installs PySide6 + xxhash (needs Python 3.10+).

**Linux / macOS:** `./run_mmtx.sh`

**Or:** `pip install .` and then run `mmtx`

**Standalone .exe (no Python on the target PC):** run `build_exe.bat`, which produces `dist\MMTX\MMTX.exe`.

| Live progress | Archive | Verify |
|---|---|---|
| ![progress](docs/2_offload_progress.png) | ![archive](docs/3_archive.png) | ![verify](docs/4_verify.png) |

## Daily workflow

1. **Plug in the card.** MMTX detects it within a few seconds, figures out the camera, scans it, and lists every shooting day. Days that aren't on the working drive yet are pre-selected.
2. **Set titles.** The default title (e.g. `DailyRun`) fills every day. Double-click a title to change one day (`Beach Sunrise` → `2026_11_13_Beach_Sunrise`). If a folder for that date already exists on the working drive, its title is picked up automatically and new files merge into it.
3. **Click Offload.** You get a summary: copied, already there, failed, speed. Green means every file was read back and matched.

When you're done editing:

4. **Archive tab** → pick the working drive and the archive drive → **Older than 30 days** (or tick folders, or filter by date range) → **Move to archive**. Each folder is copied, verified against both its fresh hash and the checksum recorded at offload, and only then removed from the working drive.

Months later: **Verify tab** → point it at the archive drive → **Verify checksums**. You get a per-folder report of what's bit-perfect, what has rotted and what's missing.

## Folder layout

```
D:\Footage\  (working drive)                F:\Archive\  (archive drive, "Year folders")
  2026_11_14_DailyRun\                        2026\
    GoPro_HERO13_Black\                         2026_11_09_DailyRun\
      GX010001.MP4  GL010001.LRV  ...           2026_11_13_Beach_Sunrise\
    DJI\                                          GoPro_HERO13_Black\ ...
      DJI_20261114063012_0002_D.MP4 .LRF .SRT     Beach_Sunrise.prproj   <- edit files come along
    Insta360_X4\                                  _mmtx_checksums.xxh128
      VID_20261114_062000_00_001.insv ...
    Sony_FX3\
      C0001.MP4  C0001M01.XML  C0001S03.MP4
    _mmtx_checksums.xxh128
```

Camera subfolder names are editable in the Offload tab and remembered per camera (rename `DJI` to `Mini4Pro` once and it sticks). Turn off **Camera subfolders** to drop everything flat into the day folder.

## Supported cameras

| Camera | Card layout recognized | Shooting date comes from |
|---|---|---|
| **GoPro** HERO / MAX | `DCIM/1xxGOPRO`: MP4, LRV, THM, JPG, GPR, WAV, 360 | File timestamp. Model read from `MISC/version.txt` |
| **DJI** drones, Osmo Action / Pocket / 360 | `DCIM/DJI_001`, `DCIM/100MEDIA`, `PANORAMA`: MP4, LRF, SRT, JPG, DNG, OSV | Timestamp in the filename (`DJI_YYYYMMDDhhmmss_…`), else file time |
| **Insta360** X-series, GO, Ace | `DCIM/Camera01`: insv, insp, lrv, mp4, dng | Timestamp in the filename. Model sniffed from the `.insv` |
| **Sony** FX3 / FX30 / A7S III / any XAVC body | `PRIVATE/M4ROOT/CLIP` (+ `M01.XML`), `SUB` proxies, `THMBNL`, `DCIM/100MSDCF` | `CreationDate` in the XML sidecar (ignores a wrong file clock). Model from the XML |
| **Anything else** | Media files anywhere on the card | File timestamp (Pixel `PXL_` names parsed) |

A clip's proxy, thumbnail and sidecar always land on the same day as the clip, even when recording runs past midnight. Camera junk (`MISC`, `MEDIAPRO.XML`, `STATUS.BIN`, log files) is ignored.

## How verification works

1. Each file is copied to `name.mmtx-partial` while the **source stream is hashed** (XXH3-128 by default; it hashes far faster than any drive can read, so the disk stays the bottleneck).
2. The copy is fsync'd and **evicted from the OS cache** (Linux `posix_fadvise`, macOS `F_NOCACHE`, Windows unbuffered `FILE_FLAG_NO_BUFFERING` read), so the next step reads the actual disk rather than RAM.
3. The destination is **read back and hashed**. A match renames it to its final name. A mismatch deletes it, retries once, then marks the file as failed.
4. A manifest `_mmtx_checksums.xxh128` is written at the root of every day / archive folder. It uses the standard `hash  path` format, so `xxh128sum -c` (or `sha256sum -c` if you pick SHA-256) also works without MMTX.
5. On **archive**, the source hash is also compared to the manifest from offload. That catches corruption that happened on the working drive in between. Folders where that happens are copied but **kept** on the working drive and flagged.

## Safety rules

- **Cards are read-only** unless you tick *Delete from card after verify*. Even then, only days where every file verified are touched.
- **Nothing is overwritten.** If the same name already exists and its checksum matches, the file is skipped and counted as verified (this is how re-running an interrupted offload resumes). If the same name holds different content, the new file is saved as `name_1.ext`.
- **Move = copy + verify + delete.** A folder is removed from the source only after every file in it verified.
- Archive moves on the **same physical drive** are an instant rename.
- **Cancel / pulled card:** the in-flight `.mmtx-partial` is removed. Completed files stay verified and in the manifest, so the next run resumes.
- Checks for **free space** and the **FAT32 4 GB file limit** before starting.
- **Camera timestamps are preserved** on every copied file. Read-only attributes are not carried over.
- Every job writes a full log (Settings → Logs → Open).

## Settings

- **Checksum:** XXH3-128 (default), XXH64, SHA-256, MD5. Folders that already have a manifest keep its algorithm.
- **Read-back verify:** on by default. Turning it off still records source checksums (for later Verify runs) but only checks the destination size. Roughly halves the time on slow archive drives.
- **Bypass OS cache on read-back:** on by default.
- **Spaces → underscores** in titles (`Morning Run` → `2026_11_14_Morning_Run`).
- **Keep card folder structure** (for Sony Catalyst Browse, which wants `PRIVATE/M4ROOT/…`).
- **Rename GoPro chapters** so they sort together: `GX010123.MP4` → `GX0123_01.MP4`.

## Notes

- **USB connections:** cameras that connect over USB as MTP (several GoPro models, phones) don't appear as a drive. Use a card reader. Insta360 X-series and DJI drones in USB storage mode work fine.
- **Sony cards:** deleting clips outside the camera leaves its clip database out of sync (the camera will offer to repair it). Formatting in camera is cleaner, so *Delete from card* is off by default.
- Network shares are never auto-listed as cards, but you can still Browse to one as a destination.
- To rename the app, change `APP_NAME` in `mmtx/__init__.py`.

## Tests

```
python tests/test_engine.py          # engine: offload, resume, corruption, conflicts, cancel, archive, bit rot, verify
python tests/gui_smoke.py shots/     # drives the real window headless end to end and saves screenshots
python tests/make_fake_cards.py out/ # fake GoPro / DJI / Insta360 / FX3 cards to play with
```

## Layout

```
mmtx/
  hashing.py     checksums + cache-bypassing readers (Linux / macOS / Windows)
  manifest.py    _mmtx_checksums.* read / write (xxhsum / sha256sum compatible)
  transfer.py    copy -> read-back verify -> rename engine, move/delete rules, Verify + Create-checksum runners
  cameras.py     GoPro / DJI / Insta360 / Sony detection, roles, capture dates, models
  scanner.py     card scan grouped by day; working-drive scan of YYYY_MM_DD_<Title> folders
  planner.py     scans + choices -> jobs; folder naming; archive layouts
  settings.py    JSON settings in %APPDATA%\MMTX (Windows), ~/.config/mmtx (Linux)
  ui/            PySide6 window, tabs, progress panel, dialogs, theme
```
