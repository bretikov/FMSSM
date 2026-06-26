"""
fmssm_format.py

Parser / serializer for the save format of the GBA groovebox FMS (version 20).

File structure:
  - Sector 0 (0x1000 B): bank directory + global settings + presets
  - Sectors 1..N (0x1000 B each): data pool for the individual banks

This module is pure logic (no GUI). Main classes:
  - SaveFile   - the whole .sav file (sector0 + banks)
  - Bank       - a single bank (metadata + patterns)
  - Pattern    - a single pattern (trigMask + steps)
  - FMStep / NoiseStep - individual steps

Safety note about bank versions:
  A bank has a `bankVersion` byte (offset 0x0F in the metadata). If its
  low nibble == 0x1, it's the current format (v20) and the real version
  lives in the `extVersion` field (u16 at offset 0x09D0). If the low
  nibble is anything else, it's an older bank format (written by older
  firmware) with a different metadata layout, which this module does
  NOT parse in depth - such a bank is loaded as "legacy" and its bytes
  are simply copied 1:1 when working with the save (it can be
  moved/deleted, but its patterns cannot be edited).
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional


SECTOR_SIZE = 0x1000
META_SIZE = 0x0A62
MAX_BANK_SECTORS = 5
DATA_SECTORS_FLASH = 31
DATA_SECTORS_SRAM = 7

CURRENT_FORMAT_VERSION = 20
BANK_VERSION_SENTINEL = 0x1  # low nibble meaning "real version is in extVersion"

CHARSET = [str(i) for i in range(10)] + [chr(ord('A') + i) for i in range(26)] + ['-']


# ---------------------------------------------------------------------------
# Helpers: bank name encoding
# ---------------------------------------------------------------------------

def decode_bank_name(raw: bytes) -> str:
    out = []
    for v in raw:
        if v == 0xFF or v == 36:
            out.append('-')
        elif 0 <= v <= 35:
            out.append(CHARSET[v])
        else:
            out.append('?')
    return ''.join(out)


def encode_bank_name(name: str) -> bytes:
    name = (name.upper() + '----')[:4]
    out = bytearray()
    for ch in name:
        if ch == '-':
            out.append(36)
        elif ch.isdigit():
            out.append(int(ch))
        elif 'A' <= ch <= 'Z':
            out.append(10 + ord(ch) - ord('A'))
        else:
            out.append(36)
    return bytes(out)


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

FM_STEP_SIZE = 24
NOISE_STEP_SIZE = 12


@dataclass
class FMStep:
    """A single FM track step (24 bytes). `trig` is also tracked
    separately by Pattern (it determines whether the step is active in
    trigMask), but the stored 'trig' byte (1=ON, 2=LESS) is kept here
    too for round-trip fidelity."""
    raw: bytes  # exactly 24 bytes, including the trig byte at offset 0

    SIZE = FM_STEP_SIZE

    @property
    def trig(self) -> int:
        return self.raw[0]

    @property
    def note(self) -> int:
        return self.raw[1]

    @property
    def level(self) -> int:
        return self.raw[2]

    def __repr__(self):
        return f"FMStep(trig={self.trig}, note={self.note}, level={self.level})"

    @staticmethod
    def from_bytes(b: bytes) -> "FMStep":
        assert len(b) == FM_STEP_SIZE
        return FMStep(raw=bytes(b))

    def to_bytes(self) -> bytes:
        return self.raw


@dataclass
class NoiseStep:
    raw: bytes  # exactly 12 bytes

    SIZE = NOISE_STEP_SIZE

    @property
    def trig(self) -> int:
        return self.raw[0]

    @property
    def rate(self) -> int:
        return self.raw[1]

    @property
    def level(self) -> int:
        return self.raw[2]

    def __repr__(self):
        return f"NoiseStep(trig={self.trig}, rate={self.rate}, level={self.level})"

    @staticmethod
    def from_bytes(b: bytes) -> "NoiseStep":
        assert len(b) == NOISE_STEP_SIZE
        return NoiseStep(raw=bytes(b))

    def to_bytes(self) -> bytes:
        return self.raw


# ---------------------------------------------------------------------------
# Pattern (compressed data of a single slot)
# ---------------------------------------------------------------------------

@dataclass
class Pattern:
    """A pattern is sparse - only steps with an active bit in trigMask
    are stored. steps is a dict {step_index(0..15): FMStep|NoiseStep}."""
    is_noise: bool
    steps: dict = field(default_factory=dict)  # {0..15: FMStep/NoiseStep}

    @property
    def trig_mask(self) -> int:
        mask = 0
        for idx in self.steps:
            mask |= (1 << idx)
        return mask

    def step_size(self) -> int:
        return NOISE_STEP_SIZE if self.is_noise else FM_STEP_SIZE

    @staticmethod
    def parse(data: bytes, offset: int, is_noise: bool) -> tuple["Pattern", int]:
        """Parses a single pattern from `data` starting at `offset`.
        Returns (Pattern, new_offset)."""
        trig_mask = struct.unpack_from('<H', data, offset)[0]
        pos = offset + 2
        step_size = NOISE_STEP_SIZE if is_noise else FM_STEP_SIZE
        steps = {}
        for bit in range(16):
            if trig_mask & (1 << bit):
                raw = data[pos:pos + step_size]
                if len(raw) != step_size:
                    raise ValueError(
                        f"Pattern data too short at bit {bit} "
                        f"(offset {pos}, need {step_size} B, have {len(raw)} B)"
                    )
                if is_noise:
                    steps[bit] = NoiseStep.from_bytes(raw)
                else:
                    steps[bit] = FMStep.from_bytes(raw)
                pos += step_size
        return Pattern(is_noise=is_noise, steps=steps), pos

    def to_bytes(self) -> bytes:
        out = bytearray()
        out += struct.pack('<H', self.trig_mask)
        for bit in sorted(self.steps.keys()):
            out += self.steps[bit].to_bytes()
        return bytes(out)

    def byte_size(self) -> int:
        return 2 + len(self.steps) * self.step_size()

    def is_empty(self) -> bool:
        return len(self.steps) == 0

    def copy(self) -> "Pattern":
        return Pattern(is_noise=self.is_noise, steps=dict(self.steps))


# ---------------------------------------------------------------------------
# Bank
# ---------------------------------------------------------------------------

N_FM_TRACKS = 4
N_TRACKS = 5  # 4x FM + 1x noise
N_SLOTS = 16


@dataclass
class Bank:
    """A single groovebox bank.

    legacy: True means the bank was saved by older firmware
    (bankVersion low nibble != sentinel). For such a bank we do NOT
    parse the internal structure - we only keep the original raw
    bytes (raw_legacy_data) for later 1:1 write-back. It can be
    moved/deleted/exported, but its individual patterns cannot be
    edited.
    """
    index: int  # 0-7, bank-id stamp from bankVersion
    legacy: bool = False
    legacy_version: Optional[int] = None  # old version (if legacy)
    raw_legacy_data: Optional[bytes] = None  # full bank bytes, if legacy

    # --- the following fields are only valid if legacy == False ---
    ext_version: int = CURRENT_FORMAT_VERSION

    # track metadata (15 B)
    fm_current_pattern: list = field(default_factory=lambda: [0, 0, 0, 0])
    ns_current_pattern: int = 0
    fm_length: list = field(default_factory=lambda: [16, 16, 16, 16])
    ns_length: int = 16
    fm_rate_div: list = field(default_factory=lambda: [1, 1, 1, 1])
    ns_rate_div: int = 1

    # other per-track/slot fields - kept as raw blocks so that during a
    # repack they are simply copied (they aren't the subject of moving
    # pattern data itself, but they MUST travel together with the
    # pattern, since they carry e.g. the length/rate/echo settings for
    # that pattern).
    fm_pat_length: list = field(default_factory=lambda: [[16]*16 for _ in range(4)])
    fm_pat_rate: list = field(default_factory=lambda: [[1]*16 for _ in range(4)])
    ns_pat_length: list = field(default_factory=lambda: [16]*16)
    ns_pat_rate: list = field(default_factory=lambda: [1]*16)

    fm_echo_repeats: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    fm_echo_interval: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    fm_echo_stereo: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    fm_echo_vol_decay: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    fm_echo_mod_decay: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    fm_echo_transpose: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    fm_echo_tsp_accum: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    fm_shuffle: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])

    ns_shuffle: list = field(default_factory=lambda: [0]*16)
    ns_echo_repeats: list = field(default_factory=lambda: [0]*16)
    ns_echo_interval: list = field(default_factory=lambda: [0]*16)
    ns_echo_stereo: list = field(default_factory=lambda: [0]*16)
    ns_echo_vol_decay: list = field(default_factory=lambda: [0]*16)
    ns_echo_rate_shift: list = field(default_factory=lambda: [0]*16)
    ns_echo_rate_accum: list = field(default_factory=lambda: [0]*16)

    fm_transpose_rates: list = field(default_factory=lambda: [[[1]*8 for _ in range(16)] for _ in range(4)])
    fm_transpose_len: list = field(default_factory=lambda: [[1]*16 for _ in range(4)])
    fm_transpose_steps: list = field(default_factory=lambda: [[[0]*8 for _ in range(16)] for _ in range(4)])
    fm_transpose_mode: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])

    fm_pat_mods: list = field(default_factory=lambda: [[bytes(6)]*16 for _ in range(4)])  # ModConfig raw 6B
    ns_pat_mods: list = field(default_factory=lambda: [bytes(6)]*16)

    fm_direction: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])
    ns_direction: list = field(default_factory=lambda: [0]*16)
    fm_echo_fbk_decay: list = field(default_factory=lambda: [[0]*16 for _ in range(4)])

    # patterns[track][slot] = Pattern or None.  track 0-3 = FM, track 4 = noise
    patterns: list = field(default_factory=lambda: [[None]*16 for _ in range(5)])

    # ------------------------------------------------------------------
    # Creating an empty bank
    # ------------------------------------------------------------------

    @staticmethod
    def create_empty(index: int) -> "Bank":
        """Creates a brand-new, empty bank (all patterns None, all
        settings at their default values) with the given bank-id."""
        return Bank(index=index, legacy=False, ext_version=CURRENT_FORMAT_VERSION)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def parse(data: bytes, sector_start: int, sector_count: int, expected_index: int) -> "Bank":
        off = sector_start * SECTOR_SIZE
        total_size = sector_count * SECTOR_SIZE
        bank_bytes = data[off:off + total_size]

        if len(bank_bytes) < META_SIZE:
            raise ValueError(f"Bank {expected_index}: data too short ({len(bank_bytes)} B)")

        meta = bank_bytes[:META_SIZE]
        bank_version_byte = meta[0x0F]
        hi_nibble = bank_version_byte >> 4
        lo_nibble = bank_version_byte & 0xF

        if lo_nibble != BANK_VERSION_SENTINEL:
            # legacy format - we don't parse it in depth
            return Bank(
                index=expected_index,
                legacy=True,
                legacy_version=lo_nibble,
                raw_legacy_data=bank_bytes,
            )

        ext_version = struct.unpack_from('<H', meta, 0x09D0)[0]

        bank = Bank(index=expected_index, legacy=False, ext_version=ext_version)

        # --- track metadata (15 B) ---
        tm = meta[0:15]
        bank.fm_current_pattern = list(tm[0:4])
        bank.ns_current_pattern = tm[4]
        bank.fm_length = list(tm[5:9])
        bank.ns_length = tm[9]
        bank.fm_rate_div = list(tm[10:14])
        bank.ns_rate_div = tm[14]

        # --- per track/slot arrays ---
        def read_u8_grid(off_, n_tracks, n_slots):
            block = meta[off_:off_ + n_tracks * n_slots]
            return [list(block[t*n_slots:(t+1)*n_slots]) for t in range(n_tracks)]

        def read_u8_flat(off_, n):
            return list(meta[off_:off_ + n])

        def read_s8_grid(off_, n_tracks, n_slots):
            block = meta[off_:off_ + n_tracks * n_slots]
            return [[struct.unpack_from('b', block, t*n_slots+s)[0] for s in range(n_slots)]
                    for t in range(n_tracks)]

        def read_s8_flat(off_, n):
            return [struct.unpack_from('b', meta, off_+i)[0] for i in range(n)]

        bank.fm_pat_length = read_u8_grid(0x0060, 4, 16)
        bank.fm_pat_rate = read_u8_grid(0x00A0, 4, 16)
        bank.ns_pat_length = read_u8_flat(0x00E0, 16)
        bank.ns_pat_rate = read_u8_flat(0x00F0, 16)

        bank.fm_echo_repeats = read_u8_grid(0x0100, 4, 16)
        bank.fm_echo_interval = read_u8_grid(0x0140, 4, 16)
        bank.fm_echo_stereo = read_u8_grid(0x0180, 4, 16)
        bank.fm_echo_vol_decay = read_s8_grid(0x01C0, 4, 16)
        bank.fm_echo_mod_decay = read_s8_grid(0x0200, 4, 16)
        bank.fm_echo_transpose = read_s8_grid(0x0240, 4, 16)
        bank.fm_echo_tsp_accum = read_u8_grid(0x0280, 4, 16)
        bank.fm_shuffle = read_u8_grid(0x02C0, 4, 16)

        bank.ns_shuffle = read_u8_flat(0x0300, 16)
        bank.ns_echo_repeats = read_u8_flat(0x0310, 16)
        bank.ns_echo_interval = read_u8_flat(0x0320, 16)
        bank.ns_echo_stereo = read_u8_flat(0x0330, 16)
        bank.ns_echo_vol_decay = read_s8_flat(0x0340, 16)
        bank.ns_echo_rate_shift = read_s8_flat(0x0350, 16)
        bank.ns_echo_rate_accum = read_u8_flat(0x0360, 16)

        # fmTransposeRates[4][16][8] @ 0x0370 (512 B)
        ttr = meta[0x0370:0x0370+512]
        bank.fm_transpose_rates = [
            [list(ttr[(t*16+s)*8:(t*16+s)*8+8]) for s in range(16)]
            for t in range(4)
        ]
        bank.fm_transpose_len = read_u8_grid(0x0570, 4, 16)

        # fmTransposeSteps[4][16][8] s8 @ 0x05B0 (512 B)
        tts = meta[0x05B0:0x05B0+512]
        bank.fm_transpose_steps = [
            [[struct.unpack_from('b', tts, (t*16+s)*8+k)[0] for k in range(8)] for s in range(16)]
            for t in range(4)
        ]
        bank.fm_transpose_mode = read_u8_grid(0x07B0, 4, 16)

        # fmPatMods[4][16] ModConfig 6B each @ 0x07F0 (384 B)
        pm = meta[0x07F0:0x07F0+384]
        bank.fm_pat_mods = [
            [bytes(pm[(t*16+s)*6:(t*16+s)*6+6]) for s in range(16)]
            for t in range(4)
        ]
        # nsPatMods[16] @ 0x0970 (96 B)
        npm = meta[0x0970:0x0970+96]
        bank.ns_pat_mods = [bytes(npm[s*6:s*6+6]) for s in range(16)]

        # extVersion uz mame (0x09D0)
        bank.fm_direction = read_u8_grid(0x09D2, 4, 16)
        bank.ns_direction = read_u8_flat(0x0A12, 16)
        bank.fm_echo_fbk_decay = read_s8_grid(0x0A22, 4, 16)

        # --- slotSaved + compressed pattern stream ---
        slot_saved = meta[0x10:0x10+80]  # [5][16]

        def is_slot_saved(track, slot):
            return slot_saved[track*16+slot] == 1

        pattern_stream = bank_bytes[META_SIZE:]
        pos = 0
        for track in range(5):
            is_noise = (track == 4)
            for slot in range(16):
                if is_slot_saved(track, slot):
                    pat, pos = Pattern.parse(pattern_stream, pos, is_noise)
                    bank.patterns[track][slot] = pat
                else:
                    bank.patterns[track][slot] = None

        return bank

    # ------------------------------------------------------------------
    # Serializace
    # ------------------------------------------------------------------

    def slot_saved_bytes(self) -> bytes:
        out = bytearray(80)
        for track in range(5):
            for slot in range(16):
                if self.patterns[track][slot] is not None and not self.patterns[track][slot].is_empty():
                    out[track*16+slot] = 1
                elif self.patterns[track][slot] is not None:
                    # existing, but empty pattern (trigMask==0) - we treat it as "saved"
                    # because in the original data even an empty trigMask==0 pattern
                    # can be explicitly stored (2 B). We preserve the original behaviour.
                    out[track*16+slot] = 1
                else:
                    out[track*16+slot] = 0
        return bytes(out)

    def pattern_stream_bytes(self) -> bytes:
        out = bytearray()
        for track in range(5):
            for slot in range(16):
                pat = self.patterns[track][slot]
                if pat is not None:
                    out += pat.to_bytes()
        return bytes(out)

    def metadata_bytes(self) -> bytes:
        """Builds the 0x0A62 bytes of fixed metadata (without the compressed stream)."""
        if self.legacy:
            raise ValueError("Cannot serialize metadata for a legacy bank - use raw_legacy_data")

        buf = bytearray(META_SIZE)
        # track metadata (15 B)
        buf[0:4] = bytes(self.fm_current_pattern)
        buf[4] = self.ns_current_pattern
        buf[5:9] = bytes(self.fm_length)
        buf[9] = self.ns_length
        buf[10:14] = bytes(self.fm_rate_div)
        buf[14] = self.ns_rate_div

        # bankVersion byte: hi nibble = index, lo nibble = sentinel
        buf[0x0F] = ((self.index & 0xF) << 4) | BANK_VERSION_SENTINEL

        # slotSaved[5][16]
        buf[0x10:0x10+80] = self.slot_saved_bytes()

        def write_u8_grid(off_, grid):
            for t, row in enumerate(grid):
                for s, v in enumerate(row):
                    buf[off_ + t*16 + s] = v & 0xFF

        def write_u8_flat(off_, row):
            for s, v in enumerate(row):
                buf[off_ + s] = v & 0xFF

        def write_s8_grid(off_, grid):
            for t, row in enumerate(grid):
                for s, v in enumerate(row):
                    struct.pack_into('b', buf, off_ + t*16 + s, v)

        def write_s8_flat(off_, row):
            for s, v in enumerate(row):
                struct.pack_into('b', buf, off_ + s, v)

        write_u8_grid(0x0060, self.fm_pat_length)
        write_u8_grid(0x00A0, self.fm_pat_rate)
        write_u8_flat(0x00E0, self.ns_pat_length)
        write_u8_flat(0x00F0, self.ns_pat_rate)

        write_u8_grid(0x0100, self.fm_echo_repeats)
        write_u8_grid(0x0140, self.fm_echo_interval)
        write_u8_grid(0x0180, self.fm_echo_stereo)
        write_s8_grid(0x01C0, self.fm_echo_vol_decay)
        write_s8_grid(0x0200, self.fm_echo_mod_decay)
        write_s8_grid(0x0240, self.fm_echo_transpose)
        write_u8_grid(0x0280, self.fm_echo_tsp_accum)
        write_u8_grid(0x02C0, self.fm_shuffle)

        write_u8_flat(0x0300, self.ns_shuffle)
        write_u8_flat(0x0310, self.ns_echo_repeats)
        write_u8_flat(0x0320, self.ns_echo_interval)
        write_u8_flat(0x0330, self.ns_echo_stereo)
        write_s8_flat(0x0340, self.ns_echo_vol_decay)
        write_s8_flat(0x0350, self.ns_echo_rate_shift)
        write_u8_flat(0x0360, self.ns_echo_rate_accum)

        # fmTransposeRates[4][16][8]
        for t in range(4):
            for s in range(16):
                base = 0x0370 + (t*16+s)*8
                buf[base:base+8] = bytes(self.fm_transpose_rates[t][s])
        write_u8_grid(0x0570, self.fm_transpose_len)

        for t in range(4):
            for s in range(16):
                base = 0x05B0 + (t*16+s)*8
                for k in range(8):
                    struct.pack_into('b', buf, base+k, self.fm_transpose_steps[t][s][k])

        write_u8_grid(0x07B0, self.fm_transpose_mode)

        for t in range(4):
            for s in range(16):
                base = 0x07F0 + (t*16+s)*6
                buf[base:base+6] = self.fm_pat_mods[t][s]
        for s in range(16):
            base = 0x0970 + s*6
            buf[base:base+6] = self.ns_pat_mods[s]

        struct.pack_into('<H', buf, 0x09D0, self.ext_version)

        write_u8_grid(0x09D2, self.fm_direction)
        write_u8_flat(0x0A12, self.ns_direction)
        write_s8_grid(0x0A22, self.fm_echo_fbk_decay)

        return bytes(buf)

    def to_bytes(self) -> bytes:
        """Returns the raw bank bytes WITHOUT padding (meta + stream).
        Padding up to the sector boundary is added by SaveFile.repack()."""
        if self.legacy:
            return self.raw_legacy_data
        return self.metadata_bytes() + self.pattern_stream_bytes()

    def needed_sectors(self) -> int:
        size = len(self.to_bytes())
        sectors = -(-size // SECTOR_SIZE)  # ceil div
        return max(1, sectors)

    # ------------------------------------------------------------------
    # Pattern operations (move/copy/swap)
    # ------------------------------------------------------------------

    def get_pattern(self, track: int, slot: int) -> Optional[Pattern]:
        self._check_not_legacy()
        return self.patterns[track][slot]

    def set_pattern(self, track: int, slot: int, pattern: Optional[Pattern]):
        self._check_not_legacy()
        self.patterns[track][slot] = pattern

    def move_pattern(self, src_track: int, src_slot: int, dst_track: int, dst_slot: int):
        """Moves a pattern (including its per-slot settings such as
        length/rate/echo/transpose/mod/direction) from the source to the
        destination slot. The destination slot is overwritten; the
        source is cleared (None)."""
        self._check_not_legacy()
        self._validate_track_slot(src_track, src_slot)
        self._validate_track_slot(dst_track, dst_slot)
        if (src_track in (0, 1, 2, 3)) != (dst_track in (0, 1, 2, 3)):
            raise ValueError("Cannot move a pattern between an FM track and the noise track (different step format)")

        self._copy_slot_settings(src_track, src_slot, dst_track, dst_slot)
        self.patterns[dst_track][dst_slot] = self.patterns[src_track][src_slot]
        self.patterns[src_track][src_slot] = None
        self._clear_slot_settings(src_track, src_slot)

    def copy_pattern(self, src_track: int, src_slot: int, dst_track: int, dst_slot: int):
        """Like move_pattern, but the source slot remains unchanged."""
        self._check_not_legacy()
        self._validate_track_slot(src_track, src_slot)
        self._validate_track_slot(dst_track, dst_slot)
        if (src_track in (0, 1, 2, 3)) != (dst_track in (0, 1, 2, 3)):
            raise ValueError("Cannot copy a pattern between an FM track and the noise track (different step format)")

        self._copy_slot_settings(src_track, src_slot, dst_track, dst_slot)
        src_pat = self.patterns[src_track][src_slot]
        self.patterns[dst_track][dst_slot] = src_pat.copy() if src_pat is not None else None

    def swap_pattern(self, track_a: int, slot_a: int, track_b: int, slot_b: int):
        """Swaps two patterns including their per-slot settings."""
        self._check_not_legacy()
        self._validate_track_slot(track_a, slot_a)
        self._validate_track_slot(track_b, slot_b)
        if (track_a in (0, 1, 2, 3)) != (track_b in (0, 1, 2, 3)):
            raise ValueError("Cannot swap a pattern between an FM track and the noise track (different step format)")

        # save a copy of source A, then move B->A, then move the saved copy of A->B
        a_pattern = self.patterns[track_a][slot_a]
        a_settings = self._extract_slot_settings(track_a, slot_a)

        self._copy_slot_settings(track_b, slot_b, track_a, slot_a)
        self.patterns[track_a][slot_a] = self.patterns[track_b][slot_b]

        self._apply_slot_settings(track_b, slot_b, a_settings)
        self.patterns[track_b][slot_b] = a_pattern

    def clear_pattern(self, track: int, slot: int):
        self._check_not_legacy()
        self._validate_track_slot(track, slot)
        self.patterns[track][slot] = None
        self._clear_slot_settings(track, slot)

    # -- internal helper methods --

    def _check_not_legacy(self):
        if self.legacy:
            raise ValueError(
                f"Bank {self.index} was saved by older firmware (version {self.legacy_version}) "
                f"and individual patterns in it cannot be edited."
            )

    def _validate_track_slot(self, track: int, slot: int):
        if not (0 <= track < 5):
            raise ValueError(f"track must be 0-4, got {track}")
        if not (0 <= slot < 16):
            raise ValueError(f"slot must be 0-15, got {slot}")

    def _is_fm(self, track: int) -> bool:
        return track in (0, 1, 2, 3)

    def _extract_slot_settings(self, track: int, slot: int) -> dict:
        if self._is_fm(track):
            return dict(
                length=self.fm_pat_length[track][slot],
                rate=self.fm_pat_rate[track][slot],
                echo_repeats=self.fm_echo_repeats[track][slot],
                echo_interval=self.fm_echo_interval[track][slot],
                echo_stereo=self.fm_echo_stereo[track][slot],
                echo_vol_decay=self.fm_echo_vol_decay[track][slot],
                echo_mod_decay=self.fm_echo_mod_decay[track][slot],
                echo_transpose=self.fm_echo_transpose[track][slot],
                echo_tsp_accum=self.fm_echo_tsp_accum[track][slot],
                shuffle=self.fm_shuffle[track][slot],
                transpose_rates=list(self.fm_transpose_rates[track][slot]),
                transpose_len=self.fm_transpose_len[track][slot],
                transpose_steps=list(self.fm_transpose_steps[track][slot]),
                transpose_mode=self.fm_transpose_mode[track][slot],
                pat_mods=self.fm_pat_mods[track][slot],
                direction=self.fm_direction[track][slot],
                echo_fbk_decay=self.fm_echo_fbk_decay[track][slot],
            )
        else:
            return dict(
                length=self.ns_pat_length[slot],
                rate=self.ns_pat_rate[slot],
                shuffle=self.ns_shuffle[slot],
                echo_repeats=self.ns_echo_repeats[slot],
                echo_interval=self.ns_echo_interval[slot],
                echo_stereo=self.ns_echo_stereo[slot],
                echo_vol_decay=self.ns_echo_vol_decay[slot],
                echo_rate_shift=self.ns_echo_rate_shift[slot],
                echo_rate_accum=self.ns_echo_rate_accum[slot],
                pat_mods=self.ns_pat_mods[slot],
                direction=self.ns_direction[slot],
            )

    def _apply_slot_settings(self, track: int, slot: int, settings: dict):
        if self._is_fm(track):
            self.fm_pat_length[track][slot] = settings['length']
            self.fm_pat_rate[track][slot] = settings['rate']
            self.fm_echo_repeats[track][slot] = settings['echo_repeats']
            self.fm_echo_interval[track][slot] = settings['echo_interval']
            self.fm_echo_stereo[track][slot] = settings['echo_stereo']
            self.fm_echo_vol_decay[track][slot] = settings['echo_vol_decay']
            self.fm_echo_mod_decay[track][slot] = settings['echo_mod_decay']
            self.fm_echo_transpose[track][slot] = settings['echo_transpose']
            self.fm_echo_tsp_accum[track][slot] = settings['echo_tsp_accum']
            self.fm_shuffle[track][slot] = settings['shuffle']
            self.fm_transpose_rates[track][slot] = list(settings['transpose_rates'])
            self.fm_transpose_len[track][slot] = settings['transpose_len']
            self.fm_transpose_steps[track][slot] = list(settings['transpose_steps'])
            self.fm_transpose_mode[track][slot] = settings['transpose_mode']
            self.fm_pat_mods[track][slot] = settings['pat_mods']
            self.fm_direction[track][slot] = settings['direction']
            self.fm_echo_fbk_decay[track][slot] = settings['echo_fbk_decay']
        else:
            self.ns_pat_length[slot] = settings['length']
            self.ns_pat_rate[slot] = settings['rate']
            self.ns_shuffle[slot] = settings['shuffle']
            self.ns_echo_repeats[slot] = settings['echo_repeats']
            self.ns_echo_interval[slot] = settings['echo_interval']
            self.ns_echo_stereo[slot] = settings['echo_stereo']
            self.ns_echo_vol_decay[slot] = settings['echo_vol_decay']
            self.ns_echo_rate_shift[slot] = settings['echo_rate_shift']
            self.ns_echo_rate_accum[slot] = settings['echo_rate_accum']
            self.ns_pat_mods[slot] = settings['pat_mods']
            self.ns_direction[slot] = settings['direction']

    def _default_slot_settings(self, is_fm: bool) -> dict:
        if is_fm:
            return dict(
                length=16, rate=1, echo_repeats=0, echo_interval=0, echo_stereo=0,
                echo_vol_decay=0, echo_mod_decay=0, echo_transpose=0, echo_tsp_accum=0,
                shuffle=0, transpose_rates=[1]*8, transpose_len=1, transpose_steps=[0]*8,
                transpose_mode=0, pat_mods=bytes(6), direction=0, echo_fbk_decay=0,
            )
        else:
            return dict(
                length=16, rate=1, shuffle=0, echo_repeats=0, echo_interval=0,
                echo_stereo=0, echo_vol_decay=0, echo_rate_shift=0, echo_rate_accum=0,
                pat_mods=bytes(6), direction=0,
            )

    def _copy_slot_settings(self, src_track, src_slot, dst_track, dst_slot):
        settings = self._extract_slot_settings(src_track, src_slot)
        self._apply_slot_settings(dst_track, dst_slot, settings)

    def _clear_slot_settings(self, track, slot):
        self._apply_slot_settings(track, slot, self._default_slot_settings(self._is_fm(track)))


# ---------------------------------------------------------------------------
# SaveFile
# ---------------------------------------------------------------------------

@dataclass
class SaveFile:
    is_flash: bool  # True = 128KB/32 sektoru, False = 32KB/8 sektoru (SRAM)
    version: int
    bpm: int
    bank_valid: int  # bitmask - prepocitava se pri repacku, drzime jen pro referenci
    scale_mask: int
    accent_color: int
    sync_mode: int
    sync_format: int
    theme: int
    dpad_swap: int
    ab_swap: int
    bank_locked: int
    active_bank: int
    bank_names: list  # 8x str (4 characters), human-readable decoded names
    bank_name_raw: list  # 8x bytes (4 B each), exact original raw bytes -
    # used to preserve byte-for-byte fidelity (0xFF "uninitialized" vs.
    # 0x24 "explicit dash") when a name hasn't actually been edited
    bank_bpm: list  # 8x int
    bank_scale: list  # 8x int
    sync_ppq_divider: int
    fm_preset_valid: bytes  # 16 B raw marker
    ns_preset_valid: bytes  # 16 B raw marker
    fm_presets_raw: bytes  # 210 B raw (15 slotu x 14B, slot0 nepouzit -> pole pro slot1..15)
    ns_presets_raw: bytes  # 90 B raw

    banks: list  # 8x Optional[Bank]; None = nikdy neulozena banka

    # ------------------------------------------------------------------
    @staticmethod
    def create_empty(is_flash: bool = True) -> "SaveFile":
        """Creates a brand-new, empty save (no bank occupied).
        Uses sensible defaults (140 BPM, all scales enabled, etc.) -
        you can adjust them before saving."""
        return SaveFile(
            is_flash=is_flash,
            version=CURRENT_FORMAT_VERSION,
            bpm=140,
            bank_valid=0,
            scale_mask=0x0FFF,
            accent_color=0,
            sync_mode=0,
            sync_format=0,
            theme=0,
            dpad_swap=0,
            ab_swap=0,
            bank_locked=0,
            active_bank=0,
            bank_names=['----'] * 8,
            bank_name_raw=[b'\xff\xff\xff\xff'] * 8,
            bank_bpm=[0] * 8,
            bank_scale=[0] * 8,
            sync_ppq_divider=12,
            fm_preset_valid=b'\xff' * 16,
            ns_preset_valid=b'\xff' * 16,
            fm_presets_raw=b'\xff' * 210,
            ns_presets_raw=b'\xff' * 90,
            banks=[None] * 8,
        )

    @staticmethod
    def load(path: str) -> "SaveFile":
        with open(path, 'rb') as f:
            data = f.read()
        return SaveFile.from_bytes(data)

    @staticmethod
    def from_bytes(data: bytes) -> "SaveFile":
        if len(data) == 128 * 1024:
            is_flash = True
        elif len(data) == 32 * 1024:
            is_flash = False
        else:
            raise ValueError(f"Unexpected file size: {len(data)} B (expected 128KB or 32KB)")

        if data[0:4] != b'GSEQ':
            raise ValueError(f"Invalid magic marker: {data[0:4]!r} (expected b'GSEQ')")

        version = struct.unpack_from('<H', data, 0x0004)[0]
        bpm = struct.unpack_from('<H', data, 0x0006)[0]
        bank_valid = struct.unpack_from('<H', data, 0x0008)[0]
        scale_mask = struct.unpack_from('<H', data, 0x000A)[0]
        accent_color = data[0x000C]
        sync_mode = data[0x000D]
        sync_format = data[0x000E]
        theme = data[0x000F]

        bank_dir_raw = data[0x0010:0x0010+16]

        dpad_swap = data[0x0020]
        ab_swap = data[0x0021]
        bank_locked = data[0x0022]

        bank_names = []
        bank_name_raw = []
        for i in range(8):
            raw = data[0x0023+i*4:0x0023+i*4+4]
            bank_names.append(decode_bank_name(raw))
            bank_name_raw.append(bytes(raw))

        active_bank = data[0x0043]

        bank_bpm = [struct.unpack_from('<H', data, 0x0044+i*2)[0] for i in range(8)]
        bank_scale = [struct.unpack_from('<H', data, 0x0064+i*2)[0] for i in range(8)]

        sync_ppq_divider = data[0x0084]

        fm_preset_valid = data[0x0085:0x0085+16]
        ns_preset_valid = data[0x0095:0x0095+16]
        fm_presets_raw = data[0x00A5:0x00A5+210]
        ns_presets_raw = data[0x0177:0x0177+90]

        banks = [None] * 8
        for i in range(8):
            sector_start = bank_dir_raw[i*2]
            sector_count = bank_dir_raw[i*2+1]
            # sector_start == 0 means "never saved" on a freshly-zeroed
            # directory; sector_start == 0xFF (erased flash/SRAM state)
            # means the same thing on hardware where unwritten bytes
            # read back as 0xFF instead of 0x00 (seen e.g. on SRAM
            # builds). Treat both as "bank does not exist".
            if sector_start == 0 or sector_start == 0xFF:
                banks[i] = None
                continue
            banks[i] = Bank.parse(data, sector_start, sector_count, expected_index=i)

        return SaveFile(
            is_flash=is_flash, version=version, bpm=bpm, bank_valid=bank_valid,
            scale_mask=scale_mask, accent_color=accent_color, sync_mode=sync_mode,
            sync_format=sync_format, theme=theme, dpad_swap=dpad_swap, ab_swap=ab_swap,
            bank_locked=bank_locked, active_bank=active_bank, bank_names=bank_names,
            bank_name_raw=bank_name_raw,
            bank_bpm=bank_bpm, bank_scale=bank_scale, sync_ppq_divider=sync_ppq_divider,
            fm_preset_valid=fm_preset_valid, ns_preset_valid=ns_preset_valid,
            fm_presets_raw=fm_presets_raw, ns_presets_raw=ns_presets_raw,
            banks=banks,
        )

    # ------------------------------------------------------------------
    # Serializace / repack
    # ------------------------------------------------------------------

    def data_sectors(self) -> int:
        return DATA_SECTORS_FLASH if self.is_flash else DATA_SECTORS_SRAM

    def total_size(self) -> int:
        return 128*1024 if self.is_flash else 32*1024

    def repack(self) -> bytes:
        """Rebuilds the entire save file. All occupied banks are packed
        starting from sector 1 in index order 0..7, each aligned to a
        sector boundary. Returns the finished bytes at the full file size."""

        # check capacity of each individual bank
        for i, bank in enumerate(self.banks):
            if bank is None:
                continue
            sectors_needed = bank.needed_sectors()
            if sectors_needed > MAX_BANK_SECTORS:
                raise ValueError(
                    f"Bank {i} needs {sectors_needed} sectors, "
                    f"the maximum is {MAX_BANK_SECTORS} (20 KB)."
                )

        # compute total need and check pool capacity
        total_sectors_needed = sum(
            b.needed_sectors() for b in self.banks if b is not None
        )
        if total_sectors_needed > self.data_sectors():
            raise ValueError(
                f"The file would need {total_sectors_needed} data sectors, "
                f"only {self.data_sectors()} are available. "
                f"Delete/shrink a bank."
            )

        out = bytearray(self.total_size())
        for i in range(len(out)):
            out[i] = 0xFF  # erased state as the default

        # --- sector 0 ---
        out[0x0000:0x0004] = b'GSEQ'
        struct.pack_into('<H', out, 0x0004, self.version)
        struct.pack_into('<H', out, 0x0006, self.bpm)

        bank_valid = 0
        for i, bank in enumerate(self.banks):
            if bank is not None:
                bank_valid |= (1 << i)
        struct.pack_into('<H', out, 0x0008, bank_valid)

        struct.pack_into('<H', out, 0x000A, self.scale_mask)
        out[0x000C] = self.accent_color
        out[0x000D] = self.sync_mode
        out[0x000E] = self.sync_format
        out[0x000F] = self.theme

        # --- bank pool allocation (must be computed before writing bankDir) ---
        bank_dir = bytearray(16)
        next_sector = 1
        bank_bytes_list = [None]*8
        for i, bank in enumerate(self.banks):
            if bank is None:
                bank_dir[i*2] = 0
                bank_dir[i*2+1] = 0
                continue
            raw = bank.to_bytes()
            sectors_needed = bank.needed_sectors()
            bank_dir[i*2] = next_sector
            bank_dir[i*2+1] = sectors_needed
            bank_bytes_list[i] = (next_sector, sectors_needed, raw)
            next_sector += sectors_needed

        out[0x0010:0x0010+16] = bank_dir

        out[0x0020] = self.dpad_swap
        out[0x0021] = self.ab_swap
        out[0x0022] = self.bank_locked

        for i in range(8):
            raw = self.bank_name_raw[i] if i < len(self.bank_name_raw) else None
            if raw is not None and decode_bank_name(raw) == self.bank_names[i]:
                # name hasn't been edited since load/create - preserve the
                # exact original bytes (0xFF "uninitialized" vs. 0x24
                # "explicit dash" both decode to the same '-' display,
                # but are different on-disk values)
                out[0x0023+i*4:0x0023+i*4+4] = raw
            else:
                out[0x0023+i*4:0x0023+i*4+4] = encode_bank_name(self.bank_names[i])

        out[0x0043] = self.active_bank

        for i in range(8):
            struct.pack_into('<H', out, 0x0044+i*2, self.bank_bpm[i])
        for i in range(8):
            struct.pack_into('<H', out, 0x0064+i*2, self.bank_scale[i])

        out[0x0084] = self.sync_ppq_divider

        out[0x0085:0x0085+16] = self.fm_preset_valid
        out[0x0095:0x0095+16] = self.ns_preset_valid
        out[0x00A5:0x00A5+210] = self.fm_presets_raw
        out[0x0177:0x0177+90] = self.ns_presets_raw

        # --- bank data pool ---
        for i, entry in enumerate(bank_bytes_list):
            if entry is None:
                continue
            sector_start, sectors_needed, raw = entry
            file_off = sector_start * SECTOR_SIZE
            out[file_off:file_off+len(raw)] = raw
            # the rest up to the sector boundary stays 0xFF (already pre-filled)

        return bytes(out)

    def save(self, path: str):
        data = self.repack()
        with open(path, 'wb') as f:
            f.write(data)

    # ------------------------------------------------------------------
    # High-level operations across bank boundaries
    # ------------------------------------------------------------------

    def move_pattern_between_banks(self, src_bank_idx: int, src_track: int, src_slot: int,
                                     dst_bank_idx: int, dst_track: int, dst_slot: int):
        src_bank = self.banks[src_bank_idx]
        dst_bank = self.banks[dst_bank_idx]
        if src_bank is None or dst_bank is None:
            raise ValueError("Both the source and destination bank must exist")
        if src_bank.legacy or dst_bank.legacy:
            raise ValueError("Cannot move a pattern from/to a legacy bank")
        if (src_track in (0,1,2,3)) != (dst_track in (0,1,2,3)):
            raise ValueError("Cannot move a pattern between an FM track and the noise track")

        if src_bank_idx == dst_bank_idx:
            src_bank.move_pattern(src_track, src_slot, dst_track, dst_slot)
            return

        settings = src_bank._extract_slot_settings(src_track, src_slot)
        pattern = src_bank.patterns[src_track][src_slot]

        dst_bank._apply_slot_settings(dst_track, dst_slot, settings)
        dst_bank.patterns[dst_track][dst_slot] = pattern.copy() if pattern is not None else None

    def ensure_bank(self, bank_idx: int) -> "Bank":
        """Returns the bank at the given index; if it doesn't exist yet
        (None), creates a new empty bank and stores it in that slot."""
        if self.banks[bank_idx] is None:
            self.banks[bank_idx] = Bank.create_empty(bank_idx)
        return self.banks[bank_idx]

    def copy_pattern_between_banks(self, src_bank_idx: int, src_track: int, src_slot: int,
                                    dst_bank_idx: int, dst_track: int, dst_slot: int):
        """Like move_pattern_between_banks, but the source remains
        unchanged. If the destination bank doesn't exist yet, an empty
        one is created automatically."""
        src_bank = self.banks[src_bank_idx]
        if src_bank is None:
            raise ValueError("The source bank must exist")
        if src_bank.legacy:
            raise ValueError("Cannot copy a pattern from a legacy bank")
        if (src_track in (0, 1, 2, 3)) != (dst_track in (0, 1, 2, 3)):
            raise ValueError("Cannot copy a pattern between an FM track and the noise track")

        dst_bank = self.ensure_bank(dst_bank_idx)
        if dst_bank.legacy:
            raise ValueError("Cannot copy a pattern into a legacy bank")

        if src_bank_idx == dst_bank_idx:
            src_bank.copy_pattern(src_track, src_slot, dst_track, dst_slot)
            return

        settings = src_bank._extract_slot_settings(src_track, src_slot)
        pattern = src_bank.patterns[src_track][src_slot]
        dst_bank._apply_slot_settings(dst_track, dst_slot, settings)
        dst_bank.patterns[dst_track][dst_slot] = pattern.copy() if pattern is not None else None


def copy_pattern_across_savefiles(src_save: "SaveFile", src_bank_idx: int, src_track: int, src_slot: int,
                                   dst_save: "SaveFile", dst_bank_idx: int, dst_track: int, dst_slot: int):
    """Copies a pattern (and its per-slot settings) from the source
    SaveFile (any bank/track/slot) into the DESTINATION SaveFile, which
    can be a completely different instance (typically a "new, empty
    save" being assembled by the user). If the destination bank in
    dst_save doesn't exist yet, an empty one is created. The source
    remains unchanged (this is a Copy, not a Move)."""
    src_bank = src_save.banks[src_bank_idx]
    if src_bank is None:
        raise ValueError("The source bank must exist")
    if src_bank.legacy:
        raise ValueError("Cannot copy a pattern from a legacy bank")
    if (src_track in (0, 1, 2, 3)) != (dst_track in (0, 1, 2, 3)):
        raise ValueError("Cannot copy a pattern between an FM track and the noise track")

    dst_bank = dst_save.ensure_bank(dst_bank_idx)
    if dst_bank.legacy:
        raise ValueError("Cannot copy a pattern into a legacy bank")

    settings = src_bank._extract_slot_settings(src_track, src_slot)
    pattern = src_bank.patterns[src_track][src_slot]
    dst_bank._apply_slot_settings(dst_track, dst_slot, settings)
    dst_bank.patterns[dst_track][dst_slot] = pattern.copy() if pattern is not None else None


def move_pattern_across_savefiles(src_save: "SaveFile", src_bank_idx: int, src_track: int, src_slot: int,
                                   dst_save: "SaveFile", dst_bank_idx: int, dst_track: int, dst_slot: int):
    """Like copy_pattern_across_savefiles, but the source slot is
    cleared after the move (including resetting its settings to their
    default values)."""
    src_bank = src_save.banks[src_bank_idx]
    if src_bank is None:
        raise ValueError("The source bank must exist")
    if src_bank.legacy:
        raise ValueError("Cannot move a pattern from a legacy bank")

    copy_pattern_across_savefiles(src_save, src_bank_idx, src_track, src_slot,
                                   dst_save, dst_bank_idx, dst_track, dst_slot)

    src_bank.patterns[src_track][src_slot] = None
    src_bank._clear_slot_settings(src_track, src_slot)
