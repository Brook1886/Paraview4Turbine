"""Headless verification for the VECLYS case reader.

The reader's job is to turn an input.au3x into the CGNS files a run wrote, one
per blade row, so that is what is checked: the paths it works out against ones
derived here by other means, and the meshes it hands over against the same files
opened directly with the CGNS reader.

    pvpython verify_veclys_reader.py [/path/to/input.au3x ...]
"""

from pathlib import Path
import os
import re
import shutil
import sys
import tempfile

import vtk
from paraview import servermanager
from paraview import simple as pvs


HERE = Path(__file__).resolve().parent
CASES = Path(os.environ.get(
    "PARAVIEW_DIY_CASES_DIR", HERE / "TestCase" / "veclys"
)).expanduser()
STAGE = Path(os.environ.get(
    "PARAVIEW_DIY_STAGE_CASE", CASES / "stage" / "input.au3x"
)).expanduser()

sys.path.insert(0, str(HERE / "plugins"))
import veclys_reader                                            # noqa: E402

for plugin in ("cgns_labeled_boundaries.py", "veclys_reader.py"):
    pvs.LoadPlugin(str(HERE / "plugins" / plugin), remote=False, ns=vars(pvs))

failures = []


def blocks(node, path=""):
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
            found.update(blocks(node.GetBlock(index), "%s/%s" % (path, name)))
    else:
        found[path] = node.GetNumberOfCells()
    return found


def offered(source, name):
    """What the panel would list, which is the information property."""
    info = source.GetProperty(name + "Info")
    return [info.GetElement(index)
            for index in range(0, info.GetNumberOfElements(), 2)]


def meshes_by_hand(case_file, case_number):
    """The CGNS paths, worked out from the file with a regex rather than with
    the reader's own parser, so the two can disagree."""
    text = Path(case_file).read_text()
    chains = re.search(r"^\s*device\s*=\s*(.+)$", text, re.M).group(1).strip()
    found = []
    for chain in chains.split(","):
        names = [part.split(":")[-1].strip() for part in chain.split("-")]
        found.append((names[-1], Path(case_file).parent.joinpath(
            *names, case_number, names[-1] + ".cgns")))
    return found


# ------------------------------------------------------------------- the parser
# The device list is xcode's, so the shapes it can take are checked directly.

parsed = veclys_reader.device_chains("empty:rotor37-dom:r37")
print("empty:rotor37-dom:r37 -> %s" % parsed)
if parsed != [["rotor37", "r37"]]:
    failures.append("the device chain came out as %s" % parsed)
parsed = veclys_reader.device_chains("empty:e3-dom:IGV,empty:e3-dom:R1,empty:e3-dom:S1")
print("a stage of three rows -> %s" % parsed)
if parsed != [["e3", "IGV"], ["e3", "R1"], ["e3", "S1"]]:
    failures.append("a stage came out as %s" % parsed)
parsed = veclys_reader.device_chains(" empty:a-dom:b , plain , x:y-z:w-v:u ")
print("three chains, one of them typeless -> %s" % parsed)
if parsed != [["a", "b"], ["plain"], ["y", "w", "u"]]:
    failures.append("a device list of three chains came out as %s" % parsed)

# ------------------------------------------------------------------- the cases

given = sys.argv[1:] or sorted(str(path) for path in CASES.glob("*/input.au3x"))
if STAGE.exists() and not sys.argv[1:]:
    if str(STAGE) not in given:
        given.append(str(STAGE))
if not given:
    raise SystemExit("ERROR: no input.au3x to read; give one as an argument.")

last = None
for case_file in given:
    reader = pvs.VeclysReader(FileName=case_file)
    reader.UpdatePipelineInformation()
    reader.UpdatePipeline()
    wanted = meshes_by_hand(case_file, "0000")
    tree = blocks(servermanager.Fetch(reader))
    rows = [name for name, _path in wanted]
    print("%s\n  %d row(s): %s, %d blocks, %d cells"
          % (case_file, len(rows), ", ".join(rows), len(tree),
             reader.GetDataInformation().GetNumberOfCells()))

    if list(reader.Domains) != rows:
        failures.append("%s offered the rows %s, expected %s" %
                        (case_file, list(reader.Domains), rows))

    for name, mesh in wanted:
        print("  %-4s -> %s" % (name, mesh))
        if not mesh.exists():
            failures.append("%s does not exist" % mesh)
        # Every row is a block of its own, whatever the row is called.
        if not [path for path in tree if path.startswith("/%s/" % name)]:
            failures.append("%s produced no blocks of its own" % name)
    last = (case_file, wanted, tree, reader)

case_file, wanted, tree, reader = last

# ------------------------------------------------ the same files, read directly
# Anything the case reader adds or loses would show up here.

for name, mesh in wanted:
    direct = pvs.CGNSLabeledBoundaries(FileName=str(mesh))
    direct.UpdatePipeline()
    straight = {"/%s%s" % (name, path): cells
                for path, cells in blocks(servermanager.Fetch(direct)).items()}
    mine = {path: cells for path, cells in tree.items()
            if path.startswith("/%s/" % name)}
    print("%s opened directly: %d blocks, and the case agrees: %s"
          % (mesh.name, len(straight), straight == mine))
    if straight != mine:
        failures.append("%s gives %s but the case gives %s" % (mesh.name, straight, mine))
    pvs.Delete(direct)

# ------------------------------------------------------- one row out of several

if len(wanted) > 1:
    keep = wanted[1][0]
    before = offered(reader, "Boundaries")
    reader.Domains = [keep]
    reader.UpdatePipelineInformation()
    reader.UpdatePipeline()
    after = offered(reader, "Boundaries")
    rows_left = {path.split("/")[1] for path in blocks(servermanager.Fetch(reader))}
    print("With only %s ticked: rows %s, %d boundaries offered, still named %s"
          % (keep, sorted(rows_left), len(after), after[0]))
    if rows_left != {keep}:
        failures.append("unticking rows left %s in the output" % sorted(rows_left))
    if not after or any(not name.startswith(keep + "/") for name in after):
        failures.append("the boundaries left over are %s" % after)
    # The names must not shuffle about when a row is unticked, or the ticks
    # against them would be lost.
    if [name for name in after if name not in before]:
        failures.append("unticking a row renamed the others: %s -> %s" % (before, after))

    reader.Boundaries = [after[0]]
    reader.Volumes = []
    reader.UpdatePipeline()
    kept = blocks(servermanager.Fetch(reader))
    print("With one of those boundaries ticked and no volume: %s" % sorted(kept))
    if len(kept) != 1:
        failures.append("unticking did not reach the reader inside: %s" % sorted(kept))
else:
    print("Only one row in %s; the row checkboxes are not exercised." % case_file)

# ------------------------------------------------------------- a case not there

reader.CaseNumber = "0007"
reader.UpdatePipelineInformation()
reader.UpdatePipeline()
missing = blocks(servermanager.Fetch(reader))
print("Case 0007 of the same run: %d blocks, and it said so rather than failing"
      % len(missing))
if missing:
    failures.append("a case that was never run produced %s" % sorted(missing))

# ------------------------------------------------- a run that finishes later
# Opening a case before its mesh is written must not settle into nothing: the
# reader has to look again when asked, since the panel has not changed and
# ParaView would otherwise never re-run it.

first_case = Path(given[0])
root = meshes_by_hand(str(first_case), "0000")[0][1].relative_to(first_case.parent).parts[0]
waiting = Path(tempfile.mkdtemp(prefix="veclys-"))
try:
    shutil.copy(str(first_case), str(waiting / "input.au3x"))
    early = pvs.VeclysReader(FileName=str(waiting / "input.au3x"))
    early.UpdatePipelineInformation()
    early.UpdatePipeline()
    empty = blocks(servermanager.Fetch(early))
    (waiting / root).symlink_to(first_case.parent / root)
    early.UpdatePipeline()
    still = blocks(servermanager.Fetch(early))
    early.SMProxy.InvokeCommand("Reload")
    early.UpdatePipelineInformation()
    early.UpdatePipeline()
    after = blocks(servermanager.Fetch(early))
    print("Opened before the mesh was written: %d blocks, %d once it appears, "
          "%d after Reload" % (len(empty), len(still), len(after)))
    if empty:
        failures.append("a case with no mesh yet produced %s" % sorted(empty))
    if not after:
        failures.append("Reload did not pick up the mesh the run had written")
    if early.SMProxy.GetProperty("Reload") is None:
        failures.append("there is no Reload button to press")
finally:
    pvs.Delete(early)
    shutil.rmtree(str(waiting), ignore_errors=True)

if failures:
    raise SystemExit("ERROR: " + "; ".join(failures))
print("VECLYS reader verified for %d case(s)." % len(given))
