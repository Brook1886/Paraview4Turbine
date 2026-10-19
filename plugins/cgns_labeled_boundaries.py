"""ParaView plugin: a CGNS reader whose boundaries are chosen in the Properties panel.

Loading a CGNS file produces a single Pipeline Browser entry. Its Properties
panel lists every volume zone and every named boundary-face section as a
checkbox, the way the stock reader lists its cell and point arrays. The output
is one multiblock, so the Multi-block Inspector shows, hides and colors each
boundary individually.

Two CGNS layouts are supported:

* the face sections live in their own lower-dimensional base (``bfs.cgns``).
  ParaView's reader loads those sections as cells, so each section is a plain
  cell range of that base's grid;
* the face sections share the zone with the volume elements (``r37.cgns``).
  ParaView's reader keeps only the volume cells of such a zone, so the faces are
  rebuilt from the CGNS connectivity and inherit the cell data of the volume
  cell they bound.
"""

import os
import sys
import weakref

import h5py
import numpy as np

from paraview.util.vtkAlgorithm import smdomain, smhint, smproperty, smproxy
from vtkmodules.util import numpy_support
from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase
from vtkmodules.vtkCommonCore import (vtkDataArraySelection, vtkIdList,
                                      vtkIntArray, vtkPoints, vtkStringArray)
from vtkmodules.vtkCommonDataModel import (vtkCellArray, vtkCompositeDataSet,
                                           vtkMultiBlockDataSet, vtkUnstructuredGrid)
from vtkmodules.vtkFiltersCore import vtkCellDataToPointData
try:                                                   # ParaView 6.1+ (VTK 9.4)
    from vtkmodules.vtkFiltersCore import vtkExtractCells
except ImportError:                                    # ParaView 5.x
    from vtkmodules.vtkFiltersExtraction import vtkExtractCells
from vtkmodules.vtkIOCGNSReader import vtkCGNSReader

# The toolbar macros are run by ParaView as bare strings, with no __file__ of
# their own to find their common code by. A plugin has one, so it puts pylib
# where a macro can import it from however ParaView was started.
_PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pylib")
if _PYLIB not in sys.path:
    sys.path.append(_PYLIB)


# CGNS ElementType_t integer values used by CGNS 4.x.
_ELEMENT_DIMENSION = {
    2: 0,                                      # NODE
    3: 1, 4: 1,                               # BAR_2, BAR_3
    5: 2, 6: 2, 7: 2, 8: 2, 9: 2,           # TRI/QUAD
    10: 3, 11: 3, 12: 3, 13: 3,              # TETRA/PYRA
    14: 3, 15: 3, 16: 3, 17: 3, 18: 3, 19: 3,  # PENTA/HEXA
    21: 3,                                    # PYRA_13
    22: 2, 23: 3,                             # NGON_n, NFACE_n
    24: 1,                                    # BAR_4
    25: 2, 26: 2, 27: 2, 28: 2,              # high-order TRI/QUAD
    29: 3, 30: 3, 31: 3, 32: 3, 33: 3,
    34: 3, 35: 3, 36: 3, 37: 3, 38: 3, 39: 3,
}

# ElementType_t -> (VTK cell type, nodes per element).  Only the element types
# that can be rebuilt from a fixed-stride ElementConnectivity are listed; a
# section of any other type is reported instead of being silently dropped.
_ELEMENT_VTK_TYPE = {
    2: (1, 1),                                # NODE          -> VTK_VERTEX
    3: (3, 2), 4: (21, 3),                    # BAR_2/BAR_3   -> VTK_(QUADRATIC_)LINE
    5: (5, 3), 6: (22, 6),                    # TRI_3/TRI_6   -> VTK_(QUADRATIC_)TRIANGLE
    7: (9, 4), 8: (23, 8), 9: (28, 9),        # QUAD_4/8/9    -> VTK_(BI)QUADRATIC_QUAD
}


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip("\x00")
    return str(value)


def _label(node):
    return _text(node.attrs.get("label", ""))


def _data(node):
    return node[" data"][...]


def read_mesh_metadata(file_name):
    """Return labeled face sections and volume-bearing CGNS zones.

    Each face record is a dict.  ``in_zone`` marks a section that shares its
    zone with volume elements, which is what decides how the faces are built.
    """
    face_records = []
    volume_zones = []
    base_dimensions = []
    unsupported = []

    with h5py.File(file_name, "r") as handle:
        bases = [node for node in handle.values() if _label(node) == "CGNSBase_t"]
        for base in bases:
            base_dimensions.append(int(_data(base).reshape(-1)[0]))

        if not base_dimensions:
            raise RuntimeError("No CGNSBase_t nodes were found in %s." % file_name)

        face_dimension = max(base_dimensions) - 1
        volume_dimension = max(base_dimensions)
        for base in bases:
            base_name = base.name.rsplit("/", 1)[-1]
            for zone in base.values():
                if not isinstance(zone, h5py.Group) or _label(zone) != "Zone_t":
                    continue
                zone_name = zone.name.rsplit("/", 1)[-1]
                zone_faces = []
                zone_has_volume = False
                for section in zone.values():
                    if not isinstance(section, h5py.Group) or _label(section) != "Elements_t":
                        continue
                    element_type = int(_data(section).reshape(-1)[0])
                    element_dimension = _ELEMENT_DIMENSION.get(element_type)
                    if element_dimension == volume_dimension:
                        zone_has_volume = True
                    if element_dimension != face_dimension:
                        continue
                    ranges = [child for child in section.values()
                              if isinstance(child, h5py.Group) and _label(child) == "IndexRange_t"]
                    if not ranges:
                        continue
                    first, last = (int(value) for value in _data(ranges[0]).reshape(-1)[:2])
                    zone_faces.append({
                        "base": base_name,
                        "zone": zone_name,
                        "name": section.name.rsplit("/", 1)[-1],
                        "first": first,
                        "last": last,
                        "type": element_type,
                    })
                if zone_has_volume:
                    volume_zones.append({"base": base_name, "zone": zone_name})
                for record in zone_faces:
                    # Sections that share the zone with volume elements are not
                    # loaded by ParaView's CGNS reader and must be rebuilt from
                    # their connectivity.
                    record["in_zone"] = zone_has_volume
                    record["kind"] = "connectivity" if zone_has_volume else "range"
                    if zone_has_volume and record["type"] not in _ELEMENT_VTK_TYPE:
                        unsupported.append("%s/%s/%s (ElementType_t %d)" %
                                           (base_name, zone_name, record["name"],
                                            record["type"]))
                        continue
                    face_records.append(record)

    face_records.sort(key=lambda item: (item["base"], item["zone"],
                                        item["first"], item["name"]))
    volume_zones.sort(key=lambda item: (item["base"], item["zone"]))
    if unsupported:
        print("CGNS labeled boundaries: skipping unsupported sections: %s" %
              ", ".join(unsupported))

    # Unique, stable display names for the Properties panel checkboxes.
    face_counts = {}
    for record in face_records:
        face_counts[record["name"]] = face_counts.get(record["name"], 0) + 1
    for index, record in enumerate(face_records):
        record["id"] = index
        record["title"] = (record["name"] if face_counts[record["name"]] == 1
                           else "%s_%s" % (record["zone"], record["name"]))

    zone_counts = {}
    for record in volume_zones:
        zone_counts[record["zone"]] = zone_counts.get(record["zone"], 0) + 1
    for record in volume_zones:
        record["title"] = (record["zone"] if zone_counts[record["zone"]] == 1
                           else "%s_%s" % (record["base"], record["zone"]))
    return face_records, volume_zones


def _walk_blocks(node, path=()):
    if node is None:
        return
    if node.IsA("vtkMultiBlockDataSet"):
        for block_index in range(node.GetNumberOfBlocks()):
            child = node.GetBlock(block_index)
            metadata = node.GetMetaData(block_index)
            name = ""
            if metadata and metadata.Has(vtkCompositeDataSet.NAME()):
                name = metadata.Get(vtkCompositeDataSet.NAME())
            for item in _walk_blocks(child, path + (name,)):
                yield item
    else:
        yield path, node


def zone_leaves(source):
    """Map (base name, zone name) to the grid the CGNS reader produced."""
    leaves = {}
    for path, leaf in _walk_blocks(source):
        if len(path) >= 2:
            leaves[(path[-2], path[-1])] = leaf
    return leaves


def _copy_arrays(source_data, target_data, indices):
    for array_index in range(source_data.GetNumberOfArrays()):
        array = source_data.GetArray(array_index)
        if array is None:
            continue
        values = numpy_support.vtk_to_numpy(array)[indices]
        copied = numpy_support.numpy_to_vtk(np.ascontiguousarray(values), deep=1)
        copied.SetName(array.GetName())
        target_data.AddArray(copied)


def _extract_range(grid, section):
    """A section ParaView already loaded as cells of its own base."""
    first_cell = section["first"] - 1
    last_cell = section["last"] - 1
    if first_cell < 0 or last_cell >= grid.GetNumberOfCells():
        return None
    extract = vtkExtractCells()
    extract.SetInputData(grid)
    extract.AddCellRange(first_cell, last_cell)
    extract.Update()
    piece = vtkUnstructuredGrid()
    piece.ShallowCopy(extract.GetOutput())
    return piece


def _parent_cells(grid, faces):
    """The volume cell bounded by each face, so faces inherit its cell data."""
    if not grid.IsA("vtkUnstructuredGrid"):
        return None
    grid.BuildLinks()
    point_ids = vtkIdList()
    neighbors = vtkIdList()
    parents = np.zeros(len(faces), dtype=np.int64)
    for face_index in range(len(faces)):
        point_ids.Reset()
        for point_id in faces[face_index]:
            point_ids.InsertNextId(int(point_id))
        grid.GetCellNeighbors(-1, point_ids, neighbors)
        if neighbors.GetNumberOfIds() < 1:
            return None
        parents[face_index] = neighbors.GetId(0)
    return parents


def _extract_connectivity(grid, section, file_name):
    """A section sharing its zone with the volume mesh, so ParaView's reader
    dropped it: rebuild the faces from the CGNS connectivity."""
    vtk_type, nodes_per_element = _ELEMENT_VTK_TYPE[section["type"]]
    count = section["last"] - section["first"] + 1
    with h5py.File(file_name, "r") as handle:
        node = handle[section["base"]][section["zone"]][section["name"]]
        connectivity = node["ElementConnectivity"][" data"][...].astype(np.int64)
    if count <= 0 or connectivity.size != count * nodes_per_element:
        return None
    faces = connectivity.reshape(count, nodes_per_element) - 1
    if faces.min() < 0 or faces.max() >= grid.GetNumberOfPoints():
        return None

    unique_points, local = np.unique(faces, return_inverse=True)
    local = local.reshape(faces.shape)

    coordinates = numpy_support.vtk_to_numpy(grid.GetPoints().GetData())
    points = vtkPoints()
    points.SetData(numpy_support.numpy_to_vtk(
        np.ascontiguousarray(coordinates[unique_points]), deep=1))

    offsets = np.arange(0, (count + 1) * nodes_per_element,
                        nodes_per_element, dtype=np.int64)
    cells = vtkCellArray()
    cells.SetData(
        numpy_support.numpy_to_vtkIdTypeArray(offsets, deep=1),
        numpy_support.numpy_to_vtkIdTypeArray(
            np.ascontiguousarray(local.ravel()), deep=1))

    piece = vtkUnstructuredGrid()
    piece.SetPoints(points)
    piece.SetCells(vtk_type, cells)
    _copy_arrays(grid.GetPointData(), piece.GetPointData(), unique_points)

    parents = _parent_cells(grid, faces)
    if parents is not None:
        _copy_arrays(grid.GetCellData(), piece.GetCellData(), parents)
    return piece


def _to_point_data(piece):
    """Move the CGNS cell arrays onto the points.

    PassCellData stays off, so each array appears once. Otherwise every solution
    array shows up twice in the coloring dropdown, as a point copy and a cell
    copy of the same numbers.
    """
    converter = vtkCellDataToPointData()
    converter.SetInputData(piece)
    converter.PassCellDataOff()
    converter.Update()
    converted = vtkUnstructuredGrid()
    converted.ShallowCopy(converter.GetOutput())
    return converted


def _add_field_text(piece, name, value):
    text_array = vtkStringArray()
    text_array.SetName(name)
    text_array.InsertNextValue(value)
    piece.GetFieldData().AddArray(text_array)


def _keep_only_zone_name(piece, zone):
    """Leave one field-data array on a piece, the name of its CGNS zone.

    vtkCGNSReader hangs a ``Base/Zone`` array on every zone and the solver adds
    its own ``ispatch`` and ``issurface``, which ``vtkExtractCells`` then carries
    onto a range boundary as well. They are one value per block, no use for
    colouring, and so much noise in the Coloring dropdown; the zone name is the
    one worth keeping, for telling the blocks of a stage apart.
    """
    piece.GetFieldData().Initialize()
    _add_field_text(piece, "CGNSZoneName", zone)


def _label_boundary(piece, section, converting):
    """Identifying cell arrays, added before any conversion to point data.

    BoundaryId is constant across a boundary, so interpolating it onto the
    points is exact. An element id is not, so it is only offered when the cell
    arrays are being kept.
    """
    count = piece.GetNumberOfCells()

    ids = vtkIntArray()
    ids.SetName("BoundaryId")
    ids.SetNumberOfTuples(count)
    ids.Fill(section["id"])
    piece.GetCellData().AddArray(ids)

    if not converting:
        element_ids = numpy_support.numpy_to_vtkIdTypeArray(
            np.arange(section["first"], section["first"] + count, dtype=np.int64), deep=1)
        element_ids.SetName("CGNSElementId")
        piece.GetCellData().AddArray(element_ids)


def _modified_callback(algorithm):
    """Re-execute when a Properties panel checkbox changes."""
    reference = weakref.ref(algorithm)

    def mark_modified(*_args, **_kwargs):
        target = reference()
        if target is not None:
            target.Modified()

    return mark_modified


def _enabled_names(selection):
    return [selection.GetArrayName(index)
            for index in range(selection.GetNumberOfArrays())
            if selection.ArrayIsEnabled(selection.GetArrayName(index))]


@smproxy.reader(name="CGNSLabeledBoundaryReader",
                label="CGNS Labeled Boundaries",
                extensions="cgns",
                file_description="CGNS (labeled boundaries)")
class CGNSLabeledBoundaryReader(VTKPythonAlgorithmBase):
    """One pipeline item per file; every zone and boundary is a checkbox."""

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=0, nOutputPorts=1,
                                        outputType="vtkMultiBlockDataSet")
        self._file_name = None
        self._described_file = None
        self._sections = []
        self._volume_zones = []
        self._reader = vtkCGNSReader()
        self._convert_to_point_data = True
        self._volumes = self._make_selection()
        self._boundaries = self._make_selection()
        self._cell_arrays = self._make_selection()
        self._point_arrays = self._make_selection()

    def _make_selection(self):
        selection = vtkDataArraySelection()
        selection.AddObserver("ModifiedEvent", _modified_callback(self))
        return selection

    # ------------------------------------------------------------------ setup

    def _describe_file(self):
        """Read the CGNS index once per file and fill the checkbox lists."""
        if self._described_file == self._file_name:
            return
        self._sections, self._volume_zones = read_mesh_metadata(self._file_name)

        self._reader.SetFileName(self._file_name)
        self._reader.UpdateInformation()

        self._fill_selection(self._volumes,
                             [zone["title"] for zone in self._volume_zones])
        self._fill_selection(self._boundaries,
                             [section["title"] for section in self._sections])
        self._fill_selection(
            self._cell_arrays,
            [self._reader.GetCellArrayName(index)
             for index in range(self._reader.GetNumberOfCellArrays())])
        self._fill_selection(
            self._point_arrays,
            [self._reader.GetPointArrayName(index)
             for index in range(self._reader.GetNumberOfPointArrays())])
        self._described_file = self._file_name

    @staticmethod
    def _fill_selection(selection, names):
        """Everything on by default, keeping any choice already made."""
        previous = {}
        for index in range(selection.GetNumberOfArrays()):
            name = selection.GetArrayName(index)
            previous[name] = selection.ArrayIsEnabled(name)
        selection.RemoveAllArrays()
        for name in names:
            selection.AddArray(name)
            if previous.get(name) == 0:
                selection.DisableArray(name)

    # ------------------------------------------------------------- properties

    @smproperty.stringvector(name="FileName", panel_visibility="never")
    @smdomain.filelist()
    @smhint.filechooser(extensions="cgns", file_description="CGNS (labeled boundaries)")
    def SetFileName(self, name):
        if name != self._file_name:
            self._file_name = name
            self._described_file = None
            self.Modified()

    @smproperty.dataarrayselection(name="Volumes")
    def GetVolumesSelection(self):
        return self._volumes

    @smproperty.dataarrayselection(name="Boundaries")
    def GetBoundariesSelection(self):
        return self._boundaries

    @smproperty.dataarrayselection(name="CellArrays")
    def GetCellArraysSelection(self):
        return self._cell_arrays

    @smproperty.dataarrayselection(name="PointArrays")
    def GetPointArraysSelection(self):
        return self._point_arrays

    @smproperty.intvector(name="CellDataToPointData",
                          label="Cell Data to Point Data", default_values=1)
    @smdomain.xml('<BooleanDomain name="bool"/>')
    def SetCellDataToPointData(self, value):
        value = bool(value)
        if value != self._convert_to_point_data:
            self._convert_to_point_data = value
            self.Modified()

    # ---------------------------------------------------------------- execute

    def RequestInformation(self, _request, _in_info, _out_info):
        if not self._file_name:
            return 1
        try:
            self._describe_file()
        except Exception as error:
            print("CGNS labeled boundaries: %s" % error)
            return 0
        return 1

    def _configure_reader(self):
        """Load only the bases the enabled zones and boundaries actually need."""
        wanted_volumes = [zone for zone in self._volume_zones
                          if self._volumes.ArrayIsEnabled(zone["title"])]
        wanted_sections = [section for section in self._sections
                           if self._boundaries.ArrayIsEnabled(section["title"])]

        needed_bases = ({zone["base"] for zone in wanted_volumes} |
                        {section["base"] for section in wanted_sections})
        for index in range(self._reader.GetNumberOfBaseArrays()):
            name = self._reader.GetBaseArrayName(index)
            self._reader.SetBaseArrayStatus(name, 1 if name in needed_bases else 0)

        for index in range(self._cell_arrays.GetNumberOfArrays()):
            name = self._cell_arrays.GetArrayName(index)
            self._reader.SetCellArrayStatus(
                name, 1 if self._cell_arrays.ArrayIsEnabled(name) else 0)
        for index in range(self._point_arrays.GetNumberOfArrays()):
            name = self._point_arrays.GetArrayName(index)
            self._reader.SetPointArrayStatus(
                name, 1 if self._point_arrays.ArrayIsEnabled(name) else 0)
        return wanted_volumes, wanted_sections

    def _finish(self, piece):
        if self._convert_to_point_data:
            piece = _to_point_data(piece)
        return piece

    def RequestData(self, _request, _in_info, out_info):
        output = vtkMultiBlockDataSet.GetData(out_info, 0)
        output.Initialize()
        if not self._file_name:
            return 1
        try:
            self._describe_file()
            wanted_volumes, wanted_sections = self._configure_reader()
            if not wanted_volumes and not wanted_sections:
                return 1

            self._reader.Update()
            leaves = zone_leaves(self._reader.GetOutput())

            volumes = vtkMultiBlockDataSet()
            for zone in wanted_volumes:
                grid = leaves.get((zone["base"], zone["zone"]))
                if grid is None:
                    continue
                piece = vtkUnstructuredGrid()
                piece.ShallowCopy(grid)
                piece = self._finish(piece)
                _keep_only_zone_name(piece, zone["zone"])
                index = volumes.GetNumberOfBlocks()
                volumes.SetBlock(index, piece)
                volumes.GetMetaData(index).Set(vtkCompositeDataSet.NAME(), zone["title"])

            boundaries = vtkMultiBlockDataSet()
            for section in wanted_sections:
                grid = leaves.get((section["base"], section["zone"]))
                if grid is None:
                    continue
                if section["kind"] == "connectivity":
                    piece = _extract_connectivity(grid, section, self._file_name)
                else:
                    piece = _extract_range(grid, section)
                if piece is None:
                    print("CGNS labeled boundaries: could not build boundary %s" %
                          section["name"])
                    continue
                _label_boundary(piece, section, self._convert_to_point_data)
                piece = self._finish(piece)
                _keep_only_zone_name(piece, section["zone"])
                index = boundaries.GetNumberOfBlocks()
                boundaries.SetBlock(index, piece)
                boundaries.GetMetaData(index).Set(vtkCompositeDataSet.NAME(),
                                                  section["title"])

            output.SetNumberOfBlocks(2)
            output.SetBlock(0, volumes)
            output.GetMetaData(0).Set(vtkCompositeDataSet.NAME(), "Volumes")
            output.SetBlock(1, boundaries)
            output.GetMetaData(1).Set(vtkCompositeDataSet.NAME(), "Boundaries")
        except Exception as error:
            print("CGNS labeled boundaries: %s" % error)
            return 0
        return 1
