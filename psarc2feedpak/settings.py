"""Conversion options and their on-disk persistence.

The GUI saves these to a small JSON file so they stick between runs. The CLI
takes the same knobs as flags and never touches the file, so scripts behave
the same on every machine.
"""

import dataclasses
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Options:
    """How packages get built. Defaults match what the tool has always done."""
    name_template: str = "{artist} - {title}"
    audio_quality: int = 5        # libvorbis -q:a, 0 (small) .. 10 (best)
    lossless_audio: bool = True   # copy the original Vorbis when ww2ogg is available
    include_audio: bool = True
    include_cover: bool = True
    include_phrases: bool = True  # the per-phrase difficulty ladder
    keep_dir: bool = False
    overwrite: bool = True        # False skips songs whose .feedpak exists


class _Blank(dict):
    def __missing__(self, key):
        return ""


def render_name(template, **fields):
    """Fill a filename template; unknown {fields} become empty.

    Raises ValueError on malformed templates (stray braces and the like).
    """
    return str(template).format_map(_Blank(**fields)).strip()


def _config_path():
    if sys.platform == "win32" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "psarc2feedpak" / "settings.json"
    return Path.home() / ".config" / "psarc2feedpak" / "settings.json"


def load():
    try:
        data = json.loads(_config_path().read_text(encoding="utf-8"))
    except Exception:
        return Options()
    defaults = Options()
    fields = {f.name: f.type for f in dataclasses.fields(Options)}
    kwargs = {}
    for name in fields:
        if name in data and isinstance(data[name], type(getattr(defaults, name))):
            kwargs[name] = data[name]
    opts = Options(**kwargs)
    opts.audio_quality = max(0, min(10, opts.audio_quality))
    if not opts.name_template.strip():
        opts.name_template = defaults.name_template
    return opts


def save(opts):
    path = _config_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dataclasses.asdict(opts), indent=2),
                        encoding="utf-8")
    except OSError:
        pass  # a read-only home dir shouldn't break conversion
