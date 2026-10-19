"""ParaView macro: reduce the selected slice to one row of numbers.

The right home for a handful of scalars is the SpreadSheet View, so that is what
the button opens, and it writes the row to the Output Messages window too, which
is quicker to read than a table when all you wanted was the mass flow.
"""

from paraview import servermanager
from paraview import simple as pvs

try:
    import paraview_diy_macros as diy
except ImportError:
    # Normally a loaded plugin has already put pylib on the path, and
    # paraview_diy.sh puts it there too.
    print("Surface average: load the plugins first -- Tools > Manage Plugins, "
          "or start ParaView with paraview_diy.sh.")
    raise

_LABEL = "Surface average"
_FILTER = "SurfaceAverage"
_INHERITED = ["Weighting", "Velocity", "Density"]
# A row or two needs very little of the window; the rest stays with the view
# the surface is in.
_HEIGHT_LEFT_ABOVE = 0.8


def row_as_text(average):
    """Every row, short enough to read in a message."""
    table = servermanager.Fetch(average)
    if table is None or table.GetNumberOfRows() < 1:
        return []
    lines = []
    for row in range(table.GetNumberOfRows()):
        parts = []
        for index in range(table.GetNumberOfColumns()):
            column = table.GetColumn(index)
            name = table.GetColumnName(index)
            if column.GetClassName() == "vtkStringArray":
                parts.append("%s %s" % (name, column.GetValue(row)))
            elif column.GetNumberOfComponents() == 1:
                parts.append("%s %.6g" % (name, column.GetTuple1(row)))
        lines.append(", ".join(parts))
    return lines


def show(average):
    """The row, in a SpreadSheet View across the bottom of the window."""
    view, reused = diy.view_below("SpreadSheetView", _HEIGHT_LEFT_ABOVE)
    if reused:
        diy.clear(view)
    view.FieldAssociation = "Row Data"
    pvs.Show(average, view)
    return view


def main():
    build = diy.filter_factory(_LABEL, _FILTER)
    if build is None:
        return
    source = diy.active_source(_LABEL, reject=(_FILTER,))
    if source is None:
        return

    average = build(Input=source,
                    registrationName=diy.named_after("Surface Average", source))
    template = diy.previous_filter(_FILTER)
    if template is not None:
        diy.inherit(average, template, _INHERITED)
    average.UpdatePipeline()

    lines = row_as_text(average)
    if not lines:
        diy.warn("%s: nothing to integrate; the input has no cells." % _LABEL)
        return
    show(average)
    pvs.SetActiveSource(average)
    print("%s of %s, %s:" % (_LABEL, diy.registration_name(source),
                             average.Weighting.GetData().lower()))
    for line in lines:
        print("  %s" % line)


main()
