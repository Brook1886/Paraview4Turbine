"""Headless verification for the automatic point-data coloring.

    pvbatch --force-offscreen-rendering verify_auto_color.py [file.cgns [file.cgns]]

Checks that a CGNS labeled-boundary source is colored by a point array without
anyone asking, that a VECLYS case is too, that nothing else in the pipeline is
touched, that a manual coloring choice is never overridden, and that a second
file widens the shared color map instead of replacing its range.
"""

from pathlib import Path
import os
import runpy
import sys

from paraview.simple import (ColorBy, GetActiveViewOrCreate, GetColorTransferFunction,
                             GetSettingsProxy, LoadPlugin, Render, Show, Sphere)


project_dir = Path(__file__).resolve().parent
arguments = [Path(name).resolve() for name in sys.argv[1:]]
cgns_files = arguments or [project_dir / "r37.cgns", project_dir / "bfs.cgns"]

VECLYS_CASE = Path(os.environ.get(
    "PARAVIEW_DIY_COLOR_CASE", project_dir / "TestCase" / "veclys" / "wheel" / "input.au3x"
)).expanduser()

for plugin in ("cgns_labeled_boundaries.py", "veclys_reader.py"):
    LoadPlugin(str(project_dir / "plugins" / plugin), remote=False, ns=globals())

view = GetActiveViewOrCreate("RenderView")
view.ViewSize = [400, 300]
runpy.run_path(str(project_dir / "auto_color_cgns.py"),
               run_name="__paraview_diy_startup__")

failures = []


def color_map_range(array_name):
    points = GetColorTransferFunction(array_name).RGBPoints
    values = [points[index] for index in range(0, len(points), 4)]
    return min(values), max(values)


representations = []
expected_range = None
array_name = None
for cgns_file in cgns_files:
    reader = CGNSLabeledBoundaries(registrationName=cgns_file.stem,
                                   FileName=str(cgns_file))
    reader.UpdatePipeline()
    representation = Show(reader, view)
    if list(representation.ColorArrayName) != [None, ""]:
        failures.append("%s was colored before the first render" % cgns_file.name)
    Render(view)

    association, name = representation.ColorArrayName
    print("%-10s colored by %s %s" % (cgns_file.name, association, name))
    if association != "POINTS" or not name:
        failures.append("%s was not colored by a point array automatically" % cgns_file.name)
        continue
    if not representation.IsScalarBarVisible(view):
        failures.append("%s has no scalar bar" % cgns_file.name)

    array_name = name
    array_info = reader.GetPointDataInformation().GetArray(name)
    component = -1 if array_info.GetNumberOfComponents() > 1 else 0
    low, high = array_info.GetComponentRange(component)
    expected_range = ((low, high) if expected_range is None
                      else (min(low, expected_range[0]), max(high, expected_range[1])))
    representations.append((cgns_file.name, representation))

# The shared color map must cover every file, not just the last one loaded.
if array_name is not None:
    actual_range = color_map_range(array_name)
    print("%s color map covers %.1f .. %.1f (expected %.1f .. %.1f)" %
          ((array_name,) + actual_range + expected_range))
    for actual, wanted in zip(actual_range, expected_range):
        if abs(actual - wanted) > 1e-6 * max(1.0, abs(wanted)):
            failures.append("the %s color map does not cover every file" % array_name)
            break

# ParaView colors multiblock sources by vtkBlockColors on Apply and leaves that
# color bar behind; it must be gone.
from paraview import servermanager
settings = GetSettingsProxy("GeneralSettings")
print("ColorByBlockColorsOnApply = %s" % settings.ColorByBlockColorsOnApply)
if settings.ColorByBlockColorsOnApply:
    failures.append("ParaView will still color by vtkBlockColors on Apply")
proxies = servermanager.ProxyManager().GetProxiesInGroup("scalar_bars")
for (_group, _name), proxy in proxies.items():
    title = proxy.GetProperty("Title").GetElement(0)
    visible = proxy.GetProperty("Visibility").GetElement(0)
    print("  scalar bar %r visible=%s" % (title, visible))
    if title == "vtkBlockColors" and visible:
        failures.append("the vtkBlockColors color bar is still shown")

# Anything that is not one of these readers must be left alone.
# A case opened by its input.au3x is the same mesh and has to be colored too;
# the coloring knows both readers by name, so a new one is easy to forget.
if VECLYS_CASE.exists():
    case = VeclysReader(registrationName="case", FileName=str(VECLYS_CASE))
    case.UpdatePipeline()
    case_representation = Show(case, view)
    Render(view)
    association, name = case_representation.ColorArrayName
    print("%-10s colored by %s %s" % (VECLYS_CASE.name, association, name))
    if association != "POINTS" or not name:
        failures.append("a VECLYS case was not colored automatically")
else:
    print("No VECLYS case at %s; that reader is not checked here." % VECLYS_CASE)

sphere_representation = Show(Sphere(), view)
Render(view)
print("Sphere colored by %s" % (list(sphere_representation.ColorArrayName),))
if list(sphere_representation.ColorArrayName) != [None, ""]:
    failures.append("an unrelated source was recolored")

# A deliberate choice must survive every later render.
if representations:
    name, representation = representations[0]
    ColorBy(representation, ("POINTS", "BoundaryId"))
    Render(view)
    Render(view)
    print("%s after a manual change: %s" % (name, list(representation.ColorArrayName)))
    if list(representation.ColorArrayName) != ["POINTS", "BoundaryId"]:
        failures.append("a manual coloring choice was overridden")

if failures:
    raise SystemExit("ERROR: " + "; ".join(failures))
print("Automatic point-data coloring verified for %s" %
      ", ".join(path.name for path in cgns_files))
