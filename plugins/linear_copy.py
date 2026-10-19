"""ParaView plugin: repeat a passage along a straight cascade.

A linear cascade is the flat cousin of a blade row: the mesh holds one passage
and the machine is that passage translated over and over, with no axis anywhere.
VECLYS writes one whenever ``input.au3x`` says ``cosystem=cartesian``, and its
``pitch`` is the distance from one passage to the next.

This filter is the Circumferential Copy with the rotation taken out. It keeps
the same tree and the same labels -- Copy 0 is the input where it already is,
and each copy carries a ``CopyId`` and a ``CopyOffset`` -- because the two share
everything but the step, in ``passage_copies``. So a case of several rows stays
one filter here too, with a **Blade Rows** table giving each its own pitch and
its own count, and a line with no name on it covering everything the rest do
not.

There is no Full Annulus here: a cascade is a straight line of passages with no
wheel to close, so how many of them to draw is the only answer there is.

Nothing here touches the arrays. Sliding a mesh sideways leaves every velocity
pointing where it did, so there is no Rotate Vectors to think about: the
question a rotation has to answer does not come up.
"""

import os
import sys

# The toolbar macros are run by ParaView as bare strings, with no __file__ of
# their own to find their common code by. A plugin has one, so it puts pylib
# where a macro can import it from however ParaView was started -- and where
# this finds the machinery it shares with the Circumferential Copy.
_PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pylib")
if _PYLIB not in sys.path:
    sys.path.append(_PYLIB)

from passage_copies import (Repeat, copy_into, read_rows,  # noqa: E402
                            transformed, unit)

from paraview.util.vtkAlgorithm import (smdomain, smproperty,  # noqa: E402
                                        smproxy)
from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase  # noqa: E402
from vtkmodules.vtkCommonDataModel import (vtkDataObject,  # noqa: E402
                                           vtkMultiBlockDataSet)
from vtkmodules.vtkCommonTransforms import vtkTransform  # noqa: E402


def translation(direction, distance):
    """Slide by ``distance`` along a unit direction."""
    transform = vtkTransform()
    transform.Translate(direction[0] * distance, direction[1] * distance,
                        direction[2] * distance)
    return transform


def pitch_of_distance(value):
    """A cell of the table's pitch column, which for a cascade is the pitch."""
    if value == 0.0:
        raise ValueError("a pitch of zero puts every copy in the same place")
    return value


class Slide(Repeat):
    """Copies a pitch apart along the cascade, in the mesh's own units.

    There is no Full Annulus to answer to: a cascade is a straight line of
    passages with no wheel to close, so the count from the table is the only
    answer there is and the base class's is right as it stands.
    """

    step_name, verb = "CopyOffset", "move"

    def __init__(self, rows, direction):
        Repeat.__init__(self, rows)
        self._direction = direction

    def move(self, dataset, step):
        # Vectors are left alone: a translation would not change one anyway, and
        # asking vtkTransformFilter to carry them would only risk the arrays it
        # cannot hold a vector in.
        return transformed(dataset, translation(self._direction, step), False)


@smproxy.filter(name="LinearCopy", label="Linear Copy")
@smproperty.xml("""
    <Documentation short_help="Repeat a passage along a straight cascade.">
      Translates copies of the input one pitch apart, so a single passage of a
      linear cascade becomes a row of them. This is the filter for a case whose
      input.au3x says cosystem=cartesian: such a mesh has no axis, and turning
      it about one would be meaningless.
    </Documentation>""")
@smproperty.input(name="Input", port_index=0)
@smdomain.datatype(dataTypes=["vtkDataSet", "vtkMultiBlockDataSet"],
                   composite_data_supported=1)
class LinearCopy(VTKPythonAlgorithmBase):
    """Translated copies of a cascade passage, as one multiblock."""

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=1, inputType="vtkDataObject",
                                        nOutputPorts=1, outputType="vtkMultiBlockDataSet")
        self._direction = [0.0, 1.0, 0.0]
        # The table: (name, pitch, copies) lines. ParaView fills this from the
        # property's own default, so the panel opens on one nameless line.
        self._rows = []

    # ------------------------------------------------------------- properties
    # ParaView lists these in the Properties panel in the order they are written
    # below, so this order is the panel's.

    @smproperty.doublevector(name="Direction", label="Translation Direction",
                             default_values=[0.0, 1.0, 0.0])
    @smdomain.xml("""
        <Documentation>Which way the copies go. Only the direction is taken
        from this -- the distance is the pitch in the table below -- so 0 1 0
        and 0 2 0 mean the same thing. The vector from one periodic patch of the
        mesh to its partner is the one to use, and the toolbar button measures
        it.</Documentation>""")
    def SetDirection(self, x, y, z):
        direction = [x, y, z]
        if direction != self._direction:
            self._direction = direction
            self.Modified()

    @smproperty.xml("""
    <StringVectorProperty name="Rows" label="Blade Rows"
                          command="SetRow" clean_command="ClearRows"
                          repeat_command="1" number_of_elements_per_command="3"
                          element_types="2 2 2"
                          default_values=";1;4" default_values_delimiter=";"
                          number_of_elements="3">
      <Documentation>A line per row of the case: its name, the distance from one
      of its passages to the next, and how many copies of it to make. A block
      the table names slides by its own pitch, that many times, and hands both
      down to everything under it. The toolbar button fills it in from
      input.au3x, so nothing here is typed.

      The line with no name is what every other block goes by, which is all a
      mesh of one passage needs. A cell left empty keeps whatever that line
      says; a negative pitch copies the other way round.</Documentation>
    </StringVectorProperty>""")
    def SetRow(self, name, pitch, copies):
        """One line of the table. ParaView clears and re-sends them all."""
        self._rows.append((name, pitch, copies))
        self.Modified()

    @smproperty.xml("""
        <PropertyGroup label="Blade Rows">
          <Property name="Rows"/>
        </PropertyGroup>""")
    def ClearRows(self):
        self._rows = []
        self.Modified()

    # ---------------------------------------------------------------- execute

    def repeat(self):
        """What the panel is asking for, as one object for the walk to use."""
        return Slide(read_rows(self._rows, pitch_of_distance, "pitch"),
                     unit(self._direction, "translation direction"))

    def RequestData(self, _request, in_info, out_info):
        output = vtkMultiBlockDataSet.GetData(out_info, 0)
        output.Initialize()
        source = vtkDataObject.GetData(in_info[0], 0)
        if source is None:
            return 1
        return int(copy_into("Linear copy", self.repeat, source, output))
