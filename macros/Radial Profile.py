"""ParaView macro: slice the machine and plot the profile up the span.

One press does the whole job: a plane through the middle normal to the machine
axis, a Radial Profile on it, and a Line Chart View of the result against
radius. Press it on a slice you have already placed and it profiles that one
instead of cutting another.

Unlike the other buttons this one applies what it makes. A profile is a table of
a few dozen rows and the point of it is to be looked at.
"""

from paraview import simple as pvs

try:
    import paraview_diy_macros as diy
except ImportError:
    # Normally a loaded plugin has already put pylib on the path, and
    # paraview_diy.sh puts it there too.
    print("Radial profile: load the plugins first -- Tools > Manage Plugins, "
          "or start ParaView with paraview_diy.sh.")
    raise

_LABEL = "Radial profile"
_FILTER = "RadialProfile"
_SLICE = "Cut"                       # ParaView's own name for the Slice filter
_NORMALS = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
# The radius is what the plot is against and the curve length is bookkeeping, so
# neither is a property to plot.
_NOT_SERIES = ("Radius", "Length", "Area")
# The first of these that the slice carries is the one plotted when the radius
# is up the side of the chart.
_PREFERRED = ("Pressure", "Temperature", "Density", "Mach")
# Which axis the radius goes on. "x" puts every property up the side at once,
# which reads well only while they are of a similar size, since they share the
# one axis. "y" is the usual way of drawing a spanwise profile but carries one
# property at a time, the foot of the chart being in that property's own units.
_RADIUS_ON = "x"
# A profile is worth looking at, so it takes more of the window than a table.
_HEIGHT_LEFT_ABOVE = 0.65
# Every press gets a plot of its own, side by side along the bottom, so the
# stations can be compared. Set this False to draw over the last one instead.
_A_PLOT_EACH = True


def profiles_on(chart):
    """Every radial profile drawn on a chart."""
    found = []
    for source in pvs.GetSources().values():
        if source.GetXMLName() != _FILTER:
            continue
        display = pvs.servermanager.GetRepresentation(source, chart)
        if display is not None and display.Visibility:
            found.append(source)
    return found


def own_chart(profile):
    """The chart that is this profile's alone, if it has one.

    A profile shares no plot with another: that is the whole point of a plot per
    station. A chart showing this profile and some other is not its own, so it
    is given a fresh one rather than left crowding a neighbour -- which is what
    the eye icon does, since ParaView draws a source into whatever view is
    active.
    """
    for chart in diy.views_of("XYChartView"):
        drawn = profiles_on(chart)
        if profile in drawn and len(drawn) == 1:
            return chart
    return None


def evict(profile):
    """Take a profile off any chart it is on, so it can be given its own."""
    for chart in diy.views_of("XYChartView"):
        display = pvs.servermanager.GetRepresentation(profile, chart)
        if display is not None and display.Visibility:
            pvs.Hide(profile, chart)


def replot_title(profile):
    """A station title for a profile being re-plotted, from the slice it is on."""
    try:
        plane, axis = existing_slice(profile.Input)
        if plane is not None and axis is not None:
            return station(plane, axis)
    except Exception:
        pass
    return "Radial profile"


def facing_axis(normal):
    """The coordinate axis a plane most nearly faces."""
    return max(range(3), key=lambda index: abs(normal[index]))


def existing_slice(source):
    """(the slice to profile, its axis) if the selection is already one."""
    if source.GetXMLName() != _SLICE:
        return None, None
    plane = source.SliceType
    if plane.GetXMLName() != "Plane":
        # A sphere or a cylinder says nothing about where the machine axis is.
        return source, None
    return source, facing_axis(plane.Normal)


def cut_through_the_middle(source, axis):
    bounds = source.GetDataInformation().GetBounds()
    middle = [0.5 * (bounds[0] + bounds[1]), 0.5 * (bounds[2] + bounds[3]),
              0.5 * (bounds[4] + bounds[5])]
    plane = pvs.Slice(Input=source, SliceType="Plane")
    plane.SliceType.Origin = middle
    plane.SliceType.Normal = _NORMALS[axis]
    return plane


def property_to_plot(display):
    """Which property goes along the foot of the chart.

    One at a time, because with the radius up the side the other axis carries
    the property's own units and a pressure cannot share it with a temperature.
    The names are the chart's series rather than the table's columns: a vector
    column becomes Velocity_X and its fellows.
    """
    series = [name for name in list(display.SeriesLabel)[::2]
              if name not in _NOT_SERIES]
    for name in _PREFERRED:
        if name in series:
            return name
    return series[0] if series else None


def station(plane, axis):
    """Where the slice is, which is what tells one plot from the next."""
    try:
        if plane.SliceType.GetXMLName() == "Plane":
            return "%s = %.4g" % (diy.AXIS_NAMES[axis], plane.SliceType.Origin[axis])
    except AttributeError:
        pass
    return "Radial profile"


def plot(profile, title):
    """A Line Chart View, beside the last one if there is one to be beside."""
    drawn = diy.views_of("XYChartView")
    if drawn and _A_PLOT_EACH:
        chart = diy.view_beside(drawn[-1], "XYChartView")
    else:
        chart, reused = diy.view_below("XYChartView", _HEIGHT_LEFT_ABOVE,
                                       reuse=not _A_PLOT_EACH)
        if reused:
            diy.clear(chart)
    chart.ChartTitle = title
    display = pvs.Show(profile, chart)
    display.UseIndexForXAxis = 0

    if _RADIUS_ON == "x":
        display.XArrayName = "Radius"
        # Whatever ParaView would have drawn, less the two columns that are
        # bookkeeping rather than properties.
        display.SeriesVisibility = [name for name in display.SeriesVisibility
                                    if name not in _NOT_SERIES]
        chart.BottomAxisTitle = "Radius"
        chart.LeftAxisTitle = ""
        print("%s: radius along the foot, the flow properties up the side. They "
              "share the one axis, so untick in Series the ones you are not "
              "comparing." % _LABEL)
    else:
        against = property_to_plot(display)
        if against is None:
            diy.warn("%s: the profile carries no flow property to plot." % _LABEL)
            return chart
        display.XArrayName = against
        display.SeriesVisibility = ["Radius"]
        chart.BottomAxisTitle = against
        chart.LeftAxisTitle = "Radius"
        print("%s: %s along the foot, radius up the side. Change X Array Name "
              "in the Display section to plot another property."
              % (_LABEL, against))
    pvs.Render(chart)
    return chart


def main():
    build = diy.filter_factory(_LABEL, _FILTER)
    if build is None:
        return
    source = diy.active_source(_LABEL)
    if source is None:
        return

    # Pressed on a profile that already has a plot of its own: nothing to do but
    # bring it forward. Pressed on one without -- never plotted, its plot closed,
    # or crowding another's after the eye icon put it there -- give it a fresh
    # one, taking it off any plot it is wrongly sharing first.
    if source.GetXMLName() == _FILTER:
        chart = own_chart(source)
        if chart is not None:
            pvs.SetActiveView(chart)
            print("%s: %s already has a plot." % (_LABEL, source.GetXMLLabel()))
            return
        evict(source)
        profile, plane, station_title = source, None, replot_title(source)
    else:
        plane, axis = existing_slice(source)
        if plane is None:
            axis = diy.machine_axis(_LABEL, source)
            if axis is None:
                return
            plane = cut_through_the_middle(source, axis)
            pvs.Show(plane)
            print("%s: cut normal to %s through the middle; drag the plane to "
                  "move it." % (_LABEL, diy.AXIS_NAMES[axis]))
        elif axis is None:
            axis = diy.machine_axis(_LABEL, source)
            if axis is None:
                return
        profile = build(Input=plane, RotationAxis=axis,
                        registrationName=diy.named_after("Radial Profile", plane))
        profile.UpdatePipeline()
        station_title = station(plane, axis)
        if not diy.table_columns(profile):
            diy.warn("%s: the slice produced no profile; check the Rotation Axis "
                     "and the plane." % _LABEL)
            return

    plot(profile, station_title)
    pvs.SetActiveSource(profile)
    print("%s: %d bands from the slice." % (_LABEL, profile.GetDataInformation()
                                            .GetNumberOfRows()))


main()
