"""Headless verification for the pitch a case is copied at.

``input.au3x`` says how a passage repeats -- ``cosystem`` whether there is an
axis to turn about, ``assembly-sectors`` or ``pitch`` how far apart the copies go
-- so the toolbar button reads it rather than asking. This checks the reading,
the Linear Copy the cartesian half of it needs, and the button that puts the two
together:

    pvpython verify_passage_copies.py [file.cgns]

The Circumferential Copy has its own check in verify_circumferential_copy.py;
this one is about where the numbers in it come from.
"""

from pathlib import Path
import os
import re
import shutil
import sys
import tempfile

import numpy as np
import vtk
from paraview import servermanager
from paraview import simple as pvs
from vtkmodules.util import numpy_support


HERE = Path(__file__).resolve().parent
TOLERANCE = 1e-6
# Optional reference runs. Environment variables select local copies without
# putting machine-specific paths in this file. Missing cases are reported.
CASES = Path(os.environ.get(
    "PARAVIEW_DIY_CASES_DIR", HERE / "TestCase" / "veclys"
)).expanduser()
STAGE = Path(os.environ.get(
    "PARAVIEW_DIY_STAGE_CASE", CASES / "stage" / "input.au3x"
)).expanduser()
WHEEL = Path(os.environ.get(
    "PARAVIEW_DIY_WHEEL_CASE", CASES / "wheel" / "input.au3x"
)).expanduser()
CASCADE = Path(os.environ.get(
    "PARAVIEW_DIY_CASCADE_CASE", CASES / "cascade" / "input.au3x"
)).expanduser()
DUCT = Path(os.environ.get(
    "PARAVIEW_DIY_DUCT_CASE", CASES / "duct" / "input.au3x"
)).expanduser()

sys.path.insert(0, str(HERE / "pylib"))
sys.path.insert(0, str(HERE / "plugins"))
for plugin in ("cgns_labeled_boundaries.py", "circumferential_copy.py",
               "linear_copy.py", "veclys_reader.py"):
    pvs.LoadPlugin(str(HERE / "plugins" / plugin), remote=False, ns=vars(pvs))

import circumferential_copy                                     # noqa: E402
import veclys_case                                              # noqa: E402

MACRO = (HERE / "macros" / "Circumferential Copy.py").read_text()
failures = []


def leaves(node, path=""):
    """Every dataset in a composite output, by its block path."""
    found = {}
    if node is None:
        return found
    if node.IsA("vtkCompositeDataSet"):
        for index in range(node.GetNumberOfBlocks()):
            metadata = node.GetMetaData(index)
            name = (metadata.Get(vtk.vtkCompositeDataSet.NAME())
                    if metadata and metadata.Has(vtk.vtkCompositeDataSet.NAME())
                    else str(index))
            name = re.sub(r"^Block \d+_", "", name) if name else name
            found.update(leaves(node.GetBlock(index), "%s/%s" % (path, name)))
    else:
        found[path] = node
    return found


def points_of(block):
    return numpy_support.vtk_to_numpy(block.GetPoints().GetData())


def setting(source, name):
    """What a property is set to, unwrapped."""
    held = getattr(source, name)
    return held.GetData() if hasattr(held, "GetData") else held


def press(source):
    """The toolbar button, run the way ParaView runs it: the file, top to
    bottom, on every press."""
    for existing in list(pvs.GetSources().values()):
        if existing.GetXMLName() in ("CircumferentialCopy", "LinearCopy"):
            pvs.Delete(existing)
    pvs.SetActiveSource(source)
    exec(compile(MACRO, "Circumferential Copy.py", "exec"), {})
    return pvs.GetActiveSource()


def blade_rows(source):
    """The Blade Rows table read back as [(name, apart, copies)]."""
    flat = list(setting(source, "BladeRows"))
    return [tuple(flat[index:index + 3]) for index in range(0, len(flat), 3)]


def copies_by_row(source):
    """{top-level block name: how many copies it was repeated into}.

    Counted by the distinct Copy names rather than by the leaves, since a row
    that holds a volume and six boundaries has seven leaves per copy.
    """
    found = {}
    for path in leaves(servermanager.Fetch(source)):
        parts = path.split("/")
        found.setdefault(parts[1], set()).add(parts[-1])
    return {row: len(names) for row, names in found.items()}


# ------------------------------------------------------------ the case file
# The coordinate system has to be read before the pitch, because a cartesian
# section keeps an assembly-sectors that means nothing: half_cylinder is a
# straight duct carrying assembly-sectors=12, and taking that at face value
# would give a 30 degree pitch to a mesh with no axis in it.

expected = {
    STAGE: [("IGV", "annular", 11.25), ("R1", "annular", 360.0 / 28),
            ("S1", "annular", 7.2)],
    WHEEL: [("r37", "annular", 10.0)],
    CASCADE: [("eleven", "cartesian", 0.05655)],
    DUCT: [("duct", "cartesian", None)],
}
for case_file, wanted in expected.items():
    if not case_file.exists():
        print("No %s on this machine; that case is not read." % case_file)
        continue
    rows = veclys_case.case_rows(str(case_file), "0000")
    print("%s\n  %s" % (case_file, "\n  ".join(row.describe() for row in rows)))
    got = [(row.name, row.cosystem, row.pitch) for row in rows]
    for (name, system, pitch), row in zip(wanted, got):
        if row[0] != name or row[1] != system:
            failures.append("%s: %s came out as %s" % (case_file, name, row[:2]))
        elif pitch is None:
            if row[2] is not None:
                failures.append("%s: %s has no pitch in the file but %g came out"
                                % (case_file, name, row[2]))
        elif row[2] is None or abs(row[2] - pitch) > 1e-9:
            failures.append("%s: %s has a pitch of %s, expected %g"
                            % (case_file, name, row[2], pitch))
    if len(got) != len(wanted):
        failures.append("%s holds %d rows, expected %d" % (case_file, len(got), len(wanted)))

# A mesh holding more than one passage: grid-sectors above one has never been
# run, so it is written here rather than found. The pitch is then 360 * held /
# blades, and the number the table carries is what gives that pitch back --
# rounding it to a whole blade count would quietly copy at the wrong pitch.
held = Path(tempfile.mkdtemp(prefix="veclys-"))
try:
    (held / "input.au3x").write_text(
        "[devicelist]\ndevice = empty:m-dom:wide\n\n"
        "[wide]\ncosystem=annular\ngrid-sectors=2\nassembly-sectors=32\n")
    wide = veclys_case.case_rows(str(held / "input.au3x"), "0000")[0]
    written = "%d" % round(360.0 / wide.pitch)
    print("%s\n  and the table would carry %s, which is a pitch of %.6g"
          % (wide.describe(), written,
             circumferential_copy.pitch_of_blades(float(written))))
    if abs(wide.pitch - 22.5) > 1e-9:
        failures.append("a mesh of 2 passages in 32 has a pitch of %s, expected "
                        "22.5" % wide.pitch)
    if abs(circumferential_copy.pitch_of_blades(float(written)) - wide.pitch) > 1e-9:
        failures.append("the table would carry %s, which is not the row's pitch "
                        "of %g" % (written, wide.pitch))
finally:
    shutil.rmtree(str(held), ignore_errors=True)

# A cartesian row must not offer a blade count at all, whatever its section says.
if DUCT.exists():
    duct = veclys_case.case_rows(str(DUCT), "0000")[0]
    print("The duct's section says assembly-sectors=%s, and the row offers "
          "blades=%s, pitch=%s." % (duct.settings.get("assembly-sectors"),
                                    duct.blades, duct.pitch))
    if duct.settings.get("assembly-sectors") != "12":
        failures.append("the duct case no longer carries the stale assembly-sectors "
                        "this is here to check")
    if duct.blades is not None:
        failures.append("a cartesian row offered a blade count of %s" % duct.blades)

# ------------------------------------------------------ the linear copy itself

sphere = pvs.Sphere(Center=[0.0, 1.0, 0.0], Radius=0.2)
sphere.UpdatePipeline()
before = leaves(servermanager.Fetch(sphere))[""]
normals = numpy_support.vtk_to_numpy(before.GetPointData().GetNormals()).copy()

slid = pvs.LinearCopy(Input=sphere, TranslationDirection=[0.0, 3.0, 0.0])
slid.BladeRows = ["", "0.5", "3"]      # the line every other block goes by
slid.UpdatePipeline()
blocks = leaves(servermanager.Fetch(slid))
centres = [points_of(blocks["/Copy %d" % index]).mean(axis=0) for index in range(3)]
print("Copies of a sphere at y=1, a pitch of 0.5 along (0, 3, 0): y = %s"
      % ", ".join("%.4f" % centre[1] for centre in centres))
if sorted(blocks) != ["/Copy %d" % index for index in range(3)]:
    failures.append("a plain input gave the blocks %s" % sorted(blocks))
for index, centre in enumerate(centres):
    if np.abs(centre - np.array([0.0, 1.0 + 0.5 * index, 0.0])).max() > TOLERANCE:
        failures.append("copy %d of the sphere is centred at %s" % (index, centre))
    offset = blocks["/Copy %d" % index].GetFieldData().GetArray("CopyOffset")
    if offset is None or abs(offset.GetValue(0) - 0.5 * index) > 1e-9:
        failures.append("copy %d does not carry a CopyOffset of %g" % (index, 0.5 * index))
    ids = numpy_support.vtk_to_numpy(
        blocks["/Copy %d" % index].GetPointData().GetArray("CopyId"))
    if ids.min() != index or ids.max() != index:
        failures.append("copy %d does not carry a constant CopyId" % index)

# A translation cannot change a direction, so every vector has to come through
# a copy exactly as it went in -- normals included.
moved = numpy_support.vtk_to_numpy(blocks["/Copy 2"].GetPointData().GetNormals())
if np.abs(moved - normals).max() > TOLERANCE:
    failures.append("the normals were changed by a translation")
else:
    print("The normals come through a translation untouched.")

slid.BladeRows = ["", "-0.5", "3"]
slid.UpdatePipeline()
back = points_of(leaves(servermanager.Fetch(slid))["/Copy 1"]).mean(axis=0)
print("A negative pitch puts copy 1 at y = %.4f." % back[1])
if abs(back[1] - 0.5) > TOLERANCE:
    failures.append("a negative pitch did not copy the other way")

# ------------------------------------------- a pitch and a count per row, slid

cgns_file = Path(sys.argv[1] if len(sys.argv) > 1 else HERE / "r37.cgns").resolve()
reader = pvs.CGNSLabeledBoundaries(FileName=str(cgns_file))
reader.Volumes = []
reader.UpdatePipeline()
sections = list(reader.Boundaries)[:2]
reader.Boundaries = sections
reader.UpdatePipeline()
rows = pvs.LinearCopy(Input=reader, TranslationDirection=[1.0, 0.0, 0.0])
rows.BladeRows = ["", "0.01", "3", sections[0], "0.05", "2"]
rows.UpdatePipeline()
counted = {}
for path in leaves(servermanager.Fetch(rows)):
    counted[path.split("/")[-2]] = counted.get(path.split("/")[-2], 0) + 1
print("With one line of the table, %s" %
      ", ".join("%s is repeated %d times" % pair for pair in sorted(counted.items())))
if counted.get(sections[0]) != 2:
    failures.append("the section the table names got %s copies, expected 2"
                    % counted.get(sections[0]))
if len(sections) > 1 and counted.get(sections[1]) != 3:
    failures.append("a section the table does not name got %s copies, expected 3"
                    % counted.get(sections[1]))
moved = leaves(servermanager.Fetch(rows))["/Boundaries/%s/Copy 1" % sections[0]]
offset = moved.GetFieldData().GetArray("CopyOffset")
if offset is None or abs(offset.GetValue(0) - 0.05) > 1e-12:
    failures.append("the named section slid by %s, not by its own 0.05"
                    % (offset and offset.GetValue(0)))
else:
    print("A row the table names slides by its own 0.05 rather than the "
          "panel's 0.01.")

# --------------------------------------------------------- the button, per case
# The button is what reads the case, so each kind of case is pressed on.

if WHEEL.exists():
    wheel = pvs.VeclysReader(FileName=str(WHEEL))
    wheel.UpdatePipelineInformation()
    made = press(wheel)
    print("A single annular row gives a %s, table %s."
          % (made.GetXMLName(), blade_rows(made)))
    if made.GetXMLName() != "CircumferentialCopy":
        failures.append("an annular case made a %s" % made.GetXMLName())
    elif blade_rows(made) != [("r37", "36", "4")]:
        failures.append("the r37 case came out as %s" % blade_rows(made))
    else:
        # Full Annulus is the tick that saves working the count out, and is
        # left for the user to make rather than pressed on their behalf.
        if setting(made, "FullAnnulus"):
            failures.append("the button closed the annulus without being asked")
        made.FullAnnulus = 1
        made.UpdatePipeline()
        closed = copies_by_row(made)
        print("  with Full Annulus ticked: %s" % closed)
        if set(closed.values()) != {36}:
            failures.append("Full Annulus of a 36-blade row gave %s" % closed)
        made.FullAnnulus = 0
else:
    print("No %s on this machine; the single annular row is not pressed on." % WHEEL)

if STAGE.exists():
    stage = pvs.VeclysReader(FileName=str(STAGE))
    stage.UpdatePipelineInformation()
    stage.Volumes = []
    stage.Boundaries = [name for name in stage.Boundaries if name.endswith("BLADE")]
    stage.UpdatePipeline()
    made = press(stage)

    # A stage stays one entry in the Pipeline Browser. The rows have their own
    # blade counts, so they have their own pitches and their own numbers of
    # copies, and both go in the panel a line per row -- with the name already
    # filled in, so it is never something to type.
    rows = blade_rows(made)
    print("A three-row stage gives one %s, with a table of %d lines: %s"
          % (made.GetXMLName(), len(rows),
             "; ".join(" ".join(line) for line in rows)))
    left = [proxy for proxy in pvs.GetSources().values()
            if proxy.GetXMLName() in ("CircumferentialCopy", "LinearCopy")]
    if made.GetXMLName() != "CircumferentialCopy":
        failures.append("the E3 stage made a %s" % made.GetXMLName())
    elif len(left) != 1:
        failures.append("the stage left %d filters, expected the one" % len(left))
    else:
        # Four copies of the widest-pitched row, and as many of the others as
        # it takes each to reach the same place.
        if rows != [("IGV", "32", "5"), ("R1", "28", "4"), ("S1", "50", "7")]:
            failures.append("the stage's table came out as %s" % rows)
        made.UpdatePipeline()
        counted = copies_by_row(made)
        print("  as it stands: %s" % counted)
        if counted != {"IGV": 5, "R1": 4, "S1": 7}:
            failures.append("the stage came out as %s copies" % counted)

        # Each row turns by its own pitch, whatever it is repeated.
        turned = leaves(servermanager.Fetch(made))
        for row, pitch in (("IGV", 11.25), ("R1", 360.0 / 28), ("S1", 7.2)):
            step = [block.GetFieldData().GetArray("CopyAngle").GetValue(0)
                    for path, block in turned.items()
                    if path.startswith("/%s/" % row) and path.endswith("Copy 1")]
            if not step or abs(step[0] - pitch) > 1e-9:
                failures.append("%s was copied by %s, not by its own %g"
                                % (row, step, pitch))
        print("  each row turns by its own pitch.")

        # One tick closes the wheel for all three at once, each at its own
        # blade count, which is the number nobody should have to work out.
        made.FullAnnulus = 1
        made.UpdatePipeline()
        wheel = copies_by_row(made)
        print("  with Full Annulus ticked: %s" % wheel)
        if wheel != {"IGV": 32, "R1": 28, "S1": 50}:
            failures.append("a whole wheel came out as %s" % wheel)

else:
    print("No %s on this machine; the stage is not pressed on." % STAGE)

if CASCADE.exists():
    cascade = pvs.VeclysReader(FileName=str(CASCADE))
    cascade.UpdatePipelineInformation()
    made = press(cascade)
    print("A cartesian case gives a %s: table %s along %s."
          % (made.GetXMLName(), blade_rows(made),
             setting(made, "TranslationDirection")))
    if made.GetXMLName() != "LinearCopy":
        failures.append("a cartesian case made a %s, which has no axis to turn "
                        "about" % made.GetXMLName())
    else:
        # A cascade has no wheel to close, so the tick that closes one must not
        # be there to press at all.
        offered = made.SMProxy.GetProperty("FullAnnulus")
        print("  Full Annulus on a cartesian case: %s"
              % ("offered" if offered else "not there to press"))
        if offered is not None:
            failures.append("a cartesian case offers Full Annulus")
        if blade_rows(made) != [("eleven", "0.05655", "4")]:
            failures.append("the cascade came out as %s" % blade_rows(made))
        direction = np.asarray(setting(made, "TranslationDirection"), dtype=float)
        if np.abs(direction / np.linalg.norm(direction)
                  - np.array([0.0, -1.0, 0.0])).max() > 1e-6:
            failures.append("the cascade's direction came out as %s" % direction)

        # The pitch and the direction together have to carry the periodic patch
        # onto its partner, which is what says they are both right.
        mesh = veclys_case.case_rows(str(CASCADE), "0000")[0].mesh
        patches = pvs.CGNSLabeledBoundaries(FileName=mesh)
        patches.UpdatePipelineInformation()
        patches.Volumes = []
        patches.Boundaries = ["LEFT_quad", "RIGHT_quad"]
        patches.UpdatePipeline()
        held = {path.rsplit("/", 1)[-1]: block
                for path, block in leaves(servermanager.Fetch(patches)).items()}
        patches.Boundaries = ["LEFT_quad"]
        patches.UpdatePipeline()
        one = pvs.LinearCopy(Input=patches)
        one.TranslationDirection = setting(made, "TranslationDirection")
        one.BladeRows = ["", blade_rows(made)[0][1], "2"]
        one.UpdatePipeline()
        slid_left = leaves(servermanager.Fetch(one))["/Boundaries/LEFT_quad/Copy 1"]
        from scipy.spatial import cKDTree
        gap = cKDTree(points_of(held["RIGHT_quad"])).query(points_of(slid_left))[0].max()
        size = np.ptp(points_of(held["RIGHT_quad"]), axis=0).max()
        print("  one pitch takes LEFT_quad onto RIGHT_quad: worst point %.2e off, "
              "the patch is %.4f across." % (gap, size))
        if gap > 1e-6 * size:
            failures.append("the case's pitch does not carry LEFT_quad onto "
                            "RIGHT_quad (worst gap %.3e)" % gap)
        pvs.Delete(one)
        pvs.Delete(patches)
else:
    print("No %s on this machine; the cascade is not pressed on." % CASCADE)

if DUCT.exists():
    duct_case = pvs.VeclysReader(FileName=str(DUCT))
    duct_case.UpdatePipelineInformation()
    made = press(duct_case)
    print("A cartesian case with nothing to go on gives a %s left at its "
          "defaults: table %s." % (made.GetXMLName(), blade_rows(made)))
    if made.GetXMLName() != "LinearCopy":
        failures.append("a cartesian duct made a %s" % made.GetXMLName())
    # inv_left_quad and inv_right_quad are walls, not a periodic pair, so
    # nothing must have been measured off them.
    elif blade_rows(made) != [("", "1", "4")]:
        failures.append("a pitch was invented for a case that gives none and a "
                        "mesh with no periodic pair: %s" % blade_rows(made))
else:
    print("No %s on this machine; the case with no pitch is not pressed on." % DUCT)

if failures:
    raise SystemExit("ERROR: " + "; ".join(failures))
print("Passage copies verified.")
