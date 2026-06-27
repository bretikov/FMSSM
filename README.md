# FMSSM (FMS Save Manager)

Python script for managing save files of the
GBA groovebox **FMS** by fors.fm

The script has **two panels**:

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

## How to use

1. **File → Open source file...** — pick your existing `.sav`. It shows
   up in the top panel.
2. The bottom panel ("New Composition") is ready as an empty Flash
   (128 KB) save right from the start — no need to open anything if
   you want to build from scratch. To start a SRAM (32 KB) composition
   instead, or to discard the current one and start over, use
   **File → New empty composition**; a small dialog lets you pick
   Flash or SRAM. (If you'd rather edit an existing file as the
   composition, use **File → Open composition from file...** instead —
   its build is read from the file itself.)
3. Each panel's bank list has **Rename bank**, **Clear bank**, and
   **Save this file** buttons. **Clear bank** frees the selected bank
   completely (patterns, name, everything) so it becomes empty, as if
   it had never been saved — it asks for confirmation first and shows
   how many patterns are in it. Edits in either panel — renaming or
   clearing a bank, moving/copying a pattern into or out of it — only
   live in memory until you explicitly save that panel; nothing is
   written to disk automatically.
4. In either panel, click a bank in the list on the left — the
   5-track (FM 1–4, Noise) × 16-slot grid for that bank shows up. Slot
   columns are numbered in **hexadecimal** to match a single-digit-per-slot.
5. **Select a source and a destination** (across either panel):
   - Click a cell (top or bottom) → it becomes the source (orange).
   - Click another cell (in either panel) → it becomes the destination
     (green).
   - Click a **column header** (the hex slot number) instead of a cell
     to select all 5 tracks of that slot at once — useful for
     reordering or copying an entire step across all tracks in one go.
     You can't mix a single-cell selection with a whole-column one.
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

## Aditional informations

- **FM and Noise don't mix.** FM steps and Noise cannot be
  moved/copied/swapped between an FM track and the Noise track.
  The app rejects this with an error message.
- **Banks from older FMS versions ("legacy")** — if a bank was saved by an
  older version of the FMS (a different internal metadata
  layout than the current v20), the app detects it and shows it as
  `[LEGACY vX]`. Such a bank cannot be edited, nor can patterns be
  copied out of it (to avoid misinterpreting an unknown old layout) —
  but it's left untouched in the original file, and writing simply
  copies it 1:1 with no data loss.
- **The source file on disk is never modified** until you explicitly
  save it (and by default only the composition gets saved, not the
  source) — even a Move from source to composition only changes the
  source in the running app's memory.
- **Save format variants.** Both the 128 KB Flash build and the 32 KB
  SRAM build are supported as either panel's file.
