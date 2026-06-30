#!/usr/bin/env python3
"""
fmssm_gui.py

FMSSM (FMS Save Manager) - desktop app (Tkinter) for managing save
files of the GBA groovebox FMS.

Two panels:
  - TOP panel ("Source")       - open an existing .sav file here
  - BOTTOM panel ("New Composition") - starts as a completely empty
    save; drag/copy patterns here from the top panel and assemble a
    brand-new layout, which you then save as a new .sav

Each panel has its own list of 8 banks and its own 5-track x 16-slot
grid. Patterns can be moved/copied/swapped/cleared:
  - within a single panel (as before)
  - BETWEEN panels by dragging with the mouse (= direct Move) or via
    the Move/Copy buttons (select a cell on top as source, one on the
    bottom as destination, or vice versa)

Run:
    python3 fmssm_gui.py

Note: requires only the Python standard library (tkinter ships with
most installations; on Linux you may need `sudo apt install python3-tk`).
Requires fmssm_format.py to be present in the same folder.
"""

import os
import sys
from typing import Optional
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fmssm_format import (
    SaveFile, Pattern, Bank, N_SLOTS,
    copy_pattern_across_savefiles, move_pattern_across_savefiles,
)

TRACK_LABELS = ["FM 1", "FM 2", "FM 3", "FM 4", "Noise"]


def slot_hex(slot: int) -> str:
    """Formats a slot index (0-15) as a single hex digit (0-9, A-F),
    matching the column headers shown in the grid."""
    return format(slot, "X")

COLOR_EMPTY = "#2b2b2b"
COLOR_FILLED = "#3a6ea5"
COLOR_SELECTED_SRC = "#e0a030"
COLOR_SELECTED_DST = "#5cb85c"
COLOR_LEGACY = "#7a2b2b"
COLOR_TEXT = "#e8e8e8"
COLOR_PANEL_BG = "#1e1e1e"


class SlotCell(tk.Frame):
    """A single grid cell representing (panel, track, slot)."""

    def __init__(self, parent, app, panel, track, slot, **kwargs):
        super().__init__(parent, width=46, height=36, bd=1, relief="solid",
                          bg=COLOR_EMPTY, **kwargs)
        self.app = app
        self.panel = panel
        self.track = track
        self.slot = slot
        self.pack_propagate(False)

        self.label = tk.Label(self, text="", bg=COLOR_EMPTY, fg=COLOR_TEXT, font=("TkDefaultFont", 8))
        self.label.pack(expand=True, fill="both")

        for widget in (self, self.label):
            widget.bind("<Button-1>", self._on_press)
            widget.bind("<Button-3>", self._on_right_click)
            widget.bind("<B1-Motion>", self._on_drag_motion)
            widget.bind("<ButtonRelease-1>", self._on_drag_release)

    def _on_press(self, event):
        # remember the starting cell; click-vs-drag is decided in
        # _on_drag_release (based on where the mouse ends up)
        self.app.on_drag_start(self.panel, self.track, self.slot)

    def _on_right_click(self, event):
        self.app.on_cell_right_click(self.panel, self.track, self.slot, event)

    def _on_drag_motion(self, event):
        pass  # visual drag feedback could be added here, but isn't required

    def _on_drag_release(self, event):
        x_root, y_root = event.x_root, event.y_root
        target = event.widget.winfo_containing(x_root, y_root)
        cell = self.app.find_cell_for_widget(target)
        if cell is not None:
            self.app.on_drag_release(cell.panel, cell.track, cell.slot)
        else:
            self.app.on_drag_release(None, None, None)

    def refresh(self, pattern, length, is_selected_src, is_selected_dst):
        if pattern is None:
            bg = COLOR_EMPTY
            text = ""
        else:
            bg = COLOR_FILLED
            text = f"{length}"

        if is_selected_src:
            bg = COLOR_SELECTED_SRC
        elif is_selected_dst:
            bg = COLOR_SELECTED_DST

        self.config(bg=bg)
        self.label.config(bg=bg, text=text)


class ColumnHeaderCell(tk.Label):
    """Clickable column header showing a slot's hex index. Clicking it
    selects all 5 tracks of that slot at once (a "column" selection),
    enabling Move/Copy/Swap/Clear across the whole column in a single
    action. Unlike SlotCell, this only supports click - no drag&drop,
    since dragging an entire column is a separate, more ambiguous
    gesture that isn't supported."""

    def __init__(self, parent, app, panel, slot, **kwargs):
        super().__init__(parent, text=slot_hex(slot), width=4,
                          bg=COLOR_PANEL_BG, fg="#888888", cursor="hand2", **kwargs)
        self.app = app
        self.panel = panel
        self.slot = slot
        self.bind("<Button-1>", self._on_click)

    def _on_click(self, event):
        self.app.on_column_header_click(self.panel, self.slot)

    def refresh(self, is_selected_src, is_selected_dst):
        if is_selected_src:
            bg = COLOR_SELECTED_SRC
        elif is_selected_dst:
            bg = COLOR_SELECTED_DST
        else:
            bg = COLOR_PANEL_BG
        self.config(bg=bg)


class BankGrid(tk.Frame):
    """Grid of 5 tracks x 16 slots for one bank in one panel."""

    def __init__(self, parent, app, panel, **kwargs):
        super().__init__(parent, **kwargs)
        self.app = app
        self.panel = panel
        self.cells = {}  # (track, slot) -> SlotCell
        self.headers = {}  # slot -> ColumnHeaderCell

        tk.Label(self, text="", width=6, bg=COLOR_PANEL_BG).grid(row=0, column=0)
        for s in range(N_SLOTS):
            header = ColumnHeaderCell(self, app, panel, s)
            header.grid(row=0, column=s+1)
            self.headers[s] = header

        for t in range(5):
            tk.Label(self, text=TRACK_LABELS[t], width=6, anchor="w",
                     bg=COLOR_PANEL_BG, fg=COLOR_TEXT).grid(row=t+1, column=0, sticky="w")
            for s in range(N_SLOTS):
                cell = SlotCell(self, app, panel, t, s)
                cell.grid(row=t+1, column=s+1, padx=1, pady=1)
                self.cells[(t, s)] = cell

    def refresh(self, bank, src, dst):
        # src/dst are (track, slot) tuples for a single-cell selection,
        # or (None, slot) for a whole-column selection.
        for (t, s), cell in self.cells.items():
            is_src = (src == (t, s)) or (src == (None, s))
            is_dst = (dst == (t, s)) or (dst == (None, s))
            if bank is None or bank.legacy:
                cell.refresh(None, None, is_src, is_dst)
                continue
            pattern = bank.patterns[t][s]
            # the number shown in a filled cell is the pattern LENGTH
            # (fmPatLength / nsPatLength), not the number of active steps
            if t < 4:
                length = bank.fm_pat_length[t][s]
            else:
                length = bank.ns_pat_length[s]
            cell.refresh(pattern, length, is_src, is_dst)

        for s, header in self.headers.items():
            is_src = (src == (None, s))
            is_dst = (dst == (None, s))
            header.refresh(is_src, is_dst)


class Panel:
    """One of the two panels (Source / New Composition). Holds its own
    SaveFile, its own bank selection and its own grid widget."""

    def __init__(self, app, parent_frame, panel_id, title, savefile):
        self.app = app
        self.panel_id = panel_id  # "source" or "dest"
        self.savefile: Optional[SaveFile] = savefile
        self.selected_bank_idx: Optional[int] = None
        self.path: Optional[str] = None

        self.frame = tk.Frame(parent_frame, bg=COLOR_PANEL_BG, bd=1, relief="groove")

        header = tk.Frame(self.frame, bg=COLOR_PANEL_BG)
        header.pack(side="top", fill="x", padx=6, pady=(6, 2))
        self.title_label = tk.Label(header, text=title, bg=COLOR_PANEL_BG, fg=COLOR_TEXT,
                                     font=("TkDefaultFont", 11, "bold"))
        self.title_label.pack(side="left")
        self.info_label = tk.Label(header, text="", bg=COLOR_PANEL_BG, fg="#999999")
        self.info_label.pack(side="right")

        body = tk.Frame(self.frame, bg=COLOR_PANEL_BG)
        body.pack(side="top", fill="both", expand=True, padx=6, pady=4)

        left = tk.Frame(body, bg=COLOR_PANEL_BG, width=170)
        left.pack(side="left", fill="y")
        tk.Label(left, text="Banks", bg=COLOR_PANEL_BG, fg=COLOR_TEXT).pack(anchor="w")
        self.bank_listbox = tk.Listbox(left, height=8, exportselection=False, width=24)
        self.bank_listbox.pack(fill="x", pady=2)
        self.bank_listbox.bind("<<ListboxSelect>>", lambda e: self.app.on_bank_select(self))
        tk.Button(left, text="Rename bank",
                  command=lambda: self.app.rename_bank(self)).pack(fill="x", pady=2)
        tk.Button(left, text="Clear bank",
                  command=lambda: self.app.clear_bank(self)).pack(fill="x", pady=2)
        tk.Button(left, text="Save this file",
                  command=lambda: self.app.save_panel_file(self)).pack(fill="x", pady=2)

        right = tk.Frame(body, bg=COLOR_PANEL_BG)
        right.pack(side="left", fill="both", expand=True, padx=(10, 0))
        self.bank_title_label = tk.Label(right, text="(no bank selected)", bg=COLOR_PANEL_BG,
                                          fg=COLOR_TEXT, font=("TkDefaultFont", 10, "bold"))
        self.bank_title_label.pack(anchor="w")
        self.grid = BankGrid(right, app, self, bg=COLOR_PANEL_BG)
        self.grid.pack(anchor="nw", pady=4)
        self.legacy_warning = tk.Label(right, text="", bg=COLOR_PANEL_BG, fg="#ff8080", wraplength=480, justify="left")
        self.legacy_warning.pack(anchor="w")

    def current_bank(self) -> Optional[Bank]:
        if self.savefile is None or self.selected_bank_idx is None:
            return None
        if self.selected_bank_idx >= self.savefile.max_banks():
            return None
        return self.savefile.banks[self.selected_bank_idx]

    def refresh_bank_list(self):
        self.bank_listbox.delete(0, tk.END)
        if self.savefile is None:
            self.info_label.config(text="(no file)")
            return
        max_banks = self.savefile.max_banks()
        for i in range(max_banks):
            bank = self.savefile.banks[i]
            name = self.savefile.bank_names[i]
            if bank is None:
                label = f"{i}: {name}  (empty)"
            elif bank.legacy:
                label = f"{i}: {name}  [LEGACY v{bank.legacy_version}]"
            else:
                n = sum(1 for t in range(5) for s in range(16) if bank.patterns[t][s] is not None)
                label = f"{i}: {name}  ({n} patterns, {bank.needed_sectors()} sect.)"
            self.bank_listbox.insert(tk.END, label)
        if self.selected_bank_idx is not None and self.selected_bank_idx < max_banks:
            self.bank_listbox.selection_set(self.selected_bank_idx)

        # Banks beyond max_banks() exist in the file format's directory
        # but aren't reachable through the real FMS firmware's own UI
        # on this build (SRAM only shows banks 0-1). Normally these are
        # empty and we just don't list them - but if one unexpectedly
        # has data (e.g. a file produced by another tool), warn rather
        # than silently hiding the data.
        unreachable_with_data = [i for i in range(max_banks, 8) if self.savefile.banks[i] is not None]

        used = sum(b.needed_sectors() for b in self.savefile.banks if b is not None)
        total = self.savefile.data_sectors()
        kind = "Flash 128KB" if self.savefile.is_flash else "SRAM 32KB"
        info_text = f"v{self.savefile.version} | {kind} | sectors {used}/{total}"
        if unreachable_with_data:
            bank_list = ", ".join(str(i) for i in unreachable_with_data)
            info_text += f" | WARNING: bank(s) {bank_list} have data but aren't usable on this build!"
        self.info_label.config(text=info_text)

    def refresh_grid(self):
        bank = self.current_bank()
        name = self.savefile.bank_names[self.selected_bank_idx] if (
            self.savefile is not None and self.selected_bank_idx is not None
        ) else "-"
        if self.selected_bank_idx is None or self.savefile is None:
            self.bank_title_label.config(text="(no bank selected)")
            self.grid.refresh(None, None, None)
            self.legacy_warning.config(text="")
            return

        self.bank_title_label.config(text=f"Bank {self.selected_bank_idx}: {name}")

        if bank is None:
            self.legacy_warning.config(text="This bank is empty (never saved). "
                                             "Dragging/copying a pattern here will create it automatically.")
        elif bank.legacy:
            self.legacy_warning.config(
                text=f"This bank was saved by older firmware (format v{bank.legacy_version}). "
                     f"Individual patterns cannot be viewed or edited."
            )
        else:
            self.legacy_warning.config(text="")

        src = self.app.src
        dst = self.app.dst
        src_cell = (src[1], src[2]) if src and src[0] is self else None
        dst_cell = (dst[1], dst[2]) if dst and dst[0] is self else None
        self.grid.refresh(bank, src_cell, dst_cell)


def ask_save_type(root) -> "bool | None":
    """Shows a small modal dialog letting the user pick the save build
    for a new empty composition. Returns True for Flash (128 KB),
    False for SRAM (32 KB), or None if the dialog was cancelled."""
    result = {"value": None}

    dialog = tk.Toplevel(root)
    dialog.title("New empty composition")
    dialog.configure(bg=COLOR_PANEL_BG)
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()

    tk.Label(dialog, text="Which save build should the new composition target?",
              bg=COLOR_PANEL_BG, fg=COLOR_TEXT, padx=16, pady=12).pack()

    btn_frame = tk.Frame(dialog, bg=COLOR_PANEL_BG)
    btn_frame.pack(padx=16, pady=(0, 16))

    def choose(value):
        result["value"] = value
        dialog.destroy()

    tk.Button(btn_frame, text="Flash (128 KB, 8 banks, 31 sectors)",
              width=32, command=lambda: choose(True)).pack(pady=3)
    tk.Button(btn_frame, text="SRAM (32 KB, 2 banks, 7 sectors)",
              width=32, command=lambda: choose(False)).pack(pady=3)
    tk.Button(btn_frame, text="Cancel", width=32,
              command=lambda: choose(None)).pack(pady=(8, 0))

    dialog.protocol("WM_DELETE_WINDOW", lambda: choose(None))

    # center over the main window, then block until closed
    dialog.update_idletasks()
    x = root.winfo_x() + (root.winfo_width() - dialog.winfo_width()) // 2
    y = root.winfo_y() + (root.winfo_height() - dialog.winfo_height()) // 2
    dialog.geometry(f"+{x}+{y}")

    root.wait_window(dialog)
    return result["value"]


class FMSManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("FMSSM")
        self.root.geometry("1180x900")
        self.root.configure(bg="#141414")

        # selection for move/copy/swap: (panel, track, slot) or None
        self.src = None
        self.dst = None
        self._drag_origin = None  # (panel, track, slot)

        self.dirty_source = False
        self.dirty_dest = False

        self._build_menu()
        self._build_layout()
        self._update_title()

        self.source_panel.refresh_bank_list()
        self.dest_panel.refresh_bank_list()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_menu(self):
        menubar = tk.Menu(self.root)

        filemenu = tk.Menu(menubar, tearoff=0)
        filemenu.add_command(label="Open source file...", command=self.open_source_file, accelerator="Ctrl+O")
        filemenu.add_command(label="Save source file", command=self.save_source_file, accelerator="Ctrl+Shift+S")
        filemenu.add_command(label="Save source file as...", command=self.save_source_file_as)
        filemenu.add_separator()
        filemenu.add_command(label="New empty composition", command=self.new_dest_file)
        filemenu.add_command(label="Open composition from file...", command=self.open_dest_file)
        filemenu.add_command(label="Save composition", command=self.save_dest_file, accelerator="Ctrl+S")
        filemenu.add_command(label="Save composition as...", command=self.save_dest_file_as)
        filemenu.add_separator()
        filemenu.add_command(label="Quit", command=self.root.quit)
        menubar.add_cascade(label="File", menu=filemenu)
        self.root.config(menu=menubar)

        self.root.bind("<Control-o>", lambda e: self.open_source_file())
        self.root.bind("<Control-s>", lambda e: self.save_dest_file())
        self.root.bind("<Control-S>", lambda e: self.save_source_file())  # Ctrl+Shift+S

    def _build_layout(self):
        main = tk.Frame(self.root, bg="#141414")
        main.pack(side="top", fill="both", expand=True, padx=8, pady=6)

        # --- top panel: SOURCE ---
        self.source_panel = Panel(self, main, "source", "SOURCE (opened file)", None)
        self.source_panel.frame.pack(side="top", fill="both", expand=True, pady=(0, 4))

        # --- middle row with buttons ---
        mid = tk.Frame(main, bg="#141414")
        mid.pack(side="top", fill="x", pady=4)

        self.src_label = tk.Label(mid, text="Source selection: -", bg="#141414", fg=COLOR_SELECTED_SRC)
        self.src_label.pack(side="left", padx=6)
        self.dst_label = tk.Label(mid, text="Destination selection: -", bg="#141414", fg=COLOR_SELECTED_DST)
        self.dst_label.pack(side="left", padx=6)

        btns = tk.Frame(mid, bg="#141414")
        btns.pack(side="right")
        tk.Button(btns, text="Move ->", command=self.do_move).pack(side="left", padx=3)
        tk.Button(btns, text="Copy ->", command=self.do_copy).pack(side="left", padx=3)
        tk.Button(btns, text="Swap", command=self.do_swap).pack(side="left", padx=3)
        tk.Button(btns, text="Clear source", command=self.do_clear).pack(side="left", padx=3)
        tk.Button(btns, text="Clear selection", command=self.clear_selection).pack(side="left", padx=3)

        # --- bottom panel: NEW COMPOSITION ---
        self.dest_panel = Panel(self, main, "dest", "NEW COMPOSITION (destination file)", SaveFile.create_empty())
        self.dest_panel.frame.pack(side="top", fill="both", expand=True, pady=(4, 0))

        # status bar
        self.status_label = tk.Label(self.root, text="", bg="#0a0a0a", fg="#aaaaaa", anchor="w")
        self.status_label.pack(side="bottom", fill="x")

    # ------------------------------------------------------------------
    # File: source
    # ------------------------------------------------------------------

    def open_source_file(self):
        if self.dirty_source:
            if not messagebox.askyesno("Open source file",
                                        "The current source file has unsaved changes. Discard them "
                                        "and open a different file?"):
                return
        path = filedialog.askopenfilename(
            title="Open source FMS save",
            filetypes=[("FMS save", "*.sav"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            sf = SaveFile.load(path)
        except Exception as e:
            messagebox.showerror("Error reading file", str(e))
            return
        self.source_panel.savefile = sf
        self.source_panel.path = path
        self.source_panel.selected_bank_idx = None
        self.dirty_source = False
        self.clear_selection()
        self.source_panel.refresh_bank_list()
        self.source_panel.refresh_grid()
        self._update_title()
        self.set_status(f"Source opened: {path}")

    def save_source_file(self):
        if self.source_panel.savefile is None:
            return
        if self.source_panel.path is None:
            self.save_source_file_as()
            return
        self._do_save_source(self.source_panel.path)

    def save_source_file_as(self):
        if self.source_panel.savefile is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save source file as",
            defaultextension=".sav",
            filetypes=[("FMS save", "*.sav"), ("All files", "*.*")],
        )
        if not path:
            return
        self._do_save_source(path)

    def _do_save_source(self, path):
        try:
            self.source_panel.savefile.save(path)
        except Exception as e:
            messagebox.showerror("Error saving file", str(e))
            return
        self.source_panel.path = path
        self.dirty_source = False
        self._update_title()
        self.set_status(f"Source saved: {path}")

    # ------------------------------------------------------------------
    # File: destination / composition
    # ------------------------------------------------------------------

    def new_dest_file(self):
        if self.dirty_dest:
            if not messagebox.askyesno("New composition",
                                        "The current composition has unsaved changes. Discard and start over?"):
                return
        is_flash = ask_save_type(self.root)
        if is_flash is None:
            return  # cancelled
        self.dest_panel.savefile = SaveFile.create_empty(is_flash=is_flash)
        self.dest_panel.path = None
        self.dest_panel.selected_bank_idx = None
        self.dirty_dest = False
        self.clear_selection()
        self.dest_panel.refresh_bank_list()
        self.dest_panel.refresh_grid()
        self._update_title()
        kind = "Flash 128KB" if is_flash else "SRAM 32KB"
        self.set_status(f"New empty composition created ({kind}).")

    def open_dest_file(self):
        path = filedialog.askopenfilename(
            title="Open an existing .sav as a composition to edit",
            filetypes=[("FMS save", "*.sav"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            sf = SaveFile.load(path)
        except Exception as e:
            messagebox.showerror("Error reading file", str(e))
            return
        self.dest_panel.savefile = sf
        self.dest_panel.path = path
        self.dest_panel.selected_bank_idx = None
        self.dirty_dest = False
        self.clear_selection()
        self.dest_panel.refresh_bank_list()
        self.dest_panel.refresh_grid()
        self._update_title()
        self.set_status(f"Composition loaded from: {path}")

    def save_dest_file(self):
        if self.dest_panel.savefile is None:
            return
        if self.dest_panel.path is None:
            self.save_dest_file_as()
            return
        self._do_save_dest(self.dest_panel.path)

    def save_dest_file_as(self):
        if self.dest_panel.savefile is None:
            return
        path = filedialog.asksaveasfilename(
            title="Save new composition as",
            defaultextension=".sav",
            filetypes=[("FMS save", "*.sav"), ("All files", "*.*")],
        )
        if not path:
            return
        self._do_save_dest(path)

    def _do_save_dest(self, path):
        try:
            self.dest_panel.savefile.save(path)
        except Exception as e:
            messagebox.showerror("Error saving file", str(e))
            return
        self.dest_panel.path = path
        self.dirty_dest = False
        self._update_title()
        self.set_status(f"Composition saved: {path}")

    def _update_title(self):
        src_star = "*" if self.dirty_source else ""
        dst_star = "*" if self.dirty_dest else ""
        src_name = os.path.basename(self.source_panel.path) if self.source_panel.path else "(not opened)"
        dst_name = os.path.basename(self.dest_panel.path) if self.dest_panel.path else "(new, unsaved)"
        self.root.title(f"FMSSM - source: {src_name}{src_star} | composition: {dst_name}{dst_star}")

    def mark_dirty(self, panel):
        if panel is self.source_panel:
            self.dirty_source = True
        else:
            self.dirty_dest = True
        self._update_title()

    def set_status(self, text):
        self.status_label.config(text=text)

    # ------------------------------------------------------------------
    # Bank list / grid shared by both panels
    # ------------------------------------------------------------------

    def on_bank_select(self, panel: "Panel"):
        sel = panel.bank_listbox.curselection()
        if not sel:
            return
        panel.selected_bank_idx = sel[0]
        panel.refresh_grid()

    def rename_bank(self, panel: "Panel"):
        if panel.savefile is None or panel.selected_bank_idx is None:
            return
        if panel.savefile.banks[panel.selected_bank_idx] is None:
            messagebox.showinfo("Info", "An empty bank has no name to edit - add a pattern to it first.")
            return
        current = panel.savefile.bank_names[panel.selected_bank_idx]
        new_name = simpledialog.askstring("Rename bank", "New name (max 4 characters, A-Z 0-9 -):",
                                           initialvalue=current)
        if new_name is None:
            return
        panel.savefile.bank_names[panel.selected_bank_idx] = new_name[:4]
        self.mark_dirty(panel)
        panel.refresh_bank_list()

    def clear_bank(self, panel: "Panel"):
        """Completely frees the selected bank - it becomes 'empty' in
        the directory, exactly as if it had never been saved. This also
        resets its name to the uninitialized state, not just its
        patterns."""
        if panel.savefile is None or panel.selected_bank_idx is None:
            return
        idx = panel.selected_bank_idx
        bank = panel.savefile.banks[idx]
        if bank is None:
            messagebox.showinfo("Info", "This bank is already empty.")
            return

        if bank.legacy:
            warning = (f"Bank {idx} was saved by older firmware (format v{bank.legacy_version}) "
                        f"and its contents cannot be inspected by this tool. ")
        else:
            n = sum(1 for t in range(5) for s in range(16) if bank.patterns[t][s] is not None)
            warning = f"Bank {idx} contains {n} pattern(s). "

        if not messagebox.askyesno(
            "Clear bank",
            warning + f"Really clear it completely? It will become empty, as if it had "
                      f"never been saved (this also clears its name)."
        ):
            return

        panel.savefile.banks[idx] = None
        panel.savefile.bank_names[idx] = '----'
        panel.savefile.bank_name_raw[idx] = b'\xff\xff\xff\xff'
        self.mark_dirty(panel)
        self.set_status(f"Cleared: [{panel.panel_id}] bank {idx} (now empty)")
        panel.selected_bank_idx = None
        panel.refresh_bank_list()
        self.clear_selection()

    def save_panel_file(self, panel: "Panel"):
        """Saves whichever panel's button was clicked - dispatches to
        the source or destination save logic depending on panel_id."""
        if panel.panel_id == "source":
            self.save_source_file()
        else:
            self.save_dest_file()

    def find_cell_for_widget(self, widget):
        for panel in (self.source_panel, self.dest_panel):
            for cell in panel.grid.cells.values():
                if widget is cell or widget is cell.label:
                    return cell
        return None

    # ------------------------------------------------------------------
    # Cell selection (click / right-click / drag)
    # ------------------------------------------------------------------

    def on_cell_click(self, panel: "Panel", track, slot):
        bank = panel.current_bank()
        if panel.selected_bank_idx is None:
            self.set_status("Select a bank in this panel first.")
            return
        if bank is not None and bank.legacy:
            self.set_status("A legacy bank cannot be edited at the pattern level.")
            return

        target = (panel, track, slot)
        if self.src is None:
            self.src = target
            self.set_status(f"Source selected: [{panel.panel_id}] bank {panel.selected_bank_idx}, "
                             f"{TRACK_LABELS[track]}, slot {slot_hex(slot)}")
        elif self.dst is None and target != self.src:
            self.dst = target
            self.set_status(f"Destination selected: [{panel.panel_id}] bank {panel.selected_bank_idx}, "
                             f"{TRACK_LABELS[track]}, slot {slot_hex(slot)}. Press Move/Copy/Swap.")
        else:
            self.src = target
            self.dst = None
            self.set_status(f"Source selected: [{panel.panel_id}] bank {panel.selected_bank_idx}, "
                             f"{TRACK_LABELS[track]}, slot {slot_hex(slot)}")

        self._update_selection_labels()
        self.source_panel.refresh_grid()
        self.dest_panel.refresh_grid()

    def on_column_header_click(self, panel: "Panel", slot):
        """Clicking a column header selects all 5 tracks of that slot
        at once - the resulting selection is (panel, None, slot), where
        track=None is the marker for "whole column"."""
        bank = panel.current_bank()
        if panel.selected_bank_idx is None:
            self.set_status("Select a bank in this panel first.")
            return
        if bank is not None and bank.legacy:
            self.set_status("A legacy bank cannot be edited at the pattern level.")
            return

        target = (panel, None, slot)
        if self.src is None:
            self.src = target
            self.set_status(f"Source column selected: [{panel.panel_id}] bank "
                             f"{panel.selected_bank_idx}, slot {slot_hex(slot)} (all 5 tracks)")
        elif self.dst is None and target != self.src:
            self.dst = target
            self.set_status(f"Destination column selected: [{panel.panel_id}] bank "
                             f"{panel.selected_bank_idx}, slot {slot_hex(slot)} (all 5 tracks). "
                             f"Press Move/Copy/Swap.")
        else:
            self.src = target
            self.dst = None
            self.set_status(f"Source column selected: [{panel.panel_id}] bank "
                             f"{panel.selected_bank_idx}, slot {slot_hex(slot)} (all 5 tracks)")

        self._update_selection_labels()
        self.source_panel.refresh_grid()
        self.dest_panel.refresh_grid()

    def on_cell_right_click(self, panel: "Panel", track, slot, event):
        bank = panel.current_bank()
        if bank is None or bank.legacy:
            return
        pattern = bank.patterns[track][slot]
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"Select as source ({TRACK_LABELS[track]} slot {slot_hex(slot)})",
                          command=lambda: self._set_src(panel, track, slot))
        menu.add_command(label="Select as destination",
                          command=lambda: self._set_dst(panel, track, slot))
        if pattern is not None:
            menu.add_separator()
            menu.add_command(label="Clear this pattern",
                              command=lambda: self._clear_specific(panel, track, slot))
        menu.tk_popup(event.x_root, event.y_root)

    def _set_src(self, panel, track, slot):
        self.src = (panel, track, slot)
        self._update_selection_labels()
        self.source_panel.refresh_grid()
        self.dest_panel.refresh_grid()

    def _set_dst(self, panel, track, slot):
        self.dst = (panel, track, slot)
        self._update_selection_labels()
        self.source_panel.refresh_grid()
        self.dest_panel.refresh_grid()

    def _clear_specific(self, panel, track, slot):
        bank = panel.current_bank()
        if bank is None or bank.legacy:
            return
        bank.clear_pattern(track, slot)
        self.mark_dirty(panel)
        panel.refresh_bank_list()
        panel.refresh_grid()

    def on_drag_start(self, panel: "Panel", track, slot):
        # Always remember the origin, even if the bank is empty/legacy -
        # this is also used to detect a plain click (vs. a drag) in
        # on_drag_release. Whether a drag is actually allowed to START
        # from this cell is enforced separately in _execute_move.
        self._drag_origin = (panel, track, slot)

    def on_drag_release(self, panel, track, slot):
        if self._drag_origin is None:
            return
        origin = self._drag_origin
        self._drag_origin = None
        if panel is None or track is None or slot is None:
            return
        target = (panel, track, slot)
        if target == origin:
            # mouse didn't move from the starting cell -> this was a click, not a drag
            self.on_cell_click(panel, track, slot)
            return
        # drag & drop = direct MOVE (can also be cross-panel)
        self._execute_move(origin, target)

    def _update_selection_labels(self):
        def fmt(sel):
            if sel is None:
                return "-"
            p, t, s = sel
            bidx = p.selected_bank_idx
            track_part = "all 5 tracks" if t is None else TRACK_LABELS[t]
            return f"[{p.panel_id}] bank {bidx}, {track_part}, slot {slot_hex(s)}"
        self.src_label.config(text=f"Source selection: {fmt(self.src)}")
        self.dst_label.config(text=f"Destination selection: {fmt(self.dst)}")

    def clear_selection(self):
        self.src = None
        self.dst = None
        self._update_selection_labels()
        self.source_panel.refresh_grid()
        self.dest_panel.refresh_grid()

    # ------------------------------------------------------------------
    # Actions: move / copy / swap / clear
    # ------------------------------------------------------------------

    def _validate_selection(self):
        if self.src is None or self.dst is None:
            messagebox.showinfo("Info", "First select both a source and a destination "
                                         "(a single cell, or a whole column by clicking its "
                                         "header) in either panel.")
            return False
        src_track = self.src[1]
        dst_track = self.dst[1]
        src_is_column = src_track is None
        dst_is_column = dst_track is None
        if src_is_column != dst_is_column:
            messagebox.showerror("Cannot perform action",
                                  "Cannot mix a single-cell selection with a whole-column "
                                  "selection. Select either two cells or two columns.")
            return False
        if not src_is_column:
            src_is_fm = src_track in (0, 1, 2, 3)
            dst_is_fm = dst_track in (0, 1, 2, 3)
            if src_is_fm != dst_is_fm:
                messagebox.showerror("Cannot perform action",
                                      "Cannot move/copy/swap between an FM track and the Noise "
                                      "track (they use a different step format).")
                return False
        return True

    def _execute_move(self, src, dst):
        src_panel, src_track, src_slot = src
        dst_panel, dst_track, dst_slot = dst
        src_is_fm = src_track in (0, 1, 2, 3)
        dst_is_fm = dst_track in (0, 1, 2, 3)
        if src_is_fm != dst_is_fm:
            messagebox.showerror("Cannot perform action",
                                  "Cannot move between an FM track and the Noise track "
                                  "(they use a different step format).")
            return
        if src_panel.savefile is None or src_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The source panel has no bank selected.")
            return
        if dst_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The destination panel has no bank selected.")
            return

        try:
            if src_panel is dst_panel:
                src_panel.savefile.move_pattern_between_banks(
                    src_panel.selected_bank_idx, src_track, src_slot,
                    dst_panel.selected_bank_idx, dst_track, dst_slot,
                )
            else:
                move_pattern_across_savefiles(
                    src_panel.savefile, src_panel.selected_bank_idx, src_track, src_slot,
                    dst_panel.savefile, dst_panel.selected_bank_idx, dst_track, dst_slot,
                )
        except Exception as e:
            messagebox.showerror("Error during move", str(e))
            return

        self.mark_dirty(src_panel)
        self.mark_dirty(dst_panel)
        self.set_status(f"Moved: [{src_panel.panel_id}] bank {src_panel.selected_bank_idx} "
                         f"{TRACK_LABELS[src_track]} slot {slot_hex(src_slot)} -> "
                         f"[{dst_panel.panel_id}] bank {dst_panel.selected_bank_idx} "
                         f"{TRACK_LABELS[dst_track]} slot {slot_hex(dst_slot)}")
        src_panel.refresh_bank_list()
        dst_panel.refresh_bank_list()
        src_panel.refresh_grid()
        dst_panel.refresh_grid()

    def _execute_move_column(self, src, dst):
        """Moves all 5 tracks (FM 1-4 + Noise) of one column to another,
        track-for-track (track 0 -> track 0, etc). Used for whole-column
        selections (track is None in both src and dst)."""
        src_panel, _, src_slot = src
        dst_panel, _, dst_slot = dst
        if src_panel.savefile is None or src_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The source panel has no bank selected.")
            return
        if dst_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The destination panel has no bank selected.")
            return

        try:
            for track in range(5):
                if src_panel is dst_panel:
                    src_panel.savefile.move_pattern_between_banks(
                        src_panel.selected_bank_idx, track, src_slot,
                        dst_panel.selected_bank_idx, track, dst_slot,
                    )
                else:
                    move_pattern_across_savefiles(
                        src_panel.savefile, src_panel.selected_bank_idx, track, src_slot,
                        dst_panel.savefile, dst_panel.selected_bank_idx, track, dst_slot,
                    )
        except Exception as e:
            messagebox.showerror("Error during move", str(e))
            return

        self.mark_dirty(src_panel)
        self.mark_dirty(dst_panel)
        self.set_status(f"Moved column: [{src_panel.panel_id}] bank {src_panel.selected_bank_idx} "
                         f"slot {slot_hex(src_slot)} -> [{dst_panel.panel_id}] bank "
                         f"{dst_panel.selected_bank_idx} slot {slot_hex(dst_slot)} (all 5 tracks)")
        src_panel.refresh_bank_list()
        dst_panel.refresh_bank_list()
        src_panel.refresh_grid()
        dst_panel.refresh_grid()

    def do_move(self):
        if not self._validate_selection():
            return
        if self.src[1] is None:
            self._execute_move_column(self.src, self.dst)
        else:
            self._execute_move(self.src, self.dst)
        self.clear_selection()

    def _execute_copy(self, src, dst):
        src_panel, src_track, src_slot = src
        dst_panel, dst_track, dst_slot = dst

        if src_panel.savefile is None or src_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The source panel has no bank selected.")
            return
        if dst_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The destination panel has no bank selected.")
            return

        try:
            if src_panel is dst_panel:
                src_bank = src_panel.savefile.banks[src_panel.selected_bank_idx]
                dst_bank = dst_panel.savefile.ensure_bank(dst_panel.selected_bank_idx)
                if src_bank is None:
                    raise ValueError("The source bank must exist.")
                if src_panel.selected_bank_idx == dst_panel.selected_bank_idx:
                    src_bank.copy_pattern(src_track, src_slot, dst_track, dst_slot)
                else:
                    settings = src_bank._extract_slot_settings(src_track, src_slot)
                    pattern = src_bank.patterns[src_track][src_slot]
                    dst_bank._apply_slot_settings(dst_track, dst_slot, settings)
                    dst_bank.patterns[dst_track][dst_slot] = pattern.copy() if pattern is not None else None
            else:
                copy_pattern_across_savefiles(
                    src_panel.savefile, src_panel.selected_bank_idx, src_track, src_slot,
                    dst_panel.savefile, dst_panel.selected_bank_idx, dst_track, dst_slot,
                )
        except Exception as e:
            messagebox.showerror("Error during copy", str(e))
            return

        self.mark_dirty(dst_panel)
        self.set_status(f"Copied: [{src_panel.panel_id}] bank {src_panel.selected_bank_idx} "
                         f"{TRACK_LABELS[src_track]} slot {slot_hex(src_slot)} -> "
                         f"[{dst_panel.panel_id}] bank {dst_panel.selected_bank_idx} "
                         f"{TRACK_LABELS[dst_track]} slot {slot_hex(dst_slot)}")
        dst_panel.refresh_bank_list()
        src_panel.refresh_grid()
        dst_panel.refresh_grid()

    def _execute_copy_column(self, src, dst):
        """Copies all 5 tracks (FM 1-4 + Noise) of one column to
        another, track-for-track. Used for whole-column selections."""
        src_panel, _, src_slot = src
        dst_panel, _, dst_slot = dst

        if src_panel.savefile is None or src_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The source panel has no bank selected.")
            return
        if dst_panel.selected_bank_idx is None:
            messagebox.showerror("Error", "The destination panel has no bank selected.")
            return

        try:
            for track in range(5):
                if src_panel is dst_panel:
                    src_bank = src_panel.savefile.banks[src_panel.selected_bank_idx]
                    dst_bank = dst_panel.savefile.ensure_bank(dst_panel.selected_bank_idx)
                    if src_bank is None:
                        raise ValueError("The source bank must exist.")
                    if src_panel.selected_bank_idx == dst_panel.selected_bank_idx:
                        src_bank.copy_pattern(track, src_slot, track, dst_slot)
                    else:
                        settings = src_bank._extract_slot_settings(track, src_slot)
                        pattern = src_bank.patterns[track][src_slot]
                        dst_bank._apply_slot_settings(track, dst_slot, settings)
                        dst_bank.patterns[track][dst_slot] = pattern.copy() if pattern is not None else None
                else:
                    copy_pattern_across_savefiles(
                        src_panel.savefile, src_panel.selected_bank_idx, track, src_slot,
                        dst_panel.savefile, dst_panel.selected_bank_idx, track, dst_slot,
                    )
        except Exception as e:
            messagebox.showerror("Error during copy", str(e))
            return

        self.mark_dirty(dst_panel)
        self.set_status(f"Copied column: [{src_panel.panel_id}] bank {src_panel.selected_bank_idx} "
                         f"slot {slot_hex(src_slot)} -> [{dst_panel.panel_id}] bank "
                         f"{dst_panel.selected_bank_idx} slot {slot_hex(dst_slot)} (all 5 tracks)")
        dst_panel.refresh_bank_list()
        src_panel.refresh_grid()
        dst_panel.refresh_grid()

    def do_copy(self):
        if not self._validate_selection():
            return
        if self.src[1] is None:
            self._execute_copy_column(self.src, self.dst)
        else:
            self._execute_copy(self.src, self.dst)
        self.clear_selection()

    def do_swap(self):
        if not self._validate_selection():
            return
        src_panel, src_track, src_slot = self.src
        dst_panel, dst_track, dst_slot = self.dst

        if src_panel is not dst_panel:
            messagebox.showerror("Cannot perform action", "Swap is only supported within the same "
                                                            "panel (source and destination must both "
                                                            "be on top or both on the bottom).")
            return
        if src_panel.selected_bank_idx != dst_panel.selected_bank_idx:
            messagebox.showerror("Cannot perform action", "Swap is only supported within a single bank.")
            return

        bank = src_panel.savefile.banks[src_panel.selected_bank_idx]
        if bank is None:
            return

        is_column = src_track is None
        try:
            if is_column:
                for track in range(5):
                    bank.swap_pattern(track, src_slot, track, dst_slot)
            else:
                bank.swap_pattern(src_track, src_slot, dst_track, dst_slot)
        except Exception as e:
            messagebox.showerror("Error during swap", str(e))
            return

        self.mark_dirty(src_panel)
        if is_column:
            self.set_status(f"Swapped column: slot {slot_hex(src_slot)} <-> "
                             f"slot {slot_hex(dst_slot)} (all 5 tracks, "
                             f"[{src_panel.panel_id}] bank {src_panel.selected_bank_idx})")
        else:
            self.set_status(f"Swapped: {TRACK_LABELS[src_track]} slot {slot_hex(src_slot)} <-> "
                             f"{TRACK_LABELS[dst_track]} slot {slot_hex(dst_slot)} "
                             f"([{src_panel.panel_id}] bank {src_panel.selected_bank_idx})")
        src_panel.refresh_bank_list()
        src_panel.refresh_grid()
        self.clear_selection()

    def do_clear(self):
        if self.src is None:
            messagebox.showinfo("Info", "First select the pattern (or column) you want to clear "
                                         "(as the source).")
            return
        panel, track, slot = self.src
        bank = panel.current_bank()
        if bank is None or bank.legacy:
            return

        if track is None:
            if not messagebox.askyesno("Clear column",
                                        f"Really clear all 5 tracks of slot {slot_hex(slot)} in "
                                        f"[{panel.panel_id}] bank {panel.selected_bank_idx}?"):
                return
            for t in range(5):
                bank.clear_pattern(t, slot)
            self.mark_dirty(panel)
            self.set_status(f"Cleared column: [{panel.panel_id}] bank {panel.selected_bank_idx} "
                             f"slot {slot_hex(slot)} (all 5 tracks)")
        else:
            if not messagebox.askyesno("Clear pattern",
                                        f"Really clear the pattern in [{panel.panel_id}] bank "
                                        f"{panel.selected_bank_idx}, {TRACK_LABELS[track]}, slot {slot_hex(slot)}?"):
                return
            bank.clear_pattern(track, slot)
            self.mark_dirty(panel)
            self.set_status(f"Cleared: [{panel.panel_id}] bank {panel.selected_bank_idx} "
                             f"{TRACK_LABELS[track]} slot {slot_hex(slot)}")

        panel.refresh_bank_list()
        self.clear_selection()


def main():
    root = tk.Tk()
    app = FMSManagerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
