"""Headless verification for the CGNS labeled-boundary ParaView plugin.

Every expectation is read from the CGNS file itself, so this works for any file,
not just the two shipped here.

    pvpython verify_plugin.py [file.cgns]
"""

from pathlib import Path
import re
import sys

import h5py
import vtk
from paraview import servermanager
from paraview.simple import LoadPlugin


_FACE_ELEMENT_TYPES = {5, 6, 7, 8, 9}              # TRI_3..QUAD_9
_VOLUME_ELEMENT_TYPES = set(range(10, 20)) | {21}  # TETRA_4..HEXA_27, PYRA_13


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace").rstrip("\x00")
    return str(value)


def _label(node):
    return _text(node.attrs.get("label", ""))


def read_expectations(file_name):
    """Boundary section -> (first, last), and volume zone -> cell count."""
    faces = {}
    volumes = {}
    with h5py.File(file_name, "r") as handle:
        for base in handle.values():
            if _label(base) != "CGNSBase_t":
                continue
            for zone in base.values():
                if not isinstance(zone, h5py.Group) or _label(zone) != "Zone_t":
                    continue
                zone_has_volume = False
                for section in zone.values():
                    if not isinstance(section, h5py.Group) or _label(section) != "Elements_t":
                        continue
                    element_type = int(section[" data"][...].reshape(-1)[0])
                    if element_type in _VOLUME_ELEMENT_TYPES:
                        zone_has_volume = True
                    if element_type not in _FACE_ELEMENT_TYPES:
                        continue
                    first, last = (int(value) for value in
                                   section["ElementRange"][" data"][...].reshape(-1)[:2])
                    faces[section.name.rsplit("/", 1)[-1]] = (first, last)
                if zone_has_volume:
                    volumes[zone.name.rsplit("/", 1)[-1]] = int(zone[" data"][...].reshape(-1)[1])
    return faces, volumes


def _clean(name):
    """Strip the 'Block N_' that servermanager.Fetch prepends to composite block
    names in ParaView 6.1; the data object itself carries the plain name, so
    this reads the same on 5.x and 6.x."""
    return re.sub(r"^Block \d+_", "", name) if name else name


def leaf_blocks(composite):
    """Group name -> {block name: leaf dataset} for the plugin's output."""
    groups = {}
    for group_index in range(composite.GetNumberOfBlocks()):
        group = composite.GetBlock(group_index)
        group_name = _clean(composite.GetMetaData(group_index).Get(vtk.vtkCompositeDataSet.NAME()))
        blocks = {}
        if group is not None:
            for block_index in range(group.GetNumberOfBlocks()):
                name = _clean(group.GetMetaData(block_index).Get(vtk.vtkCompositeDataSet.NAME()))
                blocks[name] = group.GetBlock(block_index)
        groups[group_name] = blocks
    return groups


project_dir = Path(__file__).resolve().parent
cgns_file = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else project_dir / "bfs.cgns"
plugin_file = project_dir / "plugins" / "cgns_labeled_boundaries.py"

expected_faces, expected_volumes = read_expectations(str(cgns_file))
if not expected_faces or not expected_volumes:
    raise SystemExit("ERROR: %s has no boundary sections or no volume zones" % cgns_file)

LoadPlugin(str(plugin_file), remote=False, ns=globals())
reader = CGNSLabeledBoundaries(registrationName=cgns_file.stem, FileName=str(cgns_file))
reader.UpdatePipeline()

failures = []

# One pipeline item, everything else chosen in the Properties panel.
print("Properties panel checkboxes:")
print("  Volumes    : %s" % list(reader.GetProperty("Volumes").Available))
print("  Boundaries : %s" % list(reader.GetProperty("Boundaries").Available))
print("  CellArrays : %s" % list(reader.GetProperty("CellArrays").Available))

if set(reader.GetProperty("Boundaries").Available) != set(expected_faces):
    failures.append("the Boundaries checkbox list does not match the file's sections")
if set(reader.GetProperty("Volumes").Available) != set(expected_volumes):
    failures.append("the Volumes checkbox list does not match the file's zones")

groups = leaf_blocks(servermanager.Fetch(reader))
volume_blocks = groups.get("Volumes", {})
boundary_blocks = groups.get("Boundaries", {})

print("Volumes:")
for name, cells in sorted(expected_volumes.items()):
    block = volume_blocks.get(name)
    if block is None:
        failures.append("no Volumes block for zone %s" % name)
        continue
    print("  %s: %d cells, %d point arrays, %d cell arrays" %
          (name, block.GetNumberOfCells(),
           block.GetPointData().GetNumberOfArrays(),
           block.GetCellData().GetNumberOfArrays()))
    if block.GetNumberOfCells() != cells:
        failures.append("%s has %d cells, CGNS says %d" % (name, block.GetNumberOfCells(), cells))
    if block.GetPointData().GetNumberOfArrays() == 0:
        failures.append("%s carries no point data" % name)
    # Converted arrays must not also be offered as cell data, or the coloring
    # dropdown lists every solution array twice.
    if block.GetCellData().GetNumberOfArrays() != 0:
        failures.append("%s still exposes cell arrays" % name)

print("Boundaries (Cell Data to Point Data on, the default):")
for name, (first, last) in sorted(expected_faces.items(), key=lambda item: item[1]):
    block = boundary_blocks.get(name)
    if block is None:
        failures.append("no Boundaries block for section %s" % name)
        continue
    point_arrays = {block.GetPointData().GetArrayName(index)
                    for index in range(block.GetPointData().GetNumberOfArrays())}
    print("  %s: %d faces, %d point arrays, %d cell arrays" %
          (name, block.GetNumberOfCells(), len(point_arrays),
           block.GetCellData().GetNumberOfArrays()))
    if block.GetNumberOfCells() != last - first + 1:
        failures.append("%s has %d faces, CGNS says %d" %
                        (name, block.GetNumberOfCells(), last - first + 1))
    if not point_arrays:
        failures.append("%s carries no point data" % name)
    if "BoundaryId" not in point_arrays:
        failures.append("%s has no BoundaryId" % name)
    if block.GetCellData().GetNumberOfArrays() != 0:
        failures.append("%s still exposes cell arrays" % name)

# The boundary faces of a watertight mesh must close into a manifold shell.
# Not every CGNS file names all of its faces, so this is reported, not enforced.
append = vtk.vtkAppendFilter()
for block in boundary_blocks.values():
    append.AddInputData(block)
append.MergePointsOn()
append.Update()
from vtkmodules.vtkFiltersCore import vtkFeatureEdges
from vtkmodules.vtkFiltersGeometry import vtkGeometryFilter
geometry = vtkGeometryFilter()
geometry.SetInputData(append.GetOutput())
geometry.Update()
edges = vtkFeatureEdges()
edges.SetInputData(geometry.GetOutput())
edges.BoundaryEdgesOn()
edges.FeatureEdgesOff()
edges.NonManifoldEdgesOff()
edges.ManifoldEdgesOff()
edges.Update()
print("Boundary shell: %d faces, %d merged points, %d free edges%s" %
      (append.GetOutput().GetNumberOfCells(), append.GetOutput().GetNumberOfPoints(),
       edges.GetOutput().GetNumberOfCells(),
       " (closed)" if edges.GetOutput().GetNumberOfCells() == 0 else ""))

# Unticking the conversion gives the cell arrays back, element ids included.
reader.CellDatatoPointData = 0
reader.UpdatePipeline()
cell_mode = leaf_blocks(servermanager.Fetch(reader)).get("Boundaries", {})
print("Boundaries (Cell Data to Point Data off):")
for name, (first, last) in sorted(expected_faces.items(), key=lambda item: item[1]):
    block = cell_mode.get(name)
    if block is None:
        failures.append("no Boundaries block for section %s with the conversion off" % name)
        continue
    element_ids = block.GetCellData().GetArray("CGNSElementId")
    if element_ids is None:
        failures.append("%s has no CGNSElementId with the conversion off" % name)
        continue
    got = tuple(int(value) for value in element_ids.GetRange())
    print("  %s: %d faces, CGNS elements %d-%d, %d cell arrays, %d point arrays" %
          (name, block.GetNumberOfCells(), got[0], got[1],
           block.GetCellData().GetNumberOfArrays(),
           block.GetPointData().GetNumberOfArrays()))
    if got != (first, last):
        failures.append("%s covers CGNS elements %d-%d, the file says %d-%d" %
                        (name, got[0], got[1], first, last))
    if block.GetPointData().GetNumberOfArrays() != 0:
        failures.append("%s exposes point data with the conversion off" % name)
reader.CellDatatoPointData = 1
reader.UpdatePipeline()

# Unchecking boundaries must actually drop them from the output.
kept = sorted(expected_faces)[:1]
reader.Boundaries = kept
reader.Volumes = []
reader.UpdatePipeline()
groups = leaf_blocks(servermanager.Fetch(reader))
remaining = sorted(groups.get("Boundaries", {}))
print("With only %s checked and no volume: %s, %d cells" %
      (kept, remaining, reader.GetDataInformation().GetNumberOfCells()))
if remaining != kept:
    failures.append("unchecking boundaries did not change the output: %s" % remaining)
if groups.get("Volumes"):
    failures.append("unchecking the volume did not remove it")

if failures:
    raise SystemExit("ERROR: " + "; ".join(failures))
print("CGNS labeled-boundary plugin verified for %s: %d volume zones, %d boundaries" %
      (cgns_file.name, len(expected_volumes), len(expected_faces)))
