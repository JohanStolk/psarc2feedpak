import argparse
import sys

from .convert import convert, ConversionError
from .settings import Options


def main():
    ap = argparse.ArgumentParser(
        prog="psarc2feedpak",
        description="Convert a Rocksmith 2014 .psarc into a .feedpak package.")
    ap.add_argument("psarc", help="input .psarc file")
    ap.add_argument("-o", "--output", help="output path (default: '<artist> - <title>.feedpak')")
    ap.add_argument("--keep-dir", action="store_true",
                    help="also leave the unzipped package as <name>.feedpak.dir")
    ap.add_argument("--template", default=Options.name_template, metavar="T",
                    help="filename template; fields {artist} {title} {album} "
                         "{year} (default: '%(default)s')")
    ap.add_argument("--quality", type=int, default=Options.audio_quality,
                    metavar="N", choices=range(11),
                    help="Vorbis audio quality, 0 smallest to 10 best "
                         "(default: %(default)s); only used when re-encoding")
    ap.add_argument("--reencode", action="store_true",
                    help="always re-encode the audio instead of copying the "
                         "original Vorbis losslessly (which needs ww2ogg)")
    ap.add_argument("--no-audio", action="store_true",
                    help="skip audio, charts only")
    ap.add_argument("--no-cover", action="store_true",
                    help="skip the cover art")
    ap.add_argument("--no-ladder", action="store_true",
                    help="skip the per-phrase difficulty ladder")
    ap.add_argument("--skip-existing", action="store_true",
                    help="leave a .feedpak that already exists alone")
    args = ap.parse_args()
    opts = Options(
        name_template=args.template,
        audio_quality=args.quality,
        lossless_audio=not args.reencode,
        include_audio=not args.no_audio,
        include_cover=not args.no_cover,
        include_phrases=not args.no_ladder,
        keep_dir=args.keep_dir,
        overwrite=not args.skip_existing)
    try:
        convert(args.psarc, args.output, keep_dir=args.keep_dir, options=opts)
    except ConversionError as e:
        sys.exit(f"error: {e}")


if __name__ == "__main__":
    main()
