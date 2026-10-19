"""ParaView macro: plot one set of CSV files together, a plot per press.

Pick the files of a set, and each is opened and drawn on a Line Chart View
against its own radius (or first) column, its series named after the file so the
legend says which curve is which. Press it again with another set and that one
gets its own chart beside the first, so several sets -- runs to compare, rig data
beside them -- sit side by side to be read against each other. Set _A_PLOT_EACH
False to clear and redraw the one chart instead.

A file already open is drawn again rather than opened again, so the same
measurement can go on every chart it belongs on and still be one entry in the
Pipeline Browser under the name it was given.

The picker is the desktop's, through zenity. Without it, open the CSV files
yourself with File > Open, select one in the Pipeline Browser, and press this.
"""

import os
import sys

from paraview import simple as pvs

# The shared helpers are in the repo's pylib. paraview_diy.sh puts it on
# PYTHONPATH and a loaded plugin appends it too -- but this is the one button
# worth pressing with no case open, so find pylib without either: from this
# file if ParaView gave the macro a __file__, else from the symlink ParaView
# keeps it at, which points back into the repo with pylib beside macros.
for _anchor in (globals().get("__file__"),
                os.path.join(os.path.expanduser("~"), ".config", "ParaView",
                             "Macros", "Plot CSV Files.py")):
    if not _anchor or not os.path.exists(_anchor):
        continue
    _pylib = os.path.join(
        os.path.dirname(os.path.dirname(os.path.realpath(_anchor))), "pylib")
    if os.path.isdir(_pylib):
        if _pylib not in sys.path:
            sys.path.append(_pylib)
        break

try:
    import paraview_diy_macros as diy
except ImportError:
    print("Plot CSV files: could not find the shared helpers (pylib). Start "
          "ParaView with paraview_diy.sh, or open input.au3x (or any CGNS file) "
          "first so a plugin puts pylib on the path.")
    raise

_LABEL = "Plot CSV files"
_CHART = "XYChartView"
_FILES = (("Tables", "*.csv *.txt *.dat *.tsv"), ("All files", "*"))
# The tab the plots are drawn in, so they never crowd the render view or a
# profile. Several sets share it, side by side.
_TAB = "CSV Files"
# Every press gets a plot of its own, side by side in the tab, so several sets of
# files can be compared. Set this False to clear and redraw a single plot.
_A_PLOT_EACH = True
# What a column of radius tends to be called, best first; else the first column.
_RADIUS_NAMES = ("radius", "r", "rad", "span", "spanfraction", "spanwise",
                 "radiusm", "radiusmm")
# Columns that are an axis or bookkeeping rather than a quantity to draw.
_NOT_SERIES = ("Length", "Area")


def x_column(names):
    """Which column to plot against: a radius by name, else the first."""
    plain = {name.lower().replace("_", "").replace(" ", ""): name for name in names}
    for wanted in _RADIUS_NAMES:
        if wanted in plain:
            return plain[wanted]
    return names[0] if names else None


def is_table(source):
    """Whether a source came in as the table of columns a chart can draw."""
    return source.GetDataInformation().GetDataSetTypeAsString() == "vtkTable"


def active_table():
    """A single table the user has already opened and selected, if any."""
    active = pvs.GetActiveSource()
    return [active] if active is not None and is_table(active) else []


def files_of(source):
    """The paths a source was opened from, or [] if it was not opened from
    any."""
    found = []
    for held in ("FileName", "FileNames"):
        given = getattr(source, held, None)
        if given is None:
            continue
        given = diy.value(given)
        found += [given] if isinstance(given, str) else list(given)
    return [name for name in found if name]


def already_open(path):
    """A table already in the pipeline that reads this file, if there is one.

    Opening it a second time would put another reader of the same file in the
    Pipeline Browser under the same name, which is no way to tell the two apart
    -- and a file worth plotting once is often worth drawing on the next chart
    beside it, against something else. So the one already there is used again.
    """
    wanted = os.path.realpath(path)
    for source in pvs.GetSources().values():
        held = [os.path.realpath(name) for name in files_of(source)]
        # Only a table is one of these to draw: the same file may also be open
        # as a mesh, and that is not the source this is looking for.
        if wanted in held and is_table(source):
            return source
    return None


def reload_file(source):
    """Read a source's file again, for a run that has been written since.

    Older ParaView has no ReloadFiles to do it with, and a reader can decline.
    Either way the file is still drawn, from what was read the first time, so
    this says so rather than stopping -- looking at yesterday's numbers without
    being told is the thing to avoid.
    """
    reload_files = getattr(pvs, "ReloadFiles", None)
    if reload_files is not None:
        try:
            reload_files(source)
        except Exception as error:
            diy.warn("%s: %s could not be read again, so it is drawn as it was "
                     "first read: %s"
                     % (_LABEL, diy.registration_name(source), error))
    source.UpdatePipeline()


def open_files(paths):
    """A source per path, opened or found, skipping what will not read as a
    table. Returns them with the names of the ones that were already open."""
    opened, again = [], []
    for path in paths:
        source = already_open(path)
        if source is not None:
            # Read afresh, since the point of picking a file again is often
            # that it has been written again.
            reload_file(source)
            opened.append(source)
            again.append(diy.registration_name(source))
            continue
        source = pvs.OpenDataFile(path)
        if source is None:
            diy.warn("%s: ParaView could not read %s." % (_LABEL, path))
            continue
        source.UpdatePipeline()
        if not is_table(source):
            diy.warn("%s: %s did not come in as a table of columns." %
                     (_LABEL, os.path.basename(path)))
            pvs.Delete(source)
            continue
        # Name it after the file, so the Pipeline Browser and the legend say
        # which file rather than "CSVReader1".
        pvs.RenameSource(os.path.splitext(os.path.basename(path))[0], source)
        opened.append(source)
    return opened, again


def draw(table, chart):
    """Draw one table's quantities against its radius, named after the file."""
    names = diy.table_columns(table)
    against = x_column(names)
    if against is None:
        return None
    label = diy.registration_name(table)
    display = pvs.Show(table, chart)
    display.UseIndexForXAxis = 0
    display.XArrayName = against
    values = [name for name in names if name != against and name not in _NOT_SERIES]
    display.SeriesVisibility = values
    # Prefix every series with the file, so two files' Pressures are told apart.
    labels = []
    for name in list(display.SeriesLabel)[::2]:
        labels += [name, "%s: %s" % (label, name)]
    display.SeriesLabel = labels
    return against


def unique_title(base, taken, qualifier=None):
    """`base`, made distinct from the titles already on the charts.

    Two sets out of the one folder would be given the same name, and a row of
    charts all called run1 is no way to tell one from the next, which is the
    whole point of a plot each. So a name already taken is qualified -- with the
    first file of the set, which is what differs between them -- and numbered
    only if that is taken as well.
    """
    taken = set(taken)
    if base not in taken:
        return base
    if qualifier and qualifier != base:
        qualified = "%s: %s" % (base, qualifier)
        if qualified not in taken:
            return qualified
        base = qualified
    number = 2
    while "%s (%d)" % (base, number) in taken:
        number += 1
    return "%s (%d)" % (base, number)


def set_title(paths, taken=()):
    """A name for a set of files: the folder they share, else the first of them.

    A set is usually one run's worth of slices in one directory, so the folder
    tells this set from the next; a scatter of files from here and there falls
    back to the name of the first. Either way it is made distinct from the
    charts already drawn, since several sets often come out of the one folder.
    """
    folders = {os.path.dirname(os.path.abspath(path)) for path in paths}
    first = os.path.splitext(os.path.basename(paths[0]))[0]
    base = first
    if len(folders) == 1:
        shared = os.path.basename(folders.pop())
        if shared:
            base = shared
    return unique_title(base, taken, qualifier=first)


def chart_titles(charts):
    """What the charts in the tab are called, as plain strings."""
    return [str(diy.value(chart.ChartTitle)) for chart in charts]


def chart_for_a_set(existing):
    """The chart to draw a set on: a fresh one beside the last, or one reused.

    A plot per press, side by side in the tab, is what lets several sets be
    compared. The first press makes the tab; each after it sits a chart beside
    the row already there, unless _A_PLOT_EACH is off, when the one chart is
    cleared and redrawn instead.
    """
    if existing and _A_PLOT_EACH:
        return diy.view_beside(existing[-1], _CHART)
    chart, reused = diy.view_in_tab(_CHART, _TAB)
    if reused:
        diy.clear(chart)
    return chart


def main():
    # The button is for loading files: pick a set and plot them together.
    # Where there is no picker, fall back to the one table already selected.
    if diy.can_ask():
        chosen = diy.ask_for_files("CSV files to plot together", _FILES)
        if not chosen:
            return
        tables, again = open_files(chosen)
        if not tables:
            diy.warn("%s: none of the %d chosen file(s) loaded as a table of "
                     "columns." % (_LABEL, len(chosen)))
            return
        if again:
            print("%s: %s already open, so drawn again rather than opened again."
                  % (_LABEL, ", ".join(again)))
        chosen_paths = chosen
    else:
        tables = active_table()
        if not tables:
            diy.warn("%s: open the CSV files yourself -- File > Open -- then "
                     "select one and press this. (There is no zenity on this "
                     "machine to pick them with.)" % _LABEL)
            return
        chosen_paths = None

    # The titles already drawn are read before this set's chart is made, so the
    # new one can be named something the row does not already carry.
    existing = diy.views_in_tab(_CHART, _TAB)
    taken = chart_titles(existing) if _A_PLOT_EACH else []
    if chosen_paths:
        title = set_title(chosen_paths, taken)
    else:
        title = unique_title(diy.registration_name(tables[0]), taken)

    chart = chart_for_a_set(existing)
    chart.ChartTitle = title
    against = None
    for table in tables:
        against = draw(table, chart) or against
    if against is not None:
        chart.BottomAxisTitle = against
    pvs.Render(chart)
    pvs.SetActiveSource(tables[0])
    print("%s: plotted %d file(s) as '%s' against %s."
          % (_LABEL, len(tables), title, against or "their first column"))


main()
