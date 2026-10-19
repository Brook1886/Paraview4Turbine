#!/usr/bin/env python3
"""Set ParaView up for this project however it is started.

`paraview_diy.sh` sets PV_PLUGIN_PATH and links the macros, so a session begun
any other way -- a desktop icon, a plain `paraview` -- has none of the plugins,
the Open dialog has never heard of `input.au3x`, and the toolbar has no buttons.
This does the same two things permanently:

* links every macro into ParaView's own Macros directory, which is where it
  looks for toolbar buttons whatever else is going on;
* ticks the **Auto Load** box against every plugin, which is what Tools > Manage
  Plugins writes into ParaView's settings file.

    ./install_autoload.py [--settings ~/.config/ParaView/ParaView.ini]

The links can be made at any time. The settings cannot: ParaView writes that
file out when it exits, so anything changed underneath a running session is
lost, and this says so rather than doing it.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
PLUGINS = HERE / "plugins"
MACROS = HERE / "macros"
PARAVIEW_CONFIG = Path.home() / ".config" / "ParaView"
DEFAULT_SETTINGS = PARAVIEW_CONFIG / "ParaView.ini"       # the 6.1 settings file
BACKUP_SUFFIX = ".before-veclys-postprocessor"

# The plugin list is one long QSettings value with the XML escaped into it, so
# every quote in it is a backslash and a quote.
QUOTE = '\\"'


def paraview_is_running():
    # Match by process name, not -f: a -f search also matches this script's own
    # command line, since the word "paraview-real" appears in it.
    for name in ("paraview-real", "pvpython", "pvbatch"):
        try:
            if subprocess.run(["pgrep", "-x", name],
                              stdout=subprocess.PIPE).returncode == 0:
                return True
        except OSError:
            return False
    return False


def entry_for(path):
    return '  <Plugin name={q}{name}{q} filename={q}{path}{q} auto_load={q}1{q} />'.format(
        q=QUOTE, name=path.stem, path=path)


def tick(text, path):
    """(the settings with this plugin set to auto-load, what happened)."""
    known = re.search(re.escape('filename=' + QUOTE + str(path) + QUOTE), text)
    if known:
        already = re.compile('(' + re.escape('filename=' + QUOTE + str(path) + QUOTE
                                             + ' auto_load=' + QUOTE) + ')0(' +
                             re.escape(QUOTE) + ')')
        updated, count = already.subn(lambda match: match.group(1) + "1" + match.group(2),
                                      text)
        return updated, ("now loads itself" if count else "already did")
    # Never seen by this ParaView, so it has to be introduced.
    closing = re.search(re.escape('</Plugins>'), text)
    if not closing:
        return text, "could not be added: no plugin list in the settings"
    return (text[:closing.start()] + entry_for(path) + "\\n" + text[closing.start():],
            "added, and now loads itself")


def link_macros():
    """Put every macro where ParaView looks for its toolbar buttons."""
    into = PARAVIEW_CONFIG / "Macros"
    into.mkdir(parents=True, exist_ok=True)
    for macro in sorted(MACROS.glob("*.py")):
        there = into / macro.name
        if there.is_symlink() or not there.exists():
            if there.is_symlink() and there.resolve() == macro.resolve():
                print("  %-24s already a button" % macro.stem)
                continue
            if there.is_symlink():
                there.unlink()
            there.symlink_to(macro)
            print("  %-24s is now a button" % macro.stem)
        else:
            # Somebody's own macro is sitting there; leave it alone.
            print("  %-24s left alone: %s is not a link" % (macro.stem, there))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS,
                        help="ParaView's settings file")
    parser.add_argument("--force", action="store_true",
                        help="edit even while ParaView is open, and lose it")
    arguments = parser.parse_args()

    print("Macros:")
    link_macros()

    print("Plugins:")
    if paraview_is_running() and not arguments.force:
        sys.exit("  ParaView is open, and it writes its settings out when it "
                 "closes, so anything changed now would be lost. The buttons "
                 "above are in place; close ParaView and run this again for the "
                 "plugins.")
    if not arguments.settings.exists():
        sys.exit("No settings at %s. Start ParaView once and close it, so that "
                 "it writes them." % arguments.settings)

    plugins = sorted(PLUGINS.glob("*.py"))
    if not plugins:
        sys.exit("No plugins in %s." % PLUGINS)

    text = arguments.settings.read_text()
    backup = arguments.settings.with_name(arguments.settings.name + BACKUP_SUFFIX)
    if not backup.exists():
        shutil.copy2(str(arguments.settings), str(backup))
        print("Kept a copy of the settings as %s" % backup.name)

    for plugin in plugins:
        text, what = tick(text, plugin)
        print("  %-24s %s" % (plugin.stem, what))
    arguments.settings.write_text(text)
    print("ParaView will load them from now on, however it is started.")


if __name__ == "__main__":
    main()
