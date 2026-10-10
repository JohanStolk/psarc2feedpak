"""Wwise .wem and .dds handling via external tools (vgmstream, ffmpeg)."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import ogg


def _search_dirs():
    """Where to look for bundled tools, most-specific first.

    Covers running from source and running as a PyInstaller build, where the
    binaries sit next to the .exe (or in its _MEIPASS temp dir) under tools/.
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots += [Path(sys.executable).resolve().parent,
                  Path(getattr(sys, "_MEIPASS", "."))]
    roots.append(Path(__file__).resolve().parent.parent)  # repo root from source
    dirs = []
    for r in roots:
        dirs += [r / "tools" / "vgmstream", r / "tools", r]
    return dirs


def find(name, extra_dirs=()):
    for d in list(extra_dirs) + _search_dirs():
        for exe in (Path(d) / f"{name}.exe", Path(d) / name):
            if exe.is_file() and (exe.suffix == ".exe" or os.access(exe, os.X_OK)):
                return str(exe)
    return shutil.which(name)


PCB_NAME = "packed_codebooks_aoTuV_603.bin"


def _find_codebooks(ww2ogg):
    """ww2ogg's codebook library: next to the binary, else in tools/."""
    dirs = [Path(ww2ogg).resolve().parent] if ww2ogg else []
    for d in dirs + _search_dirs():
        p = Path(d) / PCB_NAME
        if p.is_file():
            return str(p)
    return None


# Keep the child tools from flashing a console window under a windowed GUI build.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=_NO_WINDOW)
    if p.returncode != 0:
        raise RuntimeError(f"{Path(cmd[0]).name} failed:\n{p.stderr[-800:]}")


class Tools:
    """Bundles the external binaries so callers fail early if they're missing."""

    def __init__(self):
        self.vgmstream = find("vgmstream-cli")
        self.ffmpeg = find("ffmpeg")
        self.ww2ogg = find("ww2ogg")
        self.codebooks = _find_codebooks(self.ww2ogg)

    @property
    def can_audio(self):
        return bool(self.vgmstream and self.ffmpeg)

    @property
    def can_lossless(self):
        return bool(self.vgmstream and self.ww2ogg and self.codebooks)

    def wem_to_ogg_lossless(self, wem_bytes, out_ogg, scratch):
        """Copy the .wem's Vorbis packets into a standard .ogg, no re-encode.

        ww2ogg rebuilds the stream, ogg.rebuild() fixes its granules, and the
        result is only kept if it decodes to exactly the same PCM as vgmstream's
        decode of the original .wem. Raises RuntimeError otherwise, so the
        caller can fall back to wem_to_ogg().
        """
        wem = scratch / "_l.wem"
        raw = scratch / "_l_raw.ogg"
        ref_wav = scratch / "_l_ref.wav"
        out_wav = scratch / "_l_out.wav"
        wem.write_bytes(wem_bytes)
        try:
            _run([self.ww2ogg, str(wem), "-o", str(raw), "--pcb", self.codebooks])
            try:
                data, _ = ogg.rebuild(raw.read_bytes(), ogg.wem_sample_count(wem_bytes))
            except ValueError as e:
                raise RuntimeError(f"ogg rebuild failed: {e}") from e
            out_ogg.write_bytes(data)
            # -i: decode once, ignoring loop points, so both sides compare 1:1.
            _run([self.vgmstream, "-i", "-o", str(ref_wav), str(wem)])
            _run([self.vgmstream, "-i", "-o", str(out_wav), str(out_ogg)])
            if ref_wav.read_bytes() != out_wav.read_bytes():
                out_ogg.unlink()
                raise RuntimeError("lossless copy does not decode identically")
        finally:
            for p in (wem, raw, ref_wav, out_wav):
                if p.exists():
                    p.unlink()

    def missing(self):
        out = []
        if not self.vgmstream:
            out.append("vgmstream-cli")
        if not self.ffmpeg:
            out.append("ffmpeg")
        return out

    def wem_to_ogg(self, wem_bytes, out_ogg, scratch, quality=5):
        wem = scratch / "_a.wem"
        wav = scratch / "_a.wav"
        wem.write_bytes(wem_bytes)
        _run([self.vgmstream, "-o", str(wav), str(wem)])
        _run([self.ffmpeg, "-y", "-i", str(wav),
              "-c:a", "libvorbis", "-q:a", str(quality), str(out_ogg)])
        wem.unlink()
        wav.unlink()

    def dds_to_png(self, dds_bytes, out_png, scratch):
        dds = scratch / "_c.dds"
        dds.write_bytes(dds_bytes)
        _run([self.ffmpeg, "-y", "-i", str(dds), str(out_png)])
        dds.unlink()
