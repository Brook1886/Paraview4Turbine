"""ParaView plugin: repeat a mesh around the machine axis.

Turbomachinery meshes hold one blade passage, so a single passage is what the
CGNS file draws. This filter rotates copies of its input about the machine axis
and hands them back as one multiblock, which is how a passage becomes a sector
or a full annulus.

Everything about the copies is in one table, **Blade Rows**: a line per row,
holding its name, its blade count -- ``pitch = 360 / blades``, the number most
people actually know -- and how many copies of it to make. A block the table
names hands both down to everything under it, so a whole stage is one filter
with each row turning by its own pitch as many times as its own line says. The
toolbar button fills the table in from ``input.au3x``, so nothing there is
typed. A line with no name is what everything else uses, which is how a mesh
with no blade rows in it -- a plain CGNS file, a sphere -- gets copied at all.

Copy 0 is the input where it already is, so the copies column counts the
original. **Full Annulus** is the tick for the usual answer: every block
repeated its own blade count, closing the wheel whatever each row's count is.

Vectors are rotated with the mesh. A velocity field that was not turned with the
geometry would point the wrong way in every copy but the first.

The walk over the input, the table and the labels are shared with the Linear
Copy, which repeats a straight cascade the same way with a translation for a
step; they live in ``passage_copies``, in pylib.
"""

import os
import sys

# The toolbar macros are run by ParaView as bare strings, with no __file__ of
# their own to find their common code by. A plugin has one, so it puts pylib
# where a macro can import it from however ParaView was started -- and where
# this finds the machinery it shares with the Linear Copy.
_PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pylib")
if _PYLIB not in sys.path:
    sys.path.append(_PYLIB)

from passage_copies import (Repeat, copy_into, read_rows,  # noqa: E402
                            transformed, unit)

from paraview.util.vtkAlgorithm import (smdomain, smhint,  # noqa: E402
                                        smproperty, smproxy)
from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase  # noqa: E402
from vtkmodules.vtkCommonDataModel import (vtkDataObject,  # noqa: E402
                                           vtkMultiBlockDataSet)
from vtkmodules.vtkCommonTransforms import vtkTransform  # noqa: E402

# Values of the "Rotation Axis" enumeration; anything else means the custom
# direction below it.
_AXIS_X, _AXIS_Y, _AXIS_Z = 0, 1, 2
_AXIS_VECTORS = {_AXIS_X: (1.0, 0.0, 0.0),
                 _AXIS_Y: (0.0, 1.0, 0.0),
                 _AXIS_Z: (0.0, 0.0, 1.0)}


def axis_direction(axis, custom):
    """The unit vector the copies turn about."""
    return unit(_AXIS_VECTORS.get(axis, custom), "rotation axis")


def rotation(origin, direction, degrees):
    """Rotate by ``degrees`` about the line through ``origin``.

    PostMultiply makes the three steps apply in the order they are issued, so
    the axis is moved to the origin, turned, and put back.
    """
    transform = vtkTransform()
    transform.PostMultiply()
    transform.Translate(-origin[0], -origin[1], -origin[2])
    transform.RotateWXYZ(degrees, direction[0], direction[1], direction[2])
    transform.Translate(origin[0], origin[1], origin[2])
    return transform


def pitch_of_blades(count):
    """The pitch of a row of this many blades, in degrees.

    Negative is allowed and is how the copies are made the other way round: -48
    blades is a pitch of -7.5 degrees, the same wedge measured backwards.
    """
    if abs(count) < 1:
        raise ValueError("a blade count of %g is not a row" % count)
    return 360.0 / count


class Turn(Repeat):
    """Copies a pitch apart around the machine axis, in degrees."""

    step_name, verb = "CopyAngle", "rotate"

    def __init__(self, rows, whole, origin, direction, rotate_vectors):
        Repeat.__init__(self, rows)
        self._whole = whole           # every block repeated all the way round
        self._origin = origin
        self._direction = direction
        self._rotate_vectors = rotate_vectors

    def count(self, pitch, copies):
        """Its own blade count when the wheel is to be closed, which is the one
        number nobody should have to work out. A cascade has no such answer,
        which is why this is the rotation's and not the walk's."""
        if self._whole:
            return round(abs(360.0 / pitch))
        return Repeat.count(self, pitch, copies)

    def move(self, dataset, step):
        return transformed(dataset, rotation(self._origin, self._direction, step),
                           self._rotate_vectors)


@smproxy.filter(name="CircumferentialCopy", label="Circumferential Copy")
@smproperty.xml("""
    <Documentation short_help="Repeat a passage around the machine axis.">
      Rotates copies of the input about the machine axis, one pitch apart, so a
      single blade passage becomes a sector or a full annulus. A table of blade
      rows gives each row its own blade count and its own number of copies, so
      that a whole stage is one filter, and Full Annulus closes the wheel for
      every one of them at once.
    </Documentation>""")
@smproperty.input(name="Input", port_index=0)
@smdomain.datatype(dataTypes=["vtkDataSet", "vtkMultiBlockDataSet"],
                   composite_data_supported=1)
class CircumferentialCopy(VTKPythonAlgorithmBase):
    """Rotational copies of a blade passage, as one multiblock."""

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=1, inputType="vtkDataObject",
                                        nOutputPorts=1, outputType="vtkMultiBlockDataSet")
        self._axis = _AXIS_X
        self._axis_direction = [1.0, 0.0, 0.0]
        self._axis_origin = [0.0, 0.0, 0.0]
        # The table: (name, blades, copies) lines. ParaView fills this from the
        # property's own default, so the panel opens on one nameless line.
        self._rows = []
        self._whole = False
        self._rotate_vectors = True

    # ------------------------------------------------------------- properties
    # ParaView lists these in the Properties panel in the order they are written
    # below, so this order is the panel's.

    @smproperty.intvector(name="Axis", label="Rotation Axis", default_values=_AXIS_X)
    @smdomain.xml("""
        <EnumerationDomain name="enum">
          <Entry text="X" value="0"/>
          <Entry text="Y" value="1"/>
          <Entry text="Z" value="2"/>
          <Entry text="Custom" value="3"/>
        </EnumerationDomain>""")
    def SetAxis(self, value):
        value = int(value)
        if value != self._axis:
            self._axis = value
            self.Modified()

    @smproperty.doublevector(name="AxisDirection", label="Axis Direction",
                             default_values=[1.0, 0.0, 0.0])
    @smhint.xml("""
        <PropertyWidgetDecorator type="GenericDecorator" mode="visibility"
                                 property="Axis" value="3"/>""")
    def SetAxisDirection(self, x, y, z):
        direction = [x, y, z]
        if direction != self._axis_direction:
            self._axis_direction = direction
            self.Modified()

    @smproperty.doublevector(name="AxisOrigin", label="Axis Origin",
                             default_values=[0.0, 0.0, 0.0])
    @smdomain.xml("""
        <Documentation>A point on the machine axis. The axis of a CGNS
        turbomachinery mesh normally passes through the origin, so this is
        rarely anything but 0 0 0.</Documentation>""")
    def SetAxisOrigin(self, x, y, z):
        origin = [x, y, z]
        if origin != self._axis_origin:
            self._axis_origin = origin
            self.Modified()

    @smproperty.intvector(name="FullAnnulus", label="Full Annulus",
                          default_values=0)
    @smdomain.xml('<BooleanDomain name="bool"/>')
    @smdomain.xml("""
        <Documentation>Repeat every block all the way round, its own blade count
        of times, whatever the copies column of the table says. This is the
        usual answer and the one number nobody should have to work out: rows of
        32, 28 and 50 blades close the wheel together at 32, 28 and 50
        copies.</Documentation>""")
    def SetFullAnnulus(self, value):
        value = bool(value)
        if value != self._whole:
            self._whole = value
            self.Modified()

    @smproperty.xml("""
    <StringVectorProperty name="Rows" label="Blade Rows"
                          command="SetRow" clean_command="ClearRows"
                          repeat_command="1" number_of_elements_per_command="3"
                          element_types="2 2 2"
                          default_values=";36;4" default_values_delimiter=";"
                          number_of_elements="3">
      <Documentation>A line per blade row: its name, its blade count, and how
      many copies of it to make. A block the table names turns by
      360 / its own blade count, that many times, and hands both down to
      everything under it. This is what keeps a whole stage in one filter, and
      the toolbar button fills it in from input.au3x, so nothing here is typed.

      The line with no name is what every other block goes by, which is all a
      mesh with no blade rows in it needs. A cell left empty keeps whatever that
      line says, so a line can set the copies alone; a negative blade count
      copies the other way round. A name matching nothing in the input is
      reported rather than silently doing nothing, and so is a block the table
      leaves with no pitch, which is copied once where it
      is.</Documentation>
    </StringVectorProperty>""")
    def SetRow(self, name, blades, copies):
        """One line of the table. ParaView clears and re-sends them all."""
        self._rows.append((name, blades, copies))
        self.Modified()

    @smproperty.xml("""
        <PropertyGroup label="Blade Rows">
          <Property name="Rows"/>
        </PropertyGroup>""")
    def ClearRows(self):
        self._rows = []
        self.Modified()

    @smproperty.intvector(name="RotateVectors", label="Rotate Vectors",
                          default_values=1)
    @smdomain.xml('<BooleanDomain name="bool"/>')
    @smdomain.xml("""
        <Documentation>Turn every three-component array of floats with the
        mesh, so velocities follow the copy they belong to. Triples of any other
        type cannot hold a rotated vector and are left alone whatever this says.
        Untick to leave every array exactly as it is.</Documentation>""")
    def SetRotateVectors(self, value):
        value = bool(value)
        if value != self._rotate_vectors:
            self._rotate_vectors = value
            self.Modified()

    # ---------------------------------------------------------------- execute

    def repeat(self):
        """What the panel is asking for, as one object for the walk to use."""
        return Turn(read_rows(self._rows, pitch_of_blades), self._whole,
                    self._axis_origin,
                    axis_direction(self._axis, self._axis_direction),
                    self._rotate_vectors)

    def RequestData(self, _request, in_info, out_info):
        output = vtkMultiBlockDataSet.GetData(out_info, 0)
        output.Initialize()
        source = vtkDataObject.GetData(in_info[0], 0)
        if source is None:
            return 1
        return int(copy_into("Circumferential copy", self.repeat, source, output))
