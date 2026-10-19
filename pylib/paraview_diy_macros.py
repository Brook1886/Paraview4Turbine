"""What the toolbar macros in ``macros/`` have in common.

ParaView executes a macro as a bare script, so the macros cannot import each
other. ``paraview_diy.sh`` puts this directory on ``PYTHONPATH`` instead, which
is why starting ParaView any other way leaves the buttons complaining.

Nothing here talks to the user except through :func:`warn`. A macro that gives
up has nothing on screen to show for itself, and a plain ``print`` only reaches
an Output Messages window that does not open by itself, so the button would look
broken.
"""

import os
import re
import shutil
import subprocess

import numpy as np

import veclys_case

from paraview import print_warning, servermanager
from paraview import simple as pvs
from vtkmodules.util import numpy_support
from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet

AXIS_NAMES = ["X", "Y", "Z"]
# vtkSMViewLayoutProxy's split directions.
_VERTICAL, _HORIZONTAL = 1, 2
# The two coordinates spanning the plane each axis is normal to.
_PLANES = [(1, 2), (0, 2), (0, 1)]
# Enough points to see the shape of a passage, few enough to fetch instantly.
_SAMPLE_SIZE = 4000
# Below this fraction of the outer radius the mesh is treated as touching the
# axis, which rules that axis out.
_BORE = 0.02


def _ask_the_desktop(*arguments):
    """Put a dialog on the screen through zenity.

    ParaView's own Python has no Qt in it -- no PySide, no PyQt, not even
    tkinter -- so a macro cannot open a window of its own. zenity is the
    desktop's own dialog program and every mainstream Linux has it. Returns None
    when there is no zenity to ask with or the dialog was cancelled, which the
    caller has to tell apart from an empty answer.
    """
    if shutil.which("zenity") is None:
        return None
    try:
        answered = subprocess.run(["zenity"] + list(arguments),
                                  stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if answered.returncode != 0:            # cancelled, or closed
        return None
    return answered.stdout.decode("utf-8", "replace").rstrip("\n")


def can_ask():
    """Whether there is anything to put a dialog on the screen with."""
    return shutil.which("zenity") is not None


def ask_for_files(title, patterns=()):
    """The files the user chooses, in order, or [] if none or cancelled.

    zenity joins several files with a separator, but which one it honours varies
    by version -- some ignore ``--separator`` and use ``|`` or a newline -- so
    the answer is split by whichever separator leaves a list of files that all
    exist, rather than trusting one and getting a single bogus path.
    """
    arguments = ["--file-selection", "--multiple", "--separator", "\x1f",
                 "--title", title]
    for name, pattern in patterns:
        arguments.append("--file-filter=%s | %s" % (name, pattern))
    answer = _ask_the_desktop(*arguments)
    if not answer:
        return []
    for separator in ("\x1f", "\n", "|"):
        parts = [part for part in answer.split(separator) if part]
        if parts and all(os.path.exists(part) for part in parts):
            return parts
    return [answer]                        # one path, none of those separators


def warn(message):
    """Say something the user will see even with every panel closed."""
    print_warning(message)


def filter_factory(label, xml_name):
    """The paraview.simple constructor for one of this project's filters."""
    build = getattr(pvs, xml_name, None)
    if build is None:
        warn("%s: the filter is not loaded. Load the plugins from Tools > "
             "Manage Plugins, or start ParaView with paraview_diy.sh." % label)
    return build


def active_source(label, reject=()):
    """The selected source, or None with a reason on screen."""
    source = pvs.GetActiveSource()
    if source is None:
        warn("%s: select a source in the Pipeline Browser first." % label)
        return None
    if source.GetXMLName() in reject:
        warn("%s: %s is already one of those." % (label, source.GetXMLLabel()))
        return None
    return source


def registration_name(source):
    """What the Pipeline Browser calls a source."""
    for (name, _identifier), proxy in pvs.GetSources().items():
        if proxy == source:
            return name
    return "the input"


def named_after(label, source):
    """A registration name that says what a filter was made from, so a table of
    numbers in a SpreadSheet View is never anonymous."""
    return "%s of %s" % (label, registration_name(source))


def table_columns(source):
    """The column names of a table source, without fetching the table."""
    rows = source.GetDataInformation().DataInformation.GetRowDataInformation()
    return [rows.GetArrayInformation(index).GetName()
            for index in range(rows.GetNumberOfArrays())]


def previous_filter(xml_name):
    """The most recent filter of this kind in the pipeline, if there is one."""
    latest, newest = None, None
    for source in pvs.GetSources().values():
        if source.GetXMLName() != xml_name:
            continue
        identifier = int(source.SMProxy.GetGlobalID())
        if newest is None or identifier > newest:
            latest, newest = source, identifier
    return latest


def value(setting):
    """What a property is set to.

    ParaView hands a single number straight back but wraps everything else in a
    property object, whose str() is the repr of the value -- quotes and all --
    so reading one without asking gives "'Radius'" where Radius was meant.
    """
    return setting.GetData() if hasattr(setting, "GetData") else setting


def inherit(target, template, names):
    """Copy settings from the previous filter of the same kind."""
    for name in names:
        setattr(target, name, value(getattr(template, name)))


def views_of(kind):
    """Every view of a kind that is in the layout, leftmost or topmost first.

    Robust to there being no active view -- which happens the moment the last
    view is closed -- since pvs.GetLayout() with no argument raises then.
    """
    views = [view for view in pvs.GetViews() if view.GetXMLName() == kind]
    if not views:
        return []
    try:
        layout = pvs.GetLayout(views[0])
    except Exception:
        return views
    if layout is None:
        return views
    placed = [view for view in views if layout.GetViewLocation(view) >= 0]
    return sorted(placed, key=layout.GetViewLocation) or views


def _layout_of(view=None):
    """A layout to split, without the crash pvs.GetLayout() throws when no view
    is active -- which is the moment the last one is closed."""
    for candidate in (view, pvs.GetActiveView()):
        if candidate is not None:
            try:
                return pvs.GetLayout(candidate)
            except Exception:
                pass
    layouts = pvs.GetLayouts()
    return next(iter(layouts.values()), None) if layouts else None


def view_below(kind, fraction, reuse=True):
    """(a view of this kind across the bottom of the window, whether it existed).

    ParaView's own AssignViewToLayout drops a new view wherever it likes, which
    is usually down the right-hand side. A table or a plot of what is in the
    render view belongs under it, not beside it, and `fraction` is how much of
    the height the view above keeps. An existing view of the kind is reused
    unless told otherwise, so pressing a button twice does not carve the window
    up twice.
    """
    for view in (views_of(kind) if reuse else []):
        return view, True
    above = pvs.GetActiveView()
    layout = _layout_of(above)
    view = pvs.CreateView(kind)
    if layout is not None and above is not None and layout.GetViewLocation(above) >= 0:
        # SplitViewVertical stacks the two cells; the second one is underneath.
        cell = layout.SplitViewVertical(view=above, fraction=fraction)
        pvs.AssignViewToLayout(view=view, layout=layout, hint=cell + 1)
    else:
        pvs.AssignViewToLayout(view=view)
    return view, False


def view_in_tab(kind, tab_name):
    """(a view of this kind in its own layout tab, whether it existed).

    A separate tab, so a plot of loaded files does not crowd the render view or
    a profile's plot. The tab is reused across presses -- the caller clears it
    -- rather than a new one piling up each time.
    """
    layout = None
    for (name, _identifier), candidate in pvs.GetLayouts().items():
        if name == tab_name:
            layout = candidate
            break
    if layout is None:
        layout = pvs.CreateLayout(tab_name)
    for view in pvs.GetViews():
        if view.GetXMLName() == kind and layout.GetViewLocation(view) >= 0:
            return view, True
    view = pvs.CreateView(kind)
    pvs.AssignViewToLayout(view=view, layout=layout)
    return view, False


def views_in_tab(kind, tab_name):
    """Every view of a kind in the named layout tab, left to right.

    ``view_in_tab`` reuses the one view; this lists them all, so a caller that
    lays a plot per press side by side can find the last one to sit beside. The
    arguments are in the same order as ``view_in_tab``'s, since the two names
    differ by one letter and both take strings: swapping them would not raise,
    it would quietly look in a tab named after a view.
    """
    for (name, _identifier), layout in pvs.GetLayouts().items():
        if name != tab_name:
            continue
        placed = [(layout.GetViewLocation(view), view) for view in pvs.GetViews()
                  if view.GetXMLName() == kind and layout.GetViewLocation(view) >= 0]
        return [view for _location, view in sorted(placed)]
    return []


def view_beside(neighbour, kind):
    """Another view of this kind next to one already there, the row of them kept
    evenly wide."""
    layout = _layout_of(neighbour)
    view = pvs.CreateView(kind)
    # SplitViewHorizontal puts the two cells side by side; the second is the
    # empty one.
    cell = layout.SplitViewHorizontal(view=neighbour, fraction=0.5)
    pvs.AssignViewToLayout(view=view, layout=layout, hint=cell + 1)
    _even_out(layout, cell + 1, kind)
    return view


def _views_under(layout, cell):
    """Every view in the subtree rooted at a layout cell."""
    proxy = layout.SMProxy
    if proxy.IsSplitCell(cell):
        return (_views_under(layout, 2 * cell + 1) +
                _views_under(layout, 2 * cell + 2))
    view = proxy.GetView(cell)
    return [view] if view is not None else []


def _even_out(layout, cell, kind):
    """Make every cell in a row of side-by-side plots the same width.

    Halving the rightmost cell each time would leave 1/2, 1/4, 1/4; giving the
    topmost split of a row of n cells a fraction of 1/n, the next 1/(n-1), and
    so on down to 1/2, makes them equal.

    The walk stops at the first split whose subtree holds anything but a plot of
    this kind, so a horizontal split higher up -- the render view beside the row
    after the user rearranges the window by hand -- is left as the user set it.
    """
    chain = []
    while cell > 0:
        parent = (cell - 1) // 2
        if layout.SMProxy.GetSplitDirection(parent) != _HORIZONTAL:
            break
        under = _views_under(layout, parent)
        if not under or any(view.GetXMLName() != kind for view in under):
            break
        chain.append(parent)
        cell = parent
    for index, parent in enumerate(reversed(chain)):
        layout.SMProxy.SetSplitFraction(parent, 1.0 / (len(chain) - index + 1))


def clear(view):
    """Hide whatever a reused view is already showing."""
    for source in pvs.GetSources().values():
        representation = servermanager.GetRepresentation(source, view)
        if representation is not None:
            representation.Visibility = 0


def _points_in(node):
    if node is None:
        return []
    if node.IsA("vtkCompositeDataSet"):
        found = []
        for index in range(node.GetNumberOfBlocks()):
            found += _points_in(node.GetBlock(index))
        return found
    if node.GetNumberOfPoints() == 0:
        return []
    return [numpy_support.vtk_to_numpy(node.GetPoints().GetData())]


def sample_points(source):
    """A few thousand of a source's points, without moving the whole mesh."""
    sample = pvs.MaskPoints(Input=source, MaximumNumberofPoints=_SAMPLE_SIZE,
                            RandomSampling=1, OnRatio=1)
    try:
        sample.UpdatePipeline()
        pieces = _points_in(servermanager.Fetch(sample))
    finally:
        pvs.Delete(sample)
    return np.vstack(pieces) if pieces else None


def angular_span(u, v):
    """How wide a wedge the points fill, in degrees.

    Measured as the whole circle less the widest gap between neighbouring
    points, which is right whether or not the wedge straddles 180 degrees.
    """
    angles = np.sort(np.degrees(np.arctan2(v, u)))
    gaps = np.diff(np.concatenate([angles, angles[:1] + 360.0]))
    return 360.0 - gaps.max()


def guess_axis(points):
    """(axis, spans) for the machine axis, or (None, spans).

    A blade passage never crosses the machine axis and covers a narrow wedge
    about it, so the axis to pick is one the mesh stays clear of, and among
    those the one the mesh looks narrowest from. A passage is unambiguous; a
    single blade surface is not, and the caller is told as much.
    """
    spans = {}
    for axis, (first, second) in enumerate(_PLANES):
        u, v = points[:, first], points[:, second]
        distance = np.hypot(u, v)
        if distance.max() <= 0.0 or distance.min() <= _BORE * distance.max():
            continue                      # the mesh sits on this axis
        spans[axis] = angular_span(u, v)
    if not spans:
        return None, spans
    return min(spans, key=spans.get), spans


def describe_axis(axis, spans):
    """(what was guessed and from what, whether the guess is worth trusting)."""
    ordered = sorted(spans.items(), key=lambda item: item[1])
    detail = ", ".join("%.0f deg about %s" % (span, AXIS_NAMES[other])
                       for other, span in ordered)
    message = "the machine axis looks like %s (the mesh covers %s)" % (
        AXIS_NAMES[axis], detail)
    clear = len(ordered) < 2 or ordered[0][1] <= 0.75 * ordered[1][1]
    if not clear:
        message += ", which is not clear-cut"
    return message, clear


def machine_axis(label, source):
    """The machine axis of a source, guessed and reported. None if it cannot be."""
    try:
        points = sample_points(source)
    except Exception as error:
        warn("%s: could not sample the input: %s" % (label, error))
        return None
    if points is None or not len(points):
        warn("%s: the input has no points to guess the machine axis from." % label)
        return None
    axis, spans = guess_axis(points)
    if axis is None:
        warn("%s: the machine axis could not be guessed; set it yourself." % label)
        return None
    message, clear = describe_axis(axis, spans)
    if clear:
        print("%s: %s." % (label, message))
    else:
        warn("%s: %s; check it." % (label, message))
    return axis


# --------------------------------------------------------------- VECLYS cases
# What a run was set up with is in its input.au3x, so a button that would
# otherwise ask for a blade count can read one instead. The parsing is the
# reader's own, in veclys_case, so the plugin and the button cannot disagree.

VECLYS_READER = "VeclysReader"
# xcode's name for the two periodic patches of a passage. Matched on the first
# part of a section name -- LEFT_quad, RIGHT_tri -- rather than anywhere in it,
# so a duct's inv_left_quad is not mistaken for a periodic boundary and made to
# yield a pitch the mesh does not have.
PERIODIC = ("left", "right")


def _proxies(given):
    """Whatever a property holds, as a list of proxies."""
    if given is None:
        return []
    if hasattr(given, "SMProxy"):
        return [given]
    try:
        return [item for item in given if hasattr(item, "SMProxy")]
    except TypeError:
        return []


def upstream(source):
    """A source and everything it is built on, nearest first."""
    chain, seen, queue = [], set(), [source]
    while queue:
        node = queue.pop(0)
        key = node.SMProxy.GetGlobalIDAsString()
        if key in seen:
            continue
        seen.add(key)
        chain.append(node)
        queue.extend(_proxies(getattr(node, "Input", None)))
    return chain


def veclys_rows(label, source):
    """(case file, [Row]) of the VECLYS case a source came from, or (None, []).

    Silent when there is no case in the pipeline: a plain CGNS file opened by
    hand is an ordinary thing to be copying, and a button that reads a case when
    there is one has nothing to say when there is not.
    """
    reader = None
    for node in upstream(source):
        if node.GetXMLName() == VECLYS_READER:
            reader = node
            break
    if reader is None:
        return None, []
    case_file = value(reader.FileName)
    try:
        return case_file, veclys_case.case_rows(case_file, value(reader.CaseNumber))
    except Exception as error:
        warn("%s: %s could not be read: %s" % (label, case_file, error))
        return None, []


def _named_blocks(information, found):
    """Collect the names in a data information tree, deepest and all."""
    for index in range(information.GetNumberOfChildren()):
        name = information.GetName(index)
        if name:
            found.append(name)
        child = information.GetDataInformation(index)
        if child is not None and child.GetCompositeDataInformation() is not None:
            _named_blocks(child.GetCompositeDataInformation(), found)
    return found


def block_names(source):
    """Every block name in a source's output, without fetching the data."""
    try:
        information = source.GetDataInformation().DataInformation
        return _named_blocks(information.GetCompositeDataInformation(), [])
    except Exception:
        return []


def rows_carried(source, rows):
    """Which rows of a case a source actually holds.

    A block keeps its row's name however far down the pipeline it is, so the
    names are what say whether one row of a stage was extracted or all three are
    still there. All of them when the names settle nothing, which costs nothing:
    a per-row table is matched by name, so an entry for a row that is not there
    does not do anything.
    """
    if not rows:
        return []
    names = set(block_names(source))
    return [row for row in rows if row.name in names] or list(rows)


def _points_by_block(node, found, name=None):
    """{block name: points} for the named datasets of a fetched output."""
    if node is None:
        return found
    if node.IsA("vtkCompositeDataSet"):
        for index in range(node.GetNumberOfBlocks()):
            metadata = node.GetMetaData(index)
            child = (metadata.Get(vtkCompositeDataSet.NAME())
                     if metadata and metadata.Has(vtkCompositeDataSet.NAME()) else None)
            _points_by_block(node.GetBlock(index), found,
                             re.sub(r"^Block \d+_", "", child) if child else name)
    elif node.GetNumberOfPoints() and name:
        found.setdefault(name, []).append(
            numpy_support.vtk_to_numpy(node.GetPoints().GetData()))
    return found


def periodic_patches(names):
    """(left, right) among a mesh's boundary sections, or None.

    Paired on the word before the element type: LEFT_quad goes with RIGHT_quad,
    LEFT with RIGHT. A duct's inv_left_quad does not begin with the word, so a
    wall that merely has "left" in its name is not taken for a periodic patch
    and made to yield a pitch the mesh does not have.
    """
    left_word, right_word = PERIODIC
    for name in names:
        head, _, rest = name.partition("_")
        if head.lower() != left_word:
            continue
        for candidate in names:
            other, _, other_rest = candidate.partition("_")
            if other.lower() == right_word and other_rest == rest:
                return name, candidate
    return None


def periodic_offset(label, mesh):
    """The vector from a mesh's LEFT patch to its RIGHT one, or None.

    The two are one pitch apart by construction -- that is what makes them a
    periodic pair -- so for a cascade this is the translation between passages,
    measured off the mesh rather than guessed at. Only the two patches are read,
    which is a few hundred faces however big the volume is.
    """
    build = getattr(pvs, "CGNSLabeledBoundaries", None)
    if build is None or not os.path.exists(mesh):
        return None
    reader = build(FileName=mesh)
    try:
        reader.UpdatePipelineInformation()
        pair = periodic_patches(list(reader.Boundaries))
        if pair is None:
            return None
        reader.Volumes = []
        reader.Boundaries = list(pair)
        reader.UpdatePipeline()
        points = _points_by_block(servermanager.Fetch(reader), {})
    except Exception as error:
        warn("%s: could not measure the pitch of %s: %s"
             % (label, os.path.basename(mesh), error))
        return None
    finally:
        pvs.Delete(reader)
    if not all(name in points for name in pair):
        return None
    left, right = (np.vstack(points[name]).mean(axis=0) for name in pair)
    return right - left
