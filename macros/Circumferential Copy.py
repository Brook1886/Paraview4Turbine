"""ParaView macro: repeat a passage, round the axis or along the cascade.

ParaView gives a Python plugin no way to add its own toolbar button, but every
file in the user's Macros directory becomes one. `paraview_diy.sh` links this
file there, so the filter is one click away instead of a hunt through
Filters > Alphabetical.

The macro creates the filter and leaves it unapplied, the way picking a filter
from the menu does, so the numbers can be checked before a large mesh is
repeated thirty-six times. What it fills in depends on where the mesh came from.

Opened as a VECLYS case, `input.au3x` already says how the passage repeats and
there is nothing left to guess. `cosystem` says whether there is an axis to turn
about at all, so a cartesian case is given a **Linear Copy** -- turning a
straight cascade about an axis it does not have is not a thing to offer -- and
an annular one a **Circumferential Copy**. `assembly-sectors` is the blade count
of an annular row and `pitch` the passage spacing of a cartesian one. **Full
Annulus** is a Circumferential Copy's alone: a cascade has no wheel to close, so
the Linear Copy has no such tick to offer.

However many blade rows a case holds it is one filter: they go into the **Blade
Rows** table a line apiece -- name, blade count, copies -- so the whole stage
stays a single entry in the Pipeline Browser with each row turning by its own
pitch as often as its own line says. The names come out of the case file, so
they are never something to type, and **Full Annulus** closes the wheel for all
of them at once.

Opened as a plain CGNS file there is no case to read, and the macro does what it
always did: guesses the machine axis from a sample of the input's points and
copies the settings of the last copy in the pipeline, so a blade count is typed
once per session rather than once per boundary -- into the table's nameless
line, which is what a mesh with no blade rows in it goes by.
"""

import os

from paraview import simple as pvs

try:
    import paraview_diy_macros as diy
except ImportError:
    # Normally a loaded plugin has already put pylib on the path, and
    # paraview_diy.sh puts it there too.
    print("Circumferential copy: load the plugins first -- Tools > Manage "
          "Plugins, or start ParaView with paraview_diy.sh.")
    raise

_LABEL = "Circumferential copy"
_TURN, _SLIDE = "CircumferentialCopy", "LinearCopy"

# Copied onto a new filter from the previous one of its kind, so what was typed
# once is not typed again for the next boundary. Anything the case file settles
# is then set from the case, which is the more recent word on it.
_TURN_SETTINGS = ["RotationAxis", "AxisDirection", "AxisOrigin", "FullAnnulus",
                  "BladeRows", "RotateVectors"]
_SLIDE_SETTINGS = ["TranslationDirection", "BladeRows"]
# How many copies a case of one row starts with, and how much of the machine the
# rows of a stage start covering between them: enough of it to give the
# widest-pitched row those same four copies. Every row's line then gets as many
# as its own pitch needs to reach the same place, so the stage lines up -- and
# pressing Apply does not build a whole wheel by accident. Full Annulus is the
# tick for that, and is left for the user to make.
_STARTING_COPIES = 4


def _copies(row, extent):
    """How many copies of a row it takes to cover an extent at its own pitch."""
    return max(1, int(round(abs(extent / row.pitch))))


def _blades(row):
    """A row's middle column where the copies are turned: its blade count.

    A whole number for the ordinary mesh of one passage, and a fraction only for
    a mesh that holds several -- ``grid-sectors`` above one, which no run has
    used yet but whose pitch rounding would otherwise throw away.
    """
    blades = 360.0 / row.pitch
    return ("%d" % round(blades) if abs(blades - round(blades)) < 1e-9
            else "%.10g" % blades)


def _spacing(row):
    """A row's middle column where they are slid: the distance itself."""
    return "%.10g" % row.pitch


def _rows(rows, extent, measure):
    """The Blade Rows table, flat, the way the property wants it.

    Three strings a line -- the row's name, what its copies are apart, how many
    of them -- so the panel shows a line per blade row with the name already
    filled in and only the numbers worth touching.
    """
    lines = []
    for row in rows:
        lines += [row.name, measure(row), "%d" % _copies(row, extent)]
    return lines


def fit_turn(copy, rows):
    """Set a Circumferential Copy to what the case says about its rows."""
    pitched = [row for row in rows if row.pitch]
    if not pitched:
        print("%s: put the blade and copy counts on the table's nameless line, "
              "then click Apply." % _LABEL)
        return
    extent = _STARTING_COPIES * max(row.pitch for row in pitched)
    copy.BladeRows = _rows(pitched, extent, _blades)
    told = "; ".join("%s turns %.6g deg, %d times"
                     % (row.name, row.pitch, _copies(row, extent))
                     for row in pitched)
    if len(pitched) == 1:
        print("%s: %s, out of the case file. Tick Full Annulus for the whole "
              "wheel." % (_LABEL, told))
    else:
        print("%s: %d rows in one filter, a line each -- %s -- so each covers "
              "the same %.4g degrees. Tick Full Annulus for the whole wheel."
              % (_LABEL, len(pitched), told, extent))


def fit_slide(copy, rows):
    """Set a Linear Copy to what the case says, and point it along the cascade.

    There is always at least one row: a Linear Copy is only made when the case
    has a cartesian row to make it for.
    """
    # The direction is measured off the mesh, because input.au3x gives the
    # distance between two passages and not which way they lie. The two periodic
    # patches are one pitch apart by construction, so the vector between them is
    # both. One direction is enough: a case of several rows is one cascade.
    offset = diy.periodic_offset(_LABEL, rows[0].mesh)
    if offset is not None:
        copy.TranslationDirection = [float(part) for part in offset]
        print("%s: the periodic patches of %s lie (%.6g, %.6g, %.6g) apart, "
              "which is the way the copies go."
              % ((_LABEL, rows[0].name) + tuple(offset)))
    else:
        diy.warn("%s: %s has no LEFT/RIGHT periodic pair to take the direction "
                 "from; set Translation Direction yourself."
                 % (_LABEL, os.path.basename(rows[0].mesh)))
    measured = None if offset is None else sum(part * part for part in offset) ** 0.5

    pitched = [row for row in rows if row.pitch]
    if not pitched:
        if measured:
            # No pitch in the file, so the mesh's own is offered on the
            # nameless line for the whole input to go by.
            copy.BladeRows = ["", "%.10g" % measured, "%d" % _STARTING_COPIES]
            diy.warn("%s: the case file gives no pitch for %s, so the %.10g "
                     "between its periodic patches was measured instead; check "
                     "it." % (_LABEL, rows[0].name, measured))
        else:
            diy.warn("%s: neither the case file nor the mesh says how far apart "
                     "the passages are; put the pitch on the table's nameless "
                     "line yourself." % _LABEL)
        return

    widest = max((row.pitch for row in pitched), key=abs)
    extent = _STARTING_COPIES * abs(widest)
    copy.BladeRows = _rows(pitched, extent, _spacing)
    told = "; ".join("%s repeats every %.10g, %d times"
                     % (row.name, row.pitch, _copies(row, extent))
                     for row in pitched)
    if len(pitched) == 1:
        print("%s: %s, out of the case file." % (_LABEL, told))
    else:
        print("%s: %d rows in one filter, a line each -- %s -- so each covers "
              "the same %.4g." % (_LABEL, len(pitched), told, extent))
    if measured and abs(measured - abs(widest)) > 0.01 * abs(widest):
        diy.warn("%s: the case file says the pitch is %.10g, but the periodic "
                 "patches are %.10g apart. The file is what was used; check "
                 "which is right." % (_LABEL, widest, measured))


def main():
    source = diy.active_source(_LABEL, reject=(_TURN, _SLIDE))
    if source is None:
        return

    case, rows = diy.veclys_rows(_LABEL, source)
    here = diy.rows_carried(source, rows)
    if here:
        print("%s: %s" % (_LABEL, case))
        for row in here:
            print("  %s" % row.describe())
    cartesian = [row for row in here if row.cartesian]
    annular = [row for row in here if row.annular]
    if cartesian and annular:
        # No run has ever mixed them, and a machine is one or the other. If one
        # ever does, the cartesian rows are left out of the table rather than
        # turned about an axis they have not got: the filter then leaves them
        # where they are and says so.
        diy.warn("%s: %s has annular and cartesian rows at once (%s). The "
                 "cartesian ones have no axis to turn about, so they are left "
                 "out of the table and stay where they are."
                 % (_LABEL, os.path.basename(case),
                    ", ".join(row.name for row in cartesian)))
    if cartesian and not annular:
        kind, settings, fit, chosen = _SLIDE, _SLIDE_SETTINGS, fit_slide, cartesian
    else:
        kind, settings, fit, chosen = _TURN, _TURN_SETTINGS, fit_turn, annular

    build = diy.filter_factory(_LABEL, kind)
    if build is None:
        return
    template = diy.previous_filter(kind)
    axis = None
    if kind == _TURN and template is None:
        axis = diy.machine_axis(_LABEL, source)

    copy = build(Input=source)
    if template is not None:
        diy.inherit(copy, template, settings)
        print("%s: reusing the settings of the previous copy." % _LABEL)
    elif axis is not None:
        copy.RotationAxis = axis
    fit(copy, chosen)
    if kind == _SLIDE:
        print("%s: made a Linear Copy instead. The case is cartesian, and a "
              "straight cascade has no axis to turn about." % _LABEL)
    pvs.SetActiveSource(copy)


main()
