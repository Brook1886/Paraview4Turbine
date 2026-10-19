#!/usr/bin/env python3
"""Give each toolbar macro its logo in ParaView 6.1+.

ParaView 6.1 stores per-macro icons in its settings file under an ``[Icons]``
group as a QSettings array named ``Macros`` -- each element an ``item`` (the
macro's ``.py`` path) and an ``icon`` (an image path). Setting these is what the
**Macros > Edit Macros** dialog does; this writes them straight in, one per
macro, so the buttons show their icons on the next launch.

    ./install_icons.py [--settings ~/.config/ParaView/ParaView.ini]

ParaView rewrites its settings on exit, so this refuses to run while it is open.
If the format does not take (a different ParaView build), the Edit Macros dialog
still works by hand.
"""

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
ICONS = HERE / "icons"
PARAVIEW_CONFIG = Path.home() / ".config" / "ParaView"
MACROS_DIR = PARAVIEW_CONFIG / "Macros"
# ParaView manages its custom icons in a folder of its own next to the settings;
# an icon it is told to use is expected to live here, so the svgs are copied in.
USER_ICONS = PARAVIEW_CONFIG / "Icons"
DEFAULT_SETTINGS = PARAVIEW_CONFIG / "ParaView.ini"      # the 6.1 settings file
BACKUP_SUFFIX = ".before-veclys-postprocessor-icons"
GROUP = "Icons"          # the ini [section]
ARRAY = "Macros"         # the QSettings array -> keys "Macros\\N\\item"


def paraview_is_running():
    # Match the process by name, not -f: a -f search also matches this script's
    # own command line, since the word "paraview-real" appears in it.
    for name in ("paraview-real", "pvpython", "pvbatch"):
        try:
            if subprocess.run(["pgrep", "-x", name],
                              stdout=subprocess.PIPE).returncode == 0:
                return True
        except OSError:
            return False
    return False


def icon_for(macro):
    """The icon in icons/ that goes with a macro: 'Radial Profile.py' ->
    'radial_profile'. A PNG is preferred over the SVG, because QIcon loads a PNG
    with no dependence on a working SVG renderer -- which is what matters when
    this is set up on someone else's machine."""
    stem = macro.stem.lower().replace(" ", "_")
    png, svg = ICONS / (stem + ".png"), ICONS / (stem + ".svg")
    return png if png.exists() else svg


def pairs():
    """[(macro .py path, icon path in ParaView's icons dir)] for each macro.

    The svg is copied into ParaView's own icons folder, which is where it looks
    for a custom icon; the returned icon path points at that copy.
    """
    USER_ICONS.mkdir(parents=True, exist_ok=True)
    found = []
    for macro in sorted(MACROS_DIR.glob("*.py")):
        icon = icon_for(macro)
        if not icon.exists():
            print("  %-24s no icon (%s missing)" % (macro.stem, icon.name))
            continue
        installed = USER_ICONS / icon.name
        shutil.copyfile(str(icon), str(installed))
        found.append((macro, installed))
    return found


def macros_section(items):
    """The [Macros] group, as QSettings writes an array in its ini format.

    Interior spaces need no quotes in a QSettings ini value, which is why the
    macro paths are written bare.
    """
    lines = ["[%s]" % GROUP]
    for index, (macro, icon) in enumerate(items, start=1):
        lines.append("%s\\%d\\icon=%s" % (ARRAY, index, icon))
        lines.append("%s\\%d\\item=%s" % (ARRAY, index, macro))
    lines.append("%s\\size=%d" % (ARRAY, len(items)))
    return "\n".join(lines) + "\n"


def without_our_groups(text):
    """The settings with our icon groups taken out, so a re-run replaces rather
    than doubles them: the current [Icons] group, and any stale [Macros] group
    written by an earlier, wrong version of this script."""
    for group in (GROUP, "Macros"):
        pattern = re.compile(r"(?ms)^\[%s\]\s*\n.*?(?=^\[|\Z)" % re.escape(group))
        text = pattern.sub("", text)
    return text


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS,
                        help="ParaView's settings file (6.1: ParaView.ini)")
    parser.add_argument("--force", action="store_true",
                        help="edit even while ParaView is open, and lose it")
    arguments = parser.parse_args()

    if paraview_is_running() and not arguments.force:
        sys.exit("ParaView is open, and it rewrites its settings when it closes, "
                 "so this would be lost. Close ParaView and run it again.")
    if not arguments.settings.exists():
        sys.exit("No settings at %s. Start ParaView 6.1 once and close it so it "
                 "writes them, or pass --settings." % arguments.settings)

    print("Icons:")
    items = pairs()
    if not items:
        sys.exit("  No macro/icon pairs found. Link the macros into %s and keep "
                 "the svgs in %s." % (MACROS_DIR, ICONS))

    text = arguments.settings.read_text()
    backup = arguments.settings.with_name(arguments.settings.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(str(arguments.settings), str(backup))
        print("  kept a copy of the settings as %s" % backup.name)

    text = without_our_groups(text).rstrip("\n") + "\n\n" + macros_section(items)
    arguments.settings.write_text(text)
    for macro, icon in items:
        print("  %-24s -> %s" % (macro.stem, icon.name))
    print("Done. Start ParaView 6.1 and the Macros toolbar shows the icons.")


if __name__ == "__main__":
    main()
