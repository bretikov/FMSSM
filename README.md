# FMSSM (FMS Save Manager)

A desktop application (Python + Tkinter) for managing save files of the
GBA groovebox **FMS** (`GSEQ` format, version 20).

The app has **two panels**:

- **SOURCE** (top) — open an existing `.sav` file here. It acts as a
  "library" of patterns you pick material from.
- **NEW COMPOSITION** (bottom) — starts as a completely **empty** save
  with the same structure (8 banks × 5 tracks × 16 slots). You move or
  copy patterns here from the source and assemble a brand-new layout,
  which you then save as a new `.sav`.

## Running it

You need Python 3.8+ with the `tkinter` module.

- **Windows / macOS**: tkinter ships with the standard Python
  installation, nothing extra to install.
- **Linux**: if `python3 -c "import tkinter"` fails, install the package:
  ```
  sudo apt install python3-tk        # Debian/Ubuntu
  sudo dnf install python3-tkinter   # Fedora
  ```

**Important: keep both `.py` files in the same folder.** `fmssm_gui.py`
imports `fmssm_format.py` as a local module - if it isn't sitting right
next to `fmssm_gui.py`, the app will fail immediately with
`ModuleNotFoundError: No module named 'fmssm_format'`. Download/copy both
files into one directory before running anything (`README.md` itself
is just documentation and isn't required at runtime).

Run:

```bash
python3 fmssm_gui.py
```

No other dependencies (no `pip install`) are required — everything is
built on the standard library.

## Files

- `fmssm_format.py` — pure library for reading/writing the format (no GUI).
  Can also be used standalone, e.g. for scripting/batch edits.
- `fmssm_gui.py` — Tkinter app built on top of the library (two-panel UI).
  **Requires `fmssm_format.py` to be present in the same folder** (see
  "Running it" above) - it has no logic of its own for parsing or
  writing the save format, it only imports it from that module.

## How to use the app

1. **File → Open source file...** — pick your existing `.sav`. It shows
   up in the top panel.
2. The bottom panel ("New Composition") is ready as an empty save right
   from the start — no need to open anything if you want to build from
   scratch. (If you'd rather edit an existing file as the composition,
   use **File → Open composition from file...** instead.)
3. Both panels have their own **Save this file** button below the bank
   list (also available as **File → Save source file** /
   **Save composition**). Edits in either panel — renaming a bank,
   moving/copying a pattern into or out of it — only live in memory
   until you explicitly save that panel; nothing is written to disk
   automatically.
4. In either panel, click a bank in the list on the left — the
   5-track (FM 1–4, Noise) × 16-slot grid for that bank shows up. Slot
   columns are numbered in **hexadecimal** (`0 1 2 3 4 5 6 7 8 9 A B C
   D E F`) to match a single-digit-per-slot layout.
5. **Select a source and a destination** (across either panel):
   - Click a cell (top or bottom) → it becomes the source (orange).
   - Click another cell (in either panel) → it becomes the destination
     (green).
6. Use the buttons in the middle:
   - **Move →** — the pattern (and its settings: length, rate, echo,
     transpose, mod, direction...) moves to the destination, the
     source is cleared. Works within a single panel and across panels.
   - **Copy →** — same, but the source stays unchanged. Works within a
     single panel and across panels. **This is the main way to build a
     new composition without losing the original.**
   - **Swap** — swaps two patterns including their settings. Only works
     **within the same panel** (swapping between the source and the
     in-progress composition doesn't make sense).
   - **Clear source** — clears the selected pattern.
7. Patterns can also be moved by **dragging with the mouse** directly in
   the grid — this also works between the top and bottom panel (= Move).
   If you want to keep the source intact, use the Copy button instead.
8. Right-clicking a cell opens a quick menu (select as source/destination,
   clear).
9. If you select a bank in the bottom panel that has never been used
   ("empty") and copy/move something into it, it's created automatically.
10. Once you're done: save either or both panels with their **Save this
    file** button (or the matching **File** menu entries).

## Important limitations (inherent to the format itself)

- **FM and Noise don't mix.** FM steps (24 B) and Noise steps (12 B) use
  a different format, so a pattern cannot be moved/copied/swapped
  between an FM track and the Noise track. The app rejects this with an
  error message.
- **Banks from older firmware ("legacy")** — if a bank was saved by an
  older version of the FMS firmware (a different internal metadata
  layout than the current v20), the app detects it and shows it as
  `[LEGACY vX]`. Such a bank cannot be edited, nor can patterns be
  copied out of it (to avoid misinterpreting an unknown old layout) —
  but it's left untouched in the original file, and writing simply
  copies it 1:1 with no data loss.
- **Capacity.** Each bank can have at most 5 sectors (20 KB), and all
  8 banks together must fit into the data pool (31 sectors on Flash /
  7 on the SRAM build). If an edit would exceed the limit, the action
  fails with an error message instead of corrupting the file.
- The app **recalculates and repacks every sector from scratch** on
  every save — this is completely normal and is the same approach FMS
  itself uses when saving.
- **The source file on disk is never modified** until you explicitly
  save it (and by default only the composition gets saved, not the
  source) — even a Move from source to composition only changes the
  source in the running app's memory.
- **Save format variants.** Both the 128 KB Flash build and the 32 KB
  SRAM build are supported as either panel's file. An unused bank can
  be marked in the directory either as `sectorStart = 0` or as
  `sectorStart = 0xFF` (raw erased memory) depending on which build /
  firmware version wrote the file — both are recognized as "bank does
  not exist".

## Correctness verification

The `fmssm_format.py` library was tested by loading a real `.sav` file,
rebuilding it (without any edits) and comparing it byte-for-byte against
the original — the result matches. Also verified: moving/copying/
swapping patterns within a single bank and across banks of the same
file, and — for the two-panel model — copying and moving patterns
**between two independent `SaveFile` instances** (source → new
composition), including automatic creation of the destination bank and
confirmation that the source `SaveFile` stays byte-for-byte untouched
after a Copy operation. Both a 128 KB Flash sample file and a 32 KB
SRAM sample file were used during testing.

## Changelog

- **Renamed the project to FMSSM.** The files are now `fmssm_gui.py`
  and `fmssm_format.py` (previously `fms_gui.py` and `fms_format.py`).
  If you have an older copy of this tool, delete the old `fms_gui.py` /
  `fms_format.py` files so there's no confusion about which pair
  belongs together — the new `fmssm_gui.py` only imports from
  `fmssm_format.py`, not from the old names.
- **Slot columns are now numbered in hexadecimal.** Both the grid's
  column headers and every status/dialog message that mentions a slot
  number (e.g. "Source selected: ... slot A") now use a single hex
  digit (`0-9, A-F`) instead of a two-digit decimal number (`0-15`),
  so every slot lines up under exactly one column width.
- **Added Save/Save As for the source panel.** Previously, the source
  panel had a "Rename bank" button but no way to actually write that
  change (or any pattern edits made to the source via Move) back to
  disk — only the composition panel could be saved. Both panels now
  have a **Save this file** button below their bank list, plus
  matching **File → Save source file** / **Save source file as...**
  menu entries (`Ctrl+Shift+S`). The title bar now shows an unsaved-
  changes marker (`*`) independently for the source and the
  composition file.
- **Pattern length display.** The number shown inside a filled (blue)
  cell is the pattern's configured **length** (`fmPatLength` /
  `nsPatLength`, e.g. 6, 8, 12, 16...), not the number of active
  trigger steps as in an earlier version.
- **Fixed: clicking a destination cell in an empty bank did nothing.**
  Pressing the mouse button down on a cell always records that cell as
  a possible drag origin; whether the bank is actually editable is now
  checked only when an action (Move) is performed, not when the click
  itself is registered. Previously, an empty/legacy bank's guard check
  ran too early and silently swallowed the click, so the destination
  cell never turned green and the status bar never updated, even though
  nothing was technically broken in the underlying move/copy logic.
- **Added SRAM save support as a source/destination file.** Some save
  files mark an unused bank's directory entry as `sectorStart = 0xFF`
  (raw erased flash/SRAM memory) instead of `sectorStart = 0` — this is
  common on the 32 KB SRAM build. The parser previously only recognized
  `0` as "bank does not exist" and would crash trying to read bank data
  from a bogus location. Both representations are now treated as "no
  bank here". Byte-for-byte round-trip fidelity for unused-bank name
  fields (`0xFF` "uninitialized" vs. `0x24` "explicit dash", both of
  which display as `----`) was also preserved more precisely.
- **Full English translation.** Every user-facing string (menus,
  buttons, dialogs, status messages) as well as all code comments and
  docstrings in both `fmssm_gui.py` and `fmssm_format.py` are now in
  English.

## Possible future extensions (not in this version)

- A detail view for individual pattern steps (notes, lengths, echo
  settings).
- Editing/importing FM and Noise presets (offset 0x00A5 / 0x0177 in
  sector 0).
- Bulk operations (e.g. "copy an entire bank at once").
- Backing up the original file before saving (`.sav.bak`).
