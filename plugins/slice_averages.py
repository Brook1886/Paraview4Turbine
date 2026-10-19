"""ParaView plugin: the two averages a slice is usually taken for.

**Radial Profile** cuts a slice into radial bands and averages each one along
the curve where the band's radius meets the slice, weighted by edge length. That
is the spanwise profile a turbomachine is judged by -- total pressure or
temperature from hub to casing.

**Surface Average** reduces a whole slice to one row: the integral of each
property, its area average, or its mass-flow average, which is the one that
conserves what it should when a stagnation quantity crosses a plane.

Both work through vtkIntegrateAttributes, so a point array is integrated with
the same linear interpolation ParaView's own **Integrate Variables** uses, and
both hand back a vtkTable, which the SpreadSheet View shows as numbers and the
Line Chart View plots.
"""

import os
import sys

import numpy as np

from paraview.util.vtkAlgorithm import smdomain, smhint, smproperty, smproxy
from vtkmodules.util import numpy_support
from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase
from vtkmodules.vtkCommonCore import vtkStringArray
from vtkmodules.vtkCommonDataModel import (vtkCompositeDataSet, vtkDataObject,
                                           vtkMultiBlockDataSet, vtkTable)
from vtkmodules.vtkFiltersCore import (vtkCellDataToPointData, vtkContourFilter,
                                       vtkPolyDataNormals)
from vtkmodules.vtkFiltersGeometry import vtkGeometryFilter
from vtkmodules.vtkFiltersParallel import vtkIntegrateAttributes

# The toolbar macros are run by ParaView as bare strings, with no __file__ of
# their own to find their common code by. A plugin has one, so it puts pylib
# where a macro can import it from however ParaView was started.
_PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pylib")
if _PYLIB not in sys.path:
    sys.path.append(_PYLIB)

# Values of the "Rotation Axis" enumeration; anything else means the direction
# typed in below it.
_AXIS_VECTORS = {0: (1.0, 0.0, 0.0), 1: (0.0, 1.0, 0.0), 2: (0.0, 0.0, 1.0)}

# Values of the "Spacing" enumeration.
_UNIFORM, _CLUSTERED = 0, 1

# Values of the "Weighting" enumeration.
_INTEGRAL, _AREA, _MASS_FLOW = 0, 1, 2

# Values of the "Rows" enumeration.
_PER_BLOCK, _TOGETHER = 0, 1

# vtkIntegrateAttributes reports the size of what it integrated under one of
# these, according to how many dimensions the cells have.
_MEASURES = ("Length", "Area", "Volume")

_POINTS = vtkDataObject.FIELD_ASSOCIATION_POINTS
_CELLS = vtkDataObject.FIELD_ASSOCIATION_CELLS

_RADIUS = "RadialProfileRadius"
_WEIGHT = "MassFlux"


def axis_direction(axis, custom):
    """The unit vector radius is measured from."""
    direction = np.asarray(_AXIS_VECTORS.get(axis, custom), dtype=float)
    length = float(np.linalg.norm(direction))
    if length == 0.0:
        raise ValueError("The rotation axis is a zero-length vector.")
    return direction / length


def radius(points, origin, direction):
    """Distance from the axis, for every point."""
    offset = points - np.asarray(origin, dtype=float)
    along = np.outer(offset.dot(direction), direction)
    return np.linalg.norm(offset - along, axis=1)


def band_radii(lowest, highest, count, clustered):
    """Where to cut the span, from hub to casing.

    Uniform steps put as many samples in the middle of the span, where the flow
    hardly changes, as in the endwall boundary layers, where it changes fastest.
    Cosine spacing -- the interior points of a Chebyshev distribution -- puts
    them where the gradients are: over fifty bands the first sits a two-thousandth
    of the span from the wall rather than a hundredth of it, and the middle of
    the span is sampled half as often instead.

    Uniform gives the midpoints of `count` equal bands, which is what a band is
    if the word is taken literally.
    """
    steps = (np.arange(count) + 0.5) / float(count)
    if clustered:
        steps = 0.5 * (1.0 - np.cos(np.pi * steps))
    return lowest + steps * (highest - lowest)


def _children(node):
    """The blocks of a composite dataset, whichever flavour of composite it is."""
    if node.IsA("vtkMultiBlockDataSet"):
        return [node.GetBlock(index) for index in range(node.GetNumberOfBlocks())]
    if node.IsA("vtkPartitionedDataSetCollection"):
        return [node.GetPartitionedDataSet(index)
                for index in range(node.GetNumberOfPartitionedDataSets())]
    if node.IsA("vtkPartitionedDataSet") or node.IsA("vtkMultiPieceDataSet"):
        return [node.GetPartition(index)
                for index in range(node.GetNumberOfPartitions())]
    return None


def named_leaves(node, path=""):
    """(block path, dataset) for every dataset in the input, composite or not.

    The path is what the Multi-block Inspector shows, so a row of the output can
    say which surface it came from.
    """
    if node is None:
        return []
    children = _children(node) if node.IsA("vtkCompositeDataSet") else None
    if children is None:
        return [(path, node)] if node.IsA("vtkDataSet") else []
    found = []
    for index, child in enumerate(children):
        name = str(index)
        if node.HasMetaData(index):
            metadata = node.GetMetaData(index)
            if metadata.Has(vtkCompositeDataSet.NAME()):
                name = metadata.Get(vtkCompositeDataSet.NAME())
        found += named_leaves(child, "%s/%s" % (path, name) if path else name)
    return found


def leaves(node):
    """Every dataset in the input, composite or not."""
    return [dataset for _path, dataset in named_leaves(node)]


def _bundle(pieces):
    block = vtkMultiBlockDataSet()
    block.SetNumberOfBlocks(len(pieces))
    for index, piece in enumerate(pieces):
        block.SetBlock(index, piece)
    return block


def integrate(pieces):
    """(the integral of every array, the size of what was integrated, its name).

    The integrals come back as a one-cell dataset, point arrays and cell arrays
    alike, so dividing by the size is the average either way. The size is a
    length, an area or a volume according to what the cells are, and it is
    reported under that name so a table never calls a volume an area.
    """
    integrator = vtkIntegrateAttributes()
    integrator.SetInputData(_bundle(pieces))
    integrator.Update()
    result = integrator.GetOutput()

    measure, measured = 0.0, _MEASURES[1]
    for name in _MEASURES:
        array = result.GetCellData().GetArray(name)
        if array is not None:
            measure, measured = float(array.GetTuple1(0)), name
            break

    integrals = []
    for attributes in (result.GetPointData(), result.GetCellData()):
        for index in range(attributes.GetNumberOfArrays()):
            array = attributes.GetArray(index)
            if array is None or array.GetName() in _MEASURES:
                continue
            integrals.append((array.GetName(),
                              numpy_support.vtk_to_numpy(array)[0].copy()))
    return integrals, measure, measured


def _column(table, name, values):
    values = np.atleast_1d(np.asarray(values, dtype=float))
    if values.ndim == 1:
        values = values.reshape(-1, 1)
    array = numpy_support.numpy_to_vtk(np.ascontiguousarray(values), deep=1)
    array.SetName(name)
    table.AddColumn(array)


def _text_column(table, name, values):
    column = vtkStringArray()
    column.SetName(name)
    for value in values:
        column.InsertNextValue(value)
    table.AddColumn(column)


def middle_of(datasets):
    """The centre of what was integrated, so one slice can be told from another."""
    bounds = np.array([dataset.GetBounds() for dataset in datasets])
    low = bounds[:, ::2].min(axis=0)
    high = bounds[:, 1::2].max(axis=0)
    return 0.5 * (low + high)


def _rows_to_table(table, names, rows):
    """Columns from a list of rows, each row a name -> value mapping."""
    for name in names:
        values = [row[name] for row in rows]
        if isinstance(values[0], str):
            _text_column(table, name, values)
            continue
        stacked = (np.vstack(values) if np.ndim(values[0]) else np.asarray(values))
        _column(table, name, stacked)


def _as_polydata(dataset):
    if dataset.IsA("vtkPolyData"):
        return dataset
    surface = vtkGeometryFilter()
    surface.SetInputData(dataset)
    surface.Update()
    piece = surface.GetOutput().NewInstance()
    piece.ShallowCopy(surface.GetOutput())
    return piece


def _with_array(dataset, name, values, association=_POINTS):
    """A shallow copy carrying one more array, so the input is left alone."""
    piece = dataset.NewInstance()
    piece.ShallowCopy(dataset)
    array = numpy_support.numpy_to_vtk(np.ascontiguousarray(values), deep=1)
    array.SetName(name)
    attributes = (piece.GetPointData() if association == _POINTS
                  else piece.GetCellData())
    attributes.AddArray(array)
    return piece


_AXIS_XML = """
    <EnumerationDomain name="enum">
      <Entry text="X" value="0"/>
      <Entry text="Y" value="1"/>
      <Entry text="Z" value="2"/>
      <Entry text="Custom" value="3"/>
    </EnumerationDomain>"""

_CUSTOM_AXIS_XML = """
    <PropertyWidgetDecorator type="GenericDecorator" mode="visibility"
                             property="Axis" value="3"/>"""


@smproxy.filter(name="RadialProfile", label="Radial Profile")
@smproperty.xml("""
    <Documentation short_help="Spanwise profiles, averaged round the annulus.">
      Splits the input into radial bands and averages the flow properties along
      the curve where each band's radius crosses it, weighted by edge length.
      The bands are clustered into the hub and casing boundary layers unless
      told otherwise. The output is a table of one row per band, ready for the
      Line Chart View.
    </Documentation>""")
@smproperty.input(name="Input", port_index=0)
@smdomain.datatype(dataTypes=["vtkDataSet", "vtkMultiBlockDataSet"],
                   composite_data_supported=1)
class RadialProfile(VTKPythonAlgorithmBase):
    """Hub-to-casing profiles of a slice, as a table."""

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=1, inputType="vtkDataObject",
                                        nOutputPorts=1, outputType="vtkTable")
        self._axis = 0
        self._axis_direction = [1.0, 0.0, 0.0]
        self._axis_origin = [0.0, 0.0, 0.0]
        self._bands = 50
        self._spacing = _CLUSTERED
        self._automatic = True
        self._range = [0.0, 1.0]

    # ------------------------------------------------------------- properties

    @smproperty.intvector(name="Axis", label="Rotation Axis", default_values=0)
    @smdomain.xml(_AXIS_XML)
    def SetAxis(self, value):
        value = int(value)
        if value != self._axis:
            self._axis = value
            self.Modified()

    @smproperty.doublevector(name="AxisDirection", label="Axis Direction",
                             default_values=[1.0, 0.0, 0.0])
    @smhint.xml(_CUSTOM_AXIS_XML)
    def SetAxisDirection(self, x, y, z):
        direction = [x, y, z]
        if direction != self._axis_direction:
            self._axis_direction = direction
            self.Modified()

    @smproperty.doublevector(name="AxisOrigin", label="Axis Origin",
                             default_values=[0.0, 0.0, 0.0])
    def SetAxisOrigin(self, x, y, z):
        origin = [x, y, z]
        if origin != self._axis_origin:
            self._axis_origin = origin
            self.Modified()

    @smproperty.intvector(name="NumberOfBands", label="Number Of Bands",
                          default_values=50)
    @smdomain.intrange(min=1)
    @smdomain.xml("""
        <Documentation>How many bands the radius is divided into. Each row of
        the output is the curve at the middle of one band.</Documentation>""")
    def SetBands(self, value):
        value = int(value)
        if value != self._bands:
            self._bands = value
            self.Modified()

    @smproperty.intvector(name="Spacing", label="Spacing",
                          default_values=_CLUSTERED)
    @smdomain.xml("""
        <EnumerationDomain name="enum">
          <Entry text="Clustered at the walls" value="1"/>
          <Entry text="Uniform" value="0"/>
        </EnumerationDomain>""")
    @smdomain.xml("""
        <Documentation>Where along the span to take the profile. Clustered puts
        the samples where a turbomachine's gradients are, in the hub and casing
        boundary layers, by cosine spacing; uniform spreads them evenly, which is
        what to use if the rows are to be averaged back into one number, since
        then they carry equal weight.</Documentation>""")
    def SetClustering(self, value):
        value = int(value)
        if value != self._spacing:
            self._spacing = value
            self.Modified()

    @smproperty.intvector(name="AutomaticRadiusRange",
                          label="Automatic Radius Range", default_values=1)
    @smdomain.xml('<BooleanDomain name="bool"/>')
    @smdomain.xml("""
        <Documentation>Take the range from the hub and casing radius of the
        input. Untick to profile a chosen part of the span, or to hold the range
        still while comparing slices.</Documentation>""")
    def SetFindRadiusRange(self, value):
        value = bool(value)
        if value != self._automatic:
            self._automatic = value
            self.Modified()

    @smproperty.doublevector(name="RadiusRange", label="Radius Range",
                             default_values=[0.0, 1.0])
    @smhint.xml("""
        <PropertyWidgetDecorator type="GenericDecorator" mode="visibility"
                                 property="AutomaticRadiusRange" value="0"/>""")
    def SetRadiusRange(self, low, high):
        limits = [low, high]
        if limits != self._range:
            self._range = limits
            self.Modified()

    def _radial(self, pieces, direction):
        """Every input dataset with its radius attached, and the radius range."""
        prepared, lowest, highest = [], None, None
        for piece in pieces:
            if piece.GetNumberOfPoints() == 0:
                continue
            points = numpy_support.vtk_to_numpy(piece.GetPoints().GetData())
            distances = radius(points, self._axis_origin, direction)
            prepared.append(_with_array(piece, _RADIUS, distances))
            low, high = float(distances.min()), float(distances.max())
            lowest = low if lowest is None else min(lowest, low)
            highest = high if highest is None else max(highest, high)
        return prepared, lowest, highest

    @staticmethod
    def _curve(dataset, value):
        contour = vtkContourFilter()
        contour.SetInputData(dataset)
        contour.SetInputArrayToProcess(0, 0, 0, _POINTS, _RADIUS)
        contour.SetNumberOfContours(1)
        contour.SetValue(0, value)
        contour.Update()
        return contour.GetOutput() if contour.GetOutput().GetNumberOfCells() else None

    def RequestData(self, _request, in_info, out_info):
        output = vtkTable.GetData(out_info, 0)
        source = vtkDataObject.GetData(in_info[0], 0)
        if source is None:
            return 1
        try:
            direction = axis_direction(self._axis, self._axis_direction)
            prepared, lowest, highest = self._radial(leaves(source), direction)
            if not prepared:
                print("Radial profile: the input has no points.")
                return 1
            if not self._automatic:
                lowest, highest = self._range
            if not highest > lowest:
                print("Radial profile: the radius range %g to %g is empty." %
                      (lowest, highest))
                return 1

            radii = band_radii(lowest, highest, self._bands,
                               self._spacing == _CLUSTERED)
            names, rows, empty = ["Radius"], [], 0
            for middle in radii:
                curves = [curve for curve in
                          (self._curve(piece, middle) for piece in prepared)
                          if curve is not None]
                if not curves:
                    empty += 1
                    continue
                integrals, length, measured = integrate(curves)
                if length <= 0.0:
                    empty += 1
                    continue
                if measured not in names:
                    names.insert(1, measured)
                row = {"Radius": float(middle), measured: length}
                for name, total in integrals:
                    if name == _RADIUS:
                        continue
                    if name not in names:
                        names.append(name)
                    row[name] = total / length
                rows.append(row)

            if not rows:
                print("Radial profile: no band crossed the input.")
                return 1
            missing = [name for name in names if any(name not in row for row in rows)]
            if missing:
                print("Radial profile: %s is missing from some bands and was "
                      "left out." % ", ".join(missing))
                names = [name for name in names if name not in missing]
            _rows_to_table(output, names, rows)
            if empty:
                print("Radial profile: %d of %d bands did not cross the input." %
                      (empty, self._bands))
        except Exception as error:
            print("Radial profile: %s" % error)
            return 0
        return 1


@smproxy.filter(name="SurfaceAverage", label="Surface Average")
@smproperty.xml("""
    <Documentation short_help="Integrate a slice down to one row of numbers.">
      Integrates every flow property over the input and reports the integral,
      the area average, or the mass-flow average, along with the area itself.
      The output is a one-row table for the SpreadSheet View.
    </Documentation>""")
@smproperty.input(name="Input", port_index=0)
@smdomain.datatype(dataTypes=["vtkDataSet", "vtkMultiBlockDataSet"],
                   composite_data_supported=1)
class SurfaceAverage(VTKPythonAlgorithmBase):
    """One row: the area of a slice and what it averages."""

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=1, inputType="vtkDataObject",
                                        nOutputPorts=1, outputType="vtkTable")
        self._weighting = _AREA
        self._rows = _PER_BLOCK
        self._velocity = "Velocity"
        self._density = "Density"

    # ------------------------------------------------------------- properties

    @smproperty.intvector(name="Weighting", label="Weighting", default_values=_AREA)
    @smdomain.xml("""
        <EnumerationDomain name="enum">
          <Entry text="Integral" value="0"/>
          <Entry text="Area Average" value="1"/>
          <Entry text="Mass Flow Average" value="2"/>
        </EnumerationDomain>""")
    @smdomain.xml("""
        <Documentation>Integral is the plain integral over the surface, area
        average divides it by the area, or by the volume of a solid, and
        mass-flow average weights each point
        by the mass crossing the surface there, which is what a stagnation
        quantity should be averaged with.</Documentation>""")
    def SetAveraging(self, value):
        value = int(value)
        if value != self._weighting:
            self._weighting = value
            self.Modified()

    @smproperty.intvector(name="Rows", label="Rows", default_values=_PER_BLOCK)
    @smdomain.xml("""
        <EnumerationDomain name="enum">
          <Entry text="One Per Block" value="0"/>
          <Entry text="Everything Together" value="1"/>
        </EnumerationDomain>""")
    @smdomain.xml("""
        <Documentation>A multiblock input -- a slice of several zones, or a set
        of boundary patches -- gives one row per block by default, each named in
        the Surface column, so it is never a mystery which surface a number
        belongs to. Everything Together adds the blocks up into a single
        row.</Documentation>""")
    def SetBlockRows(self, value):
        value = int(value)
        if value != self._rows:
            self._rows = value
            self.Modified()

    @smproperty.stringvector(name="Density", label="Density", default_values="Density")
    @smdomain.xml("""
        <ArrayListDomain name="array_list" attribute_type="Scalars">
          <RequiredProperties><Property name="Input" function="Input"/></RequiredProperties>
        </ArrayListDomain>""")
    @smhint.xml("""
        <PropertyWidgetDecorator type="GenericDecorator" mode="visibility"
                                 property="Weighting" value="2"/>""")
    def SetDensity(self, name):
        if name != self._density:
            self._density = name
            self.Modified()

    @smproperty.stringvector(name="Velocity", label="Velocity",
                             default_values="Velocity")
    @smdomain.xml("""
        <ArrayListDomain name="array_list" attribute_type="Vectors">
          <RequiredProperties><Property name="Input" function="Input"/></RequiredProperties>
        </ArrayListDomain>""")
    @smhint.xml("""
        <PropertyWidgetDecorator type="GenericDecorator" mode="visibility"
                                 property="Weighting" value="2"/>""")
    def SetVelocity(self, name):
        if name != self._velocity:
            self._velocity = name
            self.Modified()

    # ---------------------------------------------------------------- execute

    def _mass_flux(self, dataset):
        """A copy whose every array is multiplied by the mass crossing there.

        Integrating that and dividing by the integral of the mass flux itself is
        the mass-flow average. The cell arrays are moved onto the points first,
        so every column of the table is weighted the same way.
        """
        surface = _as_polydata(dataset)
        if surface.GetNumberOfCells() == 0:
            return None
        if surface.GetCellData().GetNumberOfArrays():
            move = vtkCellDataToPointData()
            move.SetInputData(surface)
            move.PassCellDataOff()
            move.Update()
            surface = move.GetOutput()

        normals = vtkPolyDataNormals()
        normals.SetInputData(surface)
        normals.ComputePointNormalsOn()
        normals.ComputeCellNormalsOff()
        normals.SplittingOff()
        normals.ConsistencyOn()
        normals.Update()
        surface = normals.GetOutput()

        attributes = surface.GetPointData()
        velocity = attributes.GetArray(self._velocity)
        density = attributes.GetArray(self._density)
        if velocity is None or density is None:
            raise ValueError("mass-flow averaging needs both %s and %s on the "
                             "points" % (self._velocity, self._density))
        flux = (numpy_support.vtk_to_numpy(density) *
                np.einsum("ij,ij->i",
                          numpy_support.vtk_to_numpy(velocity),
                          numpy_support.vtk_to_numpy(attributes.GetNormals())))

        weighted = surface.NewInstance()
        weighted.CopyStructure(surface)
        for index in range(attributes.GetNumberOfArrays()):
            array = attributes.GetArray(index)
            if array is None or array.GetName() == "Normals":
                continue
            values = numpy_support.vtk_to_numpy(array)
            product = values * (flux if values.ndim == 1 else flux[:, None])
            column = numpy_support.numpy_to_vtk(np.ascontiguousarray(product), deep=1)
            column.SetName(array.GetName())
            weighted.GetPointData().AddArray(column)
        return _with_array(weighted, _WEIGHT, flux)

    def _row(self, label, pieces):
        """One row: what was integrated, where it is, and its averages."""
        row = {"Surface": label}
        if self._weighting == _MASS_FLOW:
            weighted = [piece for piece in (self._mass_flux(piece) for piece in pieces)
                        if piece is not None]
            integrals, measure, measured = integrate(weighted)
            flux = dict(integrals).get(_WEIGHT)
            if flux is None or float(flux) == 0.0:
                raise ValueError("no mass crosses %s, so it cannot be mass-flow "
                                 "averaged" % label)
            # The sign follows whichever way the normals happen to point;
            # flipping both leaves every average alone.
            flux = float(flux)
            names = ["Surface", measured, "MassFlow", "Centre"]
            row[measured] = measure
            row["MassFlow"] = abs(flux)
            for name, total in integrals:
                if name == _WEIGHT:
                    continue
                names.append(name)
                row[name] = total / flux
        else:
            integrals, measure, measured = integrate(pieces)
            divisor = measure if self._weighting == _AREA else 1.0
            if divisor == 0.0:
                raise ValueError("%s has no area to average over" % label)
            names = ["Surface", measured, "Centre"]
            row[measured] = measure
            for name, total in integrals:
                names.append(name)
                row[name] = total / divisor
        row["Centre"] = middle_of(pieces)
        return names, row

    def RequestData(self, _request, in_info, out_info):
        output = vtkTable.GetData(out_info, 0)
        source = vtkDataObject.GetData(in_info[0], 0)
        if source is None:
            return 1
        try:
            blocks = [(path, piece) for path, piece in named_leaves(source)
                      if piece.GetNumberOfCells()]
            if not blocks:
                print("Surface average: the input has no cells.")
                return 1

            if self._rows == _PER_BLOCK:
                groups = [(path or "input", [piece]) for path, piece in blocks]
            else:
                label = (blocks[0][0] or "input" if len(blocks) == 1
                         else "%d blocks" % len(blocks))
                groups = [(label, [piece for _path, piece in blocks])]

            names, rows = [], []
            for label, pieces in groups:
                try:
                    columns, row = self._row(label, pieces)
                except ValueError as error:
                    print("Surface average: %s." % error)
                    continue
                for name in columns:
                    if name not in names:
                        names.append(name)
                rows.append(row)

            if not rows:
                return 1
            missing = [name for name in names if any(name not in row for row in rows)]
            if missing:
                print("Surface average: %s is not on every block and was left "
                      "out." % ", ".join(missing))
                names = [name for name in names if name not in missing]
            _rows_to_table(output, names, rows)
        except Exception as error:
            print("Surface average: %s" % error)
            return 0
        return 1
