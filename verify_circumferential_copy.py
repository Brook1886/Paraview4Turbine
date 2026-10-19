"""Headless verification for the Circumferential Copy filter and its macro.

The rotation is checked against one computed here with numpy, and, when the file
has the periodic pair of a turbomachinery passage, against the mesh itself: one
pitch must carry the LEFT patch exactly onto the RIGHT one.

    pvpython verify_circumferential_copy.py [file.cgns]
"""

from pathlib import Path
import re
import sys

import numpy as np
import vtk
from paraview import servermanager
from paraview import simple as pvs
from vtkmodules.util import numpy_support


HERE = Path(__file__).resolve().parent
# Loose enough for the float32 arrays CGNS files carry, tight enough that a
# wrong angle cannot hide behind it.
TOLERANCE = 1e-6


def points_of(block):
    return numpy_support.vtk_to_numpy(block.GetPoints().GetData())


def array_of(block, name):
    array = block.GetPointData().GetArray(name)
    return None if array is None else numpy_support.vtk_to_numpy(array)


def worst(got, expected):
    """Largest difference, as a fraction of how big the values are."""
    got, expected = np.asarray(got, dtype=float), np.asarray(expected, dtype=float)
    return np.abs(got - expected).max() / max(np.abs(expected).max(), 1e-30)


def rotated(values, degrees):
    """The same rotation the filter should have applied, about X through 0."""
    angle = np.radians(degrees)
    cos, sin = np.cos(angle), np.sin(angle)
    turned = np.empty_like(np.asarray(values, dtype=float))
    turned[:, 0] = values[:, 0]
    turned[:, 1] = cos * values[:, 1] - sin * values[:, 2]
    turned[:, 2] = sin * values[:, 1] + cos * values[:, 2]
    return turned


def active_vectors(block):
    vectors = block.GetPointData().GetVectors()
    return vectors.GetName() if vectors else None


def leaves(node, path=""):
    """Every dataset in a composite output, by its block path."""
    found = {}
    if node is None:
        return found
    if node.IsA("vtkCompositeDataSet"):
        for index in range(node.GetNumberOfBlocks()):
            metadata = node.GetMetaData(index)
            name = (metadata.Get(vtk.vtkCompositeDataSet.NAME())
                    if metadata and metadata.Has(vtk.vtkCompositeDataSet.NAME()) else str(index))
            name = re.sub(r"^Block \d+_", "", name) if name else name
            found.update(leaves(node.GetBlock(index), "%s/%s" % (path, name)))
    else:
        found[path] = node
    return found


cgns_file = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "r37.cgns").resolve()
# paraview_diy.sh puts this on PYTHONPATH; the macro is run here as ParaView
# runs it, so it has to be able to import what the macros share.
sys.path.insert(0, str(HERE / "pylib"))

# Loaded into paraview.simple rather than into this script, because that is
# where the macro looks the filter up, exactly as it does in the GUI.
for plugin in ("cgns_labeled_boundaries.py", "circumferential_copy.py"):
    pvs.LoadPlugin(str(HERE / "plugins" / plugin), remote=False, ns=vars(pvs))

# Imported as well as loaded, so the array handling can be exercised on a
# dataset built here rather than only through the pipeline. The rotation is the
# plugin's; moving a dataset is the machinery both copies share.
sys.path.insert(0, str(HERE / "plugins"))
import circumferential_copy
import passage_copies

failures = []

reader = pvs.CGNSLabeledBoundaries(FileName=str(cgns_file))
reader.Volumes = []
reader.UpdatePipeline()
boundary_names = list(reader.Boundaries)
first_boundary = boundary_names[0]
reader.Boundaries = [first_boundary]
reader.UpdatePipeline()
source_blocks = leaves(servermanager.Fetch(reader))
source_block = list(source_blocks.values())[0]
source_points = points_of(source_block)
print("Input %s/%s: %d faces, %d points" %
      (cgns_file.name, first_boundary, source_block.GetNumberOfCells(),
       source_block.GetNumberOfPoints()))

# --------------------------------------------------------------- the geometry

copies = pvs.CircumferentialCopy(Input=reader)
copies.RotationAxis = 0            # X
# Everything about the copies is in the table; the line with no name on it is
# what a block none of the named lines claim goes by, which here is all of them.
copies.BladeRows = ["", "36", "4"]
copies.UpdatePipeline()
blocks = leaves(servermanager.Fetch(copies))
print("Output blocks: %s" % ", ".join(sorted(blocks)))

expected_paths = ["/Boundaries/%s/Copy %d" % (first_boundary, index) for index in range(4)]
if sorted(blocks) != sorted(expected_paths):
    failures.append("the block tree is %s, expected %s" %
                    (sorted(blocks), sorted(expected_paths)))

pitch = 360.0 / 36
for index in range(4):
    block = blocks.get("/Boundaries/%s/Copy %d" % (first_boundary, index))
    if block is None:
        continue
    if block.GetNumberOfCells() != source_block.GetNumberOfCells():
        failures.append("copy %d has %d cells, the input has %d" %
                        (index, block.GetNumberOfCells(), source_block.GetNumberOfCells()))
    moved = worst(points_of(block), rotated(source_points, index * pitch))
    print("  copy %d at %5.1f deg: worst point off by %.2e" % (index, index * pitch, moved))
    if moved > TOLERANCE:
        failures.append("copy %d is not the input rotated by %g degrees (off by %.3e)" %
                        (index, index * pitch, moved))

    ids = array_of(block, "CopyId")
    if ids is None or ids.min() != index or ids.max() != index:
        failures.append("copy %d does not carry a constant CopyId of %d" % (index, index))
    angles = block.GetFieldData().GetArray("CopyAngle")
    if angles is None or abs(angles.GetValue(0) - index * pitch) > 1e-9:
        failures.append("copy %d does not carry a CopyAngle of %g" % (index, index * pitch))

# ---------------------------------------------------------------- the vectors

vector_names = [name for name in
                (source_block.GetPointData().GetArrayName(index)
                 for index in range(source_block.GetPointData().GetNumberOfArrays()))
                if source_block.GetPointData().GetArray(name).GetNumberOfComponents() == 3]
if not vector_names:
    print("No three-component array in the input; the vector rotation is untested.")
for name in vector_names:
    source_values = array_of(source_block, name)
    block = blocks["/Boundaries/%s/Copy 1" % first_boundary]
    moved = worst(array_of(block, name), rotated(source_values, pitch))
    print("  %s rotated with the mesh: worst component off by %.2e" % (name, moved))
    if moved > TOLERANCE:
        failures.append("%s was not rotated with the mesh (off by %.3e)" % (name, moved))

copies.RotateVectors = 0
copies.UpdatePipeline()
untouched = leaves(servermanager.Fetch(copies))["/Boundaries/%s/Copy 1" % first_boundary]
for name in vector_names:
    if worst(array_of(untouched, name), array_of(source_block, name)) > TOLERANCE:
        failures.append("%s was still rotated with Rotate Vectors off" % name)
if vector_names:
    print("With Rotate Vectors off, %s is left alone." % ", ".join(vector_names))

# Whether the arrays are turned or not, the copies must agree with the input
# about which array is the active vectors, or ParaView's own filters downstream
# would pick a different default.
print("Active vectors: %s in the input, %s turned, %s left alone." %
      (active_vectors(source_block),
       active_vectors(blocks["/Boundaries/%s/Copy 1" % first_boundary]),
       active_vectors(untouched)))
for block, mode in ((blocks["/Boundaries/%s/Copy 1" % first_boundary], "on"),
                    (untouched, "off")):
    if active_vectors(block) != active_vectors(source_block):
        failures.append("with Rotate Vectors %s the active vectors became %s, "
                        "the input has %s" %
                        (mode, active_vectors(block), active_vectors(source_block)))
copies.RotateVectors = 1

# ------------------------------------------------------------------ the pitch

# A negative blade count is how the copies are made the other way round: -48
# blades is a pitch of -7.5 degrees, the same wedge measured backwards.
copies.BladeRows = ["", "-48", "2"]
copies.UpdatePipeline()
by_angle = leaves(servermanager.Fetch(copies))
turned = by_angle["/Boundaries/%s/Copy 1" % first_boundary]
if worst(points_of(turned), rotated(source_points, -7.5)) > TOLERANCE:
    failures.append("a blade count of -48 did not give a pitch of -7.5 degrees")
else:
    print("A blade count of -48 turns the copy the other way, by 7.5 degrees.")
if len(by_angle) != 2:
    failures.append("2 copies produced %d blocks" % len(by_angle))

# -------------------------------------------------------------- full annulus
# The tick that saves working the count out: every block repeated its own blade
# count, whatever the copies column says.

copies.BladeRows = ["", "24", "2"]
copies.FullAnnulus = 1
copies.UpdatePipeline()
whole = leaves(servermanager.Fetch(copies))
print("Full Annulus of a 24-blade row: %d copies, the last at %.1f degrees."
      % (len(whole),
         whole["/Boundaries/%s/Copy 23" % first_boundary].GetFieldData()
         .GetArray("CopyAngle").GetValue(0) if len(whole) == 24 else float("nan")))
if len(whole) != 24:
    failures.append("Full Annulus of a 24-blade row gave %d copies" % len(whole))
copies.FullAnnulus = 0

# --------------------------------------------------- the table of blade rows
# A stage is several rows at several pitches, and one filter covers the lot: a
# line of the table gives a block its own pitch and its own number of copies,
# and hands both down to everything under it. Checked here on two boundaries of
# the one mesh, which the filter cannot tell from two rows of a stage.

named = boundary_names[1] if len(boundary_names) > 1 else first_boundary
reader.Boundaries = sorted({first_boundary, named})
reader.UpdatePipeline()
# 10 degrees and two copies for anything not named, 30 degrees and three for
# this one.
copies.BladeRows = ["", "36", "2", named, "12", "3"]
copies.UpdatePipeline()
per_row = leaves(servermanager.Fetch(copies))
counted = {}
for path in per_row:
    counted[path.split("/")[-2]] = counted.get(path.split("/")[-2], 0) + 1
print("With one line of the table, %s" %
      ", ".join("%s is repeated %d times" % pair for pair in sorted(counted.items())))
if counted.get(named) != 3:
    failures.append("the row the table names got %s copies, expected 3"
                    % counted.get(named))
if named != first_boundary and counted.get(first_boundary) != 2:
    failures.append("a row the table does not name got %s copies, expected 2"
                    % counted.get(first_boundary))

step = per_row["/Boundaries/%s/Copy 1" % named].GetFieldData().GetArray("CopyAngle")
if step is None or abs(step.GetValue(0) - 30.0) > 1e-9:
    failures.append("the copy of a named row is not one 30 degree pitch on")
elif worst(points_of(per_row["/Boundaries/%s/Copy 1" % named]),
           rotated(points_of(leaves(servermanager.Fetch(reader))[
               "/Boundaries/%s" % named]), 30.0)) > TOLERANCE:
    failures.append("the named row's copy was not turned by its own pitch")
else:
    print("A row the table names turns by its own 360 / 12 rather than the "
          "panel's 360 / 36.")

# A cell left empty is a line saying nothing about that column, not a line
# saying zero, so the nameless line still applies.
copies.BladeRows = ["", "36", "2", named, "12", ""]
copies.UpdatePipeline()
counted = {}
for path in leaves(servermanager.Fetch(copies)):
    counted[path.split("/")[-2]] = counted.get(path.split("/")[-2], 0) + 1
print("With its copies cell left empty, %s is repeated %d times."
      % (named, counted.get(named, 0)))
if counted.get(named) != 2:
    failures.append("an empty copies cell gave %s copies, expected the panel's 2"
                    % counted.get(named))

# A line naming nothing in the input must leave every block alone rather than
# quietly doing something else. The filter says so on the Output Messages.
copies.BladeRows = ["", "36", "2", "NoSuchRow", "4", "9"]
copies.UpdatePipeline()
if len(leaves(servermanager.Fetch(copies))) != len(counted) * 2:
    failures.append("a line naming no block in the input changed the copies")
else:
    print("A line naming nothing in the input leaves every block on 360 / 36.")

# A table nobody could act on is refused, saying which line and why: two lines
# for one row have no right answer to pick between, and a cell that is not a
# blade count has none either. Nothing is drawn rather than something wrong.
for wrong, why in ((["IGV", "32", "4", "IGV", "28", "2"], "two lines for one row"),
                   (["", "thirty-six", "4"], "a blade count that is not a number"),
                   (["", "0.5", "4"], "half a blade")):
    lines = [tuple(wrong[index:index + 3]) for index in range(0, len(wrong), 3)]
    try:
        table = passage_copies.read_rows(lines, circumferential_copy.pitch_of_blades)
        failures.append("%s was accepted, as %s" % (why, table))
        continue
    except ValueError as error:
        print("%s is refused: %s" % (why[0].upper() + why[1:], error))
    copies.BladeRows = wrong
    copies.UpdatePipeline()
    if copies.GetDataInformation().GetNumberOfCells():
        failures.append("%s still drew something" % why)

# A block the table leaves with no pitch at all is copied once, where it is,
# and named in the report rather than failing the filter.
copies.BladeRows = [named, "12", "3"]
copies.UpdatePipeline()
alone = leaves(servermanager.Fetch(copies))
print("With no nameless line, %s is copied 3 times and the rest once."
      % named)
if len([path for path in alone if "/%s/" % first_boundary in path]) != 1:
    failures.append("a block the table says nothing about was not left alone")

copies.BladeRows = ["", "36", "4"]

# ---------------------------------------------- the mesh's own periodic pair
# One pitch must carry the periodic patch onto its partner. This is the check
# that the pitch and the direction are right rather than merely self-consistent.

periodic = False
pairs = [(left, right) for left, right in
         [(name, name.replace("LEFT", "RIGHT")) for name in boundary_names if "LEFT" in name]
         if right in boundary_names]
if not pairs:
    print("No LEFT/RIGHT periodic pair in %s; the pitch is not checked against "
          "the mesh." % cgns_file.name)
for left, right in pairs:
    reader.Boundaries = [left, right]
    reader.UpdatePipeline()
    patches = {path.rsplit("/", 1)[-1]: block
               for path, block in leaves(servermanager.Fetch(reader)).items()}
    copies.BladeRows = ["", "36", "2"]
    copies.UpdatePipeline()
    moved = leaves(servermanager.Fetch(copies))["/Boundaries/%s/Copy 1" % left]

    from scipy.spatial import cKDTree
    gap = cKDTree(points_of(patches[right])).query(points_of(moved))[0].max()
    size = np.ptp(points_of(patches[right]), axis=0).max()
    print("One pitch takes %s onto %s: worst point %.2e off, mesh is %.3f across."
          % (left, right, gap, size))
    if gap > 1e-6 * size:
        failures.append("one pitch does not carry %s onto %s (worst gap %.3e)" %
                        (left, right, gap))
    else:
        periodic = True

# ---------------------------------------------------- the raw cell arrays
# With the reader's conversion off the arrays arrive as cell data, which is the
# other half of the filter: cell vectors have to turn too, and CopyId lands on
# the cells instead of the points.

reader.CellDatatoPointData = 0
reader.Boundaries = [first_boundary]
reader.UpdatePipeline()
raw = list(leaves(servermanager.Fetch(reader)).values())[0]
copies.BladeRows = ["", "36", "2"]
copies.UpdatePipeline()
raw_copy = leaves(servermanager.Fetch(copies))["/Boundaries/%s/Copy 1" % first_boundary]

for name in [raw.GetCellData().GetArrayName(index)
             for index in range(raw.GetCellData().GetNumberOfArrays())]:
    array = raw.GetCellData().GetArray(name)
    if array is None or array.GetNumberOfComponents() != 3:
        continue
    source_values = numpy_support.vtk_to_numpy(array)
    moved = worst(numpy_support.vtk_to_numpy(raw_copy.GetCellData().GetArray(name)),
                  rotated(source_values, pitch))
    print("Cell array %s rotated with the mesh: worst component off by %.2e"
          % (name, moved))
    if moved > TOLERANCE:
        failures.append("the cell array %s was not rotated (off by %.3e)" % (name, moved))

if raw_copy.GetCellData().GetArray("CopyId") is None:
    failures.append("CopyId did not land on the cells of a cell-data mesh")
elif raw_copy.GetPointData().GetArray("CopyId") is not None:
    failures.append("CopyId landed on both attributes of a cell-data mesh")
else:
    print("CopyId lands on the cells when the arrays are cell arrays.")
reader.CellDatatoPointData = 1
reader.UpdatePipeline()

# ------------------------------------------------- arrays that are not vectors
# A three-component array of integers cannot hold a rotated vector, and turning
# one wraps it around its own type instead, so it has to be left alone even
# with Rotate Vectors on.

marker = pvs.Sphere(Center=[0.0, 1.0, 0.0], Radius=0.2)
marker.UpdatePipeline()
grid = servermanager.Fetch(marker)
colors = numpy_support.numpy_to_vtk(
    np.tile(np.array([10, 20, 30], dtype=np.uint8), (grid.GetNumberOfPoints(), 1)),
    deep=1)
colors.SetName("Colors")
grid.GetPointData().AddArray(colors)
before_normals = numpy_support.vtk_to_numpy(grid.GetPointData().GetNormals()).copy()

quarter = circumferential_copy.rotation([0.0, 0.0, 0.0],
                                        np.array([1.0, 0.0, 0.0]), 90.0)
turned = passage_copies.transformed(grid, quarter, True)
kept = numpy_support.vtk_to_numpy(turned.GetPointData().GetArray("Colors"))
normals = numpy_support.vtk_to_numpy(turned.GetPointData().GetNormals())
print("A uint8 triple through a 90 degree turn stays %s; the float normals turn."
      % (tuple(kept[0]),))
if not (kept == np.array([10, 20, 30], dtype=np.uint8)).all():
    failures.append("the integer array Colors was rotated: %s" % (tuple(kept[0]),))
if not (numpy_support.vtk_to_numpy(grid.GetPointData().GetArray("Colors"))
        == np.array([10, 20, 30], dtype=np.uint8)).all():
    failures.append("the filter modified the arrays of its own input")
if worst(normals, rotated(before_normals, 90.0)) > TOLERANCE:
    failures.append("the float normals were not rotated alongside")

# ------------------------------------------------------------ copies of copies
# A sector of two, then three of those: the second round must not label over the
# first, or the inner copy's identity is lost.

sector = pvs.CircumferentialCopy(Input=marker, RotationAxis=2)
sector.BladeRows = ["", "12", "2"]              # 30 degrees apart, twice
wheel = pvs.CircumferentialCopy(Input=sector, RotationAxis=2)
wheel.BladeRows = ["", "3", "3"]                # 120 degrees apart, three times
wheel.UpdatePipeline()
inner = leaves(servermanager.Fetch(wheel))["/Copy 1/Copy 0"]
labels = {"CopyId": inner.GetPointData().GetArray("CopyId"),
          "CopyId2": inner.GetPointData().GetArray("CopyId2"),
          "CopyAngle": inner.GetFieldData().GetArray("CopyAngle"),
          "CopyAngle2": inner.GetFieldData().GetArray("CopyAngle2")}
print("The inner copy of a copy carries %s." %
      ", ".join("%s=%g" % (name, array.GetValue(0))
                for name, array in sorted(labels.items()) if array is not None))
if any(array is None for array in labels.values()):
    failures.append("copying a copy dropped one of %s" % sorted(labels))
else:
    if labels["CopyId"].GetValue(0) != 1 or labels["CopyAngle"].GetValue(0) != 30.0:
        failures.append("copying a copy overwrote the first round's labels")
    if labels["CopyId2"].GetValue(0) != 0 or labels["CopyAngle2"].GetValue(0) != 0.0:
        failures.append("the second round's labels are wrong")

# ------------------------------------------------------- a plain, flat input

sphere = pvs.Sphere(Center=[0.0, 1.0, 0.0], Radius=0.2)
plain = pvs.CircumferentialCopy(Input=sphere, RotationAxis=0)
plain.BladeRows = ["", "4", "4"]                # quarter turns, four of them
plain.UpdatePipeline()
plain_blocks = leaves(servermanager.Fetch(plain))
print("A vtkPolyData input gives %d blocks: %s" %
      (len(plain_blocks), ", ".join(sorted(plain_blocks))))
if sorted(plain_blocks) != ["/Copy %d" % index for index in range(4)]:
    failures.append("a non-composite input gave the blocks %s" % sorted(plain_blocks))
else:
    centre = points_of(plain_blocks["/Copy 1"]).mean(axis=0)
    if np.abs(centre - np.array([0.0, 0.0, 1.0])).max() > 1e-6:
        failures.append("a quarter turn put the sphere at %s, expected (0, 0, 1)" % centre)

# ------------------------------------------------------------------ the macro
# The toolbar button runs this file top to bottom on every press, so that is how
# it is tested: once on the reader, once more with a filter already in the
# pipeline to inherit from.

macro = (HERE / "macros" / "Circumferential Copy.py").read_text()
for existing in list(pvs.GetSources().values()):
    if existing.GetXMLName() == "CircumferentialCopy":
        pvs.Delete(existing)
reader.Boundaries = [first_boundary]
reader.UpdatePipeline()
pvs.SetActiveSource(reader)
exec(compile(macro, "Circumferential Copy.py", "exec"), {})
made = pvs.GetActiveSource()
print("The macro made %s and guessed the axis is %s." %
      (made.GetXMLName(), made.RotationAxis.GetData()))
if made.GetXMLName() != "CircumferentialCopy":
    failures.append("the macro did not leave a Circumferential Copy active")
elif periodic and made.RotationAxis.GetData() != "X":
    # Only worth asserting for a mesh the periodic check has just shown to be
    # a passage about X; any other file has no right answer to guess.
    failures.append("the macro guessed axis %s for a mesh about X" %
                    made.RotationAxis.GetData())

# A second press must pick up where the first left off rather than guess again.
made.RotationAxis = 2              # Z, which the guess would never choose here
made.BladeRows = ["", "17", "4"]
pvs.SetActiveSource(reader)
exec(compile(macro, "Circumferential Copy.py", "exec"), {})
inherited = pvs.GetActiveSource()
print("A second press inherits axis %s and the table %s." %
      (inherited.RotationAxis.GetData(), list(inherited.BladeRows)))
if inherited is made:
    failures.append("the macro reused the existing filter instead of making one")
elif (inherited.RotationAxis.GetData() != "Z"
        or list(inherited.BladeRows) != ["", "17", "4"]):
    failures.append("the macro did not inherit the settings of the previous copy")

if failures:
    raise SystemExit("ERROR: " + "; ".join(failures))
print("Circumferential copy verified for %s." % cgns_file.name)
