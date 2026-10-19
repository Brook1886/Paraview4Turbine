"""Headless verification for the flow-property and slice-average filters.

The answers are known in advance: a solid-body rotation has a vorticity anyone
can write down, a circle has a circumference, and a uniform stream through a
known area carries a mass flow that is a multiplication. The CGNS files are used
for the one check arithmetic cannot make, which is whether the numbers are the
ones a compressor really has.

    pvbatch --force-offscreen-rendering verify_flow_tools.py
"""

from pathlib import Path
import os
import shutil
import sys
import tempfile

import numpy as np
from paraview import servermanager
from paraview import simple as pvs
from vtkmodules.util import numpy_support
from vtkmodules.vtkCommonCore import vtkPoints
from vtkmodules.vtkCommonDataModel import (vtkCellArray, vtkLine, vtkPolyData,
                                           vtkTriangle)


HERE = Path(__file__).resolve().parent
TOLERANCE = 1e-6

# paraview_diy.sh puts this on PYTHONPATH; the macros are run here as ParaView
# runs them, so they have to be able to import it.
sys.path.insert(0, str(HERE / "pylib"))
sys.path.insert(0, str(HERE / "plugins"))

# What the chart must never plot as a property: they are the axis itself and the
# curve length the averaging weighted by.
_NOT_SERIES = ("Radius", "Length", "Area")

for plugin in ("cgns_labeled_boundaries.py", "flow_properties.py",
               "slice_averages.py"):
    pvs.LoadPlugin(str(HERE / "plugins" / plugin), remote=False, ns=vars(pvs))

failures = []


def array_of(dataset, name):
    array = dataset.GetPointData().GetArray(name)
    return None if array is None else numpy_support.vtk_to_numpy(array)


def names_of(dataset):
    data = dataset.GetPointData()
    return [data.GetArrayName(index) for index in range(data.GetNumberOfArrays())]


def image_points(image):
    """The coordinates of a vtkImageData, which has no point array of its own."""
    origin, spacing, extent = image.GetOrigin(), image.GetSpacing(), image.GetExtent()
    axes = [origin[axis] + spacing[axis] * np.arange(extent[2 * axis],
                                                     extent[2 * axis + 1] + 1)
            for axis in range(3)]
    grid = np.meshgrid(axes[2], axes[1], axes[0], indexing="ij")
    return np.column_stack([grid[2].ravel(), grid[1].ravel(), grid[0].ravel()])


def table_of(source):
    table = servermanager.Fetch(source)
    columns = {}
    for index in range(table.GetNumberOfColumns()):
        column = table.GetColumn(index)
        if column.GetClassName() == "vtkStringArray":
            columns[table.GetColumnName(index)] = [
                column.GetValue(row) for row in range(column.GetNumberOfValues())]
        else:
            columns[table.GetColumnName(index)] = numpy_support.vtk_to_numpy(column)
    return columns


def close(got, expected, tolerance=TOLERANCE):
    scale = max(abs(float(expected)), 1.0)
    return abs(float(got) - float(expected)) <= tolerance * scale


# ============================================================ flow properties
# A solid body turning at 2 about Z, drifting at 3 along it. Every quantity the
# filter offers has a value that can be written down for such a flow.

RATE, DRIFT = 2.0, 3.0
box = pvs.Wavelet(WholeExtent=[-5, 5, -5, 5, -5, 5])
spun = pvs.Calculator(Input=box, ResultArrayName="V",
                      Function="(-%g*coordsY)*iHat + (%g*coordsX)*jHat + %g*kHat"
                               % (RATE, RATE, DRIFT))
dense = pvs.Calculator(Input=spun, ResultArrayName="Rho", Function="coordsX*coordsX")

properties = pvs.FlowProperties(Input=dense, Velocity="V", Density="Rho",
                                SchlierenExponent=5.0)
properties.Quantities = ["Q-Criterion", "Lambda2", "Vorticity",
                         "Vorticity Magnitude", "Helicity", "Divergence",
                         "Density Gradient Magnitude", "Numerical Schlieren"]
properties.UpdatePipeline()
solid = servermanager.Fetch(properties)

print("Flow properties of a solid-body rotation at %g with a drift of %g:"
      % (RATE, DRIFT))
expected = {"Vorticity": [0.0, 0.0, 2 * RATE],
            "VorticityMagnitude": 2 * RATE,
            "Divergence": 0.0,
            "Lambda2": -RATE ** 2,
            "Helicity": 2 * RATE * DRIFT,
            "Q": RATE ** 2}
for name, wanted in expected.items():
    values = array_of(solid, name)
    if values is None:
        failures.append("%s was not produced" % name)
        continue
    got = values.mean(axis=0)
    worst = float(np.abs(np.asarray(got) - np.asarray(wanted)).max())
    spread = float(np.abs(values - np.asarray(wanted)).max())
    print("  %-20s %-22s expected %s, worst point off by %.1e"
          % (name, np.round(got, 6), wanted, spread))
    if worst > TOLERANCE or spread > 1e-6 * max(1.0, abs(np.max(wanted))):
        failures.append("%s is %s, expected %s" % (name, got, wanted))

if properties.GetDataInformation().GetDataSetTypeAsString() != "vtkImageData":
    failures.append("the filter did not keep its input's type")

# The density gradient is exact wherever a central difference can be taken; the
# outermost layer of points is one-sided and is left out of the comparison.
gradient = array_of(solid, "DensityGradientMagnitude")
coordinates = image_points(solid)
inside = np.abs(coordinates[:, 0]) < 5.0
worst = float(np.abs(gradient[inside] - 2 * np.abs(coordinates[inside, 0])).max())
print("  density gradient of x^2 inside the boundary: worst off by %.1e" % worst)
if worst > 1e-9:
    failures.append("the density gradient is not 2|x| (off by %.3e)" % worst)

picture = array_of(solid, "Schlieren")
worst = float(np.abs(picture - np.exp(-5.0 * gradient / gradient.max())).max())
print("  schlieren is exp(-k |grad rho| / max): worst off by %.1e" % worst)
if worst > 1e-12:
    failures.append("the schlieren picture is not the expected exponential")

# Unticking has to remove things, and the gradient magnitude is only kept when
# it was asked for rather than merely needed by the schlieren picture.
properties.Quantities = ["Numerical Schlieren"]
properties.UpdatePipeline()
lean = names_of(servermanager.Fetch(properties))
print("  with only the schlieren picture ticked: %s" % lean)
if "Schlieren" not in lean:
    failures.append("the schlieren picture went missing when it was the only one")
for name in ("Q", "Lambda2", "Vorticity", "DensityGradientMagnitude"):
    if name in lean:
        failures.append("%s survived being unticked" % name)

# A missing array is reported, and does not stop the quantities that can be made.
properties.Quantities = ["Q-Criterion", "Numerical Schlieren"]
properties.Density = "NoSuchArray"
properties.UpdatePipeline()
partial = names_of(servermanager.Fetch(properties))
print("  with no density array: %s" % partial)
if "Q" not in partial:
    failures.append("a missing density array stopped the velocity quantities")
if "Schlieren" in partial:
    failures.append("a schlieren picture was made without a density array")

# The solid body cannot tell one convention from another, because its strain is
# zero. A general, asymmetric, compressible field can: every quantity below is
# worked out here from the gradient tensor the velocity was built from.

A = np.array([[0.3, -2.0, 0.5],
              [1.0, -0.7, 0.2],
              [0.4, 0.9, 0.6]])
linear = pvs.Wavelet(WholeExtent=[-3, 3, -3, 3, -3, 3])
terms = " + ".join("(%g*coordsX + %g*coordsY + %g*coordsZ)*%sHat"
                   % (A[row, 0], A[row, 1], A[row, 2], hat)
                   for row, hat in enumerate("ijk"))
sheared = pvs.Calculator(Input=linear, ResultArrayName="W", Function=terms)
general = pvs.FlowProperties(Input=sheared, Velocity="W")
general.Quantities = ["Q-Criterion", "Lambda2", "Vorticity", "Vorticity Magnitude",
                      "Helicity", "Divergence"]
general.UpdatePipeline()
mixed = servermanager.Fetch(general)

strain = 0.5 * (A + A.T)
spin = 0.5 * (A - A.T)
curl = np.array([A[2, 1] - A[1, 2], A[0, 2] - A[2, 0], A[1, 0] - A[0, 1]])
by_hand = {
    # Q is the same number whether it is read as half the difference of the two
    # norms or as -tr(G^2)/2; both are checked, so a factor cannot hide.
    "Q": 0.5 * (np.sum(spin * spin) - np.sum(strain * strain)),
    "Lambda2": float(np.sort(np.linalg.eigvals(strain @ strain + spin @ spin).real)[1]),
    "Vorticity": curl,
    "VorticityMagnitude": float(np.linalg.norm(curl)),
    "Divergence": float(np.trace(A)),
    "Helicity": (image_points(mixed) @ A.T) @ curl,
}
print("The same quantities for a velocity that is a general tensor times x:")
if abs(by_hand["Q"] + 0.5 * np.trace(A @ A)) > 1e-12:
    failures.append("the two ways of writing Q disagree, so the check is void")
for name, wanted in by_hand.items():
    values = array_of(mixed, name)
    if values is None:
        failures.append("%s was not produced for the general field" % name)
        continue
    worst = float(np.abs(values - wanted).max())
    print("  %-20s %-24s worst point off by %.1e"
          % (name, np.round(np.atleast_1d(np.asarray(wanted)).ravel()[:3], 6), worst))
    if worst > 1e-12:
        failures.append("%s is wrong for a general gradient (off by %.3e)" %
                        (name, worst))

# ============================================================= radial profile
# A plane cut through the middle of the box: the curve at radius r is a whole
# circle, so its length is 2 pi r, and a field that is the radius averages to
# the radius.

flat = pvs.Slice(Input=dense, SliceType="Plane")
flat.SliceType.Origin = [0.0, 0.0, 0.0]
flat.SliceType.Normal = [1.0, 0.0, 0.0]
marked = pvs.Calculator(Input=flat, ResultArrayName="R",
                        Function="sqrt(coordsY*coordsY + coordsZ*coordsZ)")
sided = pvs.Calculator(Input=marked, ResultArrayName="Y", Function="coordsY")
sided.UpdatePipeline()

profile = pvs.RadialProfile(Input=sided, RotationAxis="X", NumberOfBands=4,
                            AutomaticRadiusRange=0, RadiusRange=[1.0, 5.0],
                            Spacing="Uniform")
profile.UpdatePipeline()
columns = table_of(profile)
print("A plane cut into 4 evenly spaced bands between radius 1 and 5:")
print("  columns: %s" % ", ".join(sorted(columns)))
for row, wanted in enumerate(columns["Radius"]):
    length, average, sideways = (columns["Length"][row], columns["R"][row],
                                 columns["Y"][row])
    print("  r=%.3f  length %8.4f (2 pi r = %8.4f)  mean R %.4f  mean Y %+.1e"
          % (wanted, length, 2 * np.pi * wanted, average, sideways))
    # The curve is a polygon through a grid of unit cells rather than a circle,
    # so it falls a little short of the arc, most at the smallest radius.
    if abs(length - 2 * np.pi * wanted) > 0.03 * 2 * np.pi * wanted:
        failures.append("the curve at radius %g is %g long, expected %g" %
                        (wanted, length, 2 * np.pi * wanted))
    if not close(average, wanted, 1e-3):
        failures.append("the mean radius of the curve at %g is %g" % (wanted, average))
    # Y is as much positive as negative round a full circle, so anything but
    # zero would mean the edges are not weighted by their length.
    if abs(sideways) > 1e-6 * wanted:
        failures.append("Y averages to %g round the circle at radius %g, not 0" %
                        (sideways, wanted))

if not close(columns["Radius"][0], 1.5) or not close(columns["Radius"][-1], 4.5):
    failures.append("the bands are not centred in the given range: %s" %
                    columns["Radius"])

# Clustered spacing is the default, and has to put the samples into the walls
# without moving anything else about the answer.
profile.Spacing = "Clustered at the walls"
profile.UpdatePipeline()
clustered = table_of(profile)
span = 5.0 - 1.0
gaps = np.diff(np.concatenate([[1.0], clustered["Radius"], [5.0]]))
print("  clustered, the same 4 bands sit at %s" % np.round(clustered["Radius"], 4))
print("  the gap at the wall is %.4f of the span, in the middle %.4f"
      % (gaps[0] / span, gaps.max() / span))
if clustered["Radius"].min() <= 1.0 or clustered["Radius"].max() >= 5.0:
    failures.append("clustering put a band outside the range: %s"
                    % clustered["Radius"])
if not np.allclose(clustered["Radius"] - 1.0, 5.0 - clustered["Radius"][::-1]):
    failures.append("clustering is not symmetric about mid-span: %s"
                    % clustered["Radius"])
if gaps[0] >= 0.5 * gaps.max():
    failures.append("clustering did not draw the samples towards the walls: %s"
                    % gaps)
if abs(gaps[0] - gaps[-1]) > 1e-9:
    failures.append("the hub and the casing are clustered differently: %s" % gaps)
for row, wanted in enumerate(clustered["Radius"]):
    # The curve is still a circle wherever it is cut, so nothing else moved.
    if abs(clustered["Length"][row] - 2 * np.pi * wanted) > 0.05 * 2 * np.pi * wanted:
        failures.append("the clustered curve at radius %g is %g long, expected %g"
                        % (wanted, clustered["Length"][row], 2 * np.pi * wanted))
    if not close(clustered["R"][row], wanted, 1e-3):
        failures.append("the mean radius of the clustered curve at %g is %g"
                        % (wanted, clustered["R"][row]))
profile.Spacing = "Uniform"

profile.AutomaticRadiusRange = 1
profile.UpdatePipeline()
automatic = table_of(profile)["Radius"]
corner = np.hypot(5.0, 5.0)
print("  automatic range covers %.3f to %.3f of the available 0 to %.3f"
      % (automatic.min(), automatic.max(), corner))
if automatic.min() <= 0.0 or automatic.max() >= corner:
    failures.append("the automatic radius range is outside the data")

# ============================================================ surface average
# The same plane is a 10 by 10 square, so its area is 100 and the average of a
# field that is odd about the middle is zero.

square = pvs.Calculator(Input=sided, ResultArrayName="Three", Function="3")
straight = pvs.Calculator(Input=square, ResultArrayName="U",
                          Function="5*iHat + 0*jHat + 0*kHat")
uniform = pvs.Calculator(Input=straight, ResultArrayName="Rho2", Function="2")
uniform.UpdatePipeline()

average = pvs.SurfaceAverage(Input=uniform, Weighting="Area Average")
average.UpdatePipeline()
area = table_of(average)
print("A 10 by 10 square, area averaged: area %.4f, Three %.6f, R %.6f"
      % (area["Area"][0], area["Three"][0], area["R"][0]))
if not close(area["Area"][0], 100.0):
    failures.append("the area of the square is %g, expected 100" % area["Area"][0])
if not close(area["Three"][0], 3.0):
    failures.append("the area average of a constant 3 is %g" % area["Three"][0])

average.Weighting = "Integral"
average.UpdatePipeline()
integral = table_of(average)
print("  integrated instead: Three %.4f, expected 300" % integral["Three"][0])
if not close(integral["Three"][0], 300.0):
    failures.append("the integral of a constant 3 over 100 is %g" %
                    integral["Three"][0])

average.Weighting = "Mass Flow Average"
average.Velocity = "U"
average.Density = "Rho2"
average.UpdatePipeline()
flow = table_of(average)
print("  mass flow of 2 kg/m3 at 5 m/s through 100 m2: %.4f, expected 1000"
      % flow["MassFlow"][0])
if not close(flow["MassFlow"][0], 1000.0):
    failures.append("the mass flow is %g, expected 1000" % flow["MassFlow"][0])
for name in ("Three", "R"):
    if not close(flow[name][0], area[name][0], 1e-6):
        failures.append("with a uniform mass flux, the %s average moved from %g "
                        "to %g" % (name, area[name][0], flow[name][0]))

# With the density sloping across the plane the two averages must part company.
# Weighting Y by rho = 1 + Y/10 gives the integral of Y^2/10 over the integral
# of 1, which is 5/6 for a square of side 10.
sloped = pvs.Calculator(Input=straight, ResultArrayName="RhoY",
                        Function="1 + coordsY/10")
tilted = pvs.SurfaceAverage(Input=sloped, Weighting="Mass Flow Average",
                            Velocity="U", Density="RhoY")
tilted.UpdatePipeline()
weighted = table_of(tilted)
expected_y = 5.0 / 6.0
print("  Y averages to %+.4f by area and %+.4f by mass flow with a sloping "
      "density, against %.4f by hand" % (area["Y"][0], weighted["Y"][0], expected_y))
if abs(area["Y"][0]) > 1e-9:
    failures.append("Y area-averages to %g over a square centred on 0" % area["Y"][0])
# The product of the field and the weight is quadratic, and it is integrated
# with the same linear rule as everything else, so it lands a little high.
if abs(weighted["Y"][0] - expected_y) > 0.03 * expected_y:
    failures.append("the mass-flow average of Y is %g, expected about %g" %
                    (weighted["Y"][0], expected_y))

# The cells of a slice are triangles of wildly different sizes -- over a
# thousand to one on the rotor -- so what matters is that each counts for its
# own area and not for one cell's worth. Two triangles say it plainly.

import slice_averages                                            # noqa: E402

corners = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0],
                    [0, 0, 10], [10, 0, 10], [0, 10, 10]], dtype=float)
lopsided = vtkPolyData()
seats = vtkPoints()
seats.SetData(numpy_support.numpy_to_vtk(corners, deep=1))
lopsided.SetPoints(seats)
faces = vtkCellArray()
for triangle in ((0, 1, 2), (3, 4, 5)):
    face = vtkTriangle()
    for slot, corner in enumerate(triangle):
        face.GetPointIds().SetId(slot, corner)
    faces.InsertNextCell(face)
lopsided.SetPolys(faces)
on_cells = numpy_support.numpy_to_vtk(np.array([1.0, 100.0]), deep=1)
on_cells.SetName("C")
lopsided.GetCellData().AddArray(on_cells)
on_points = numpy_support.numpy_to_vtk(np.array([1.0] * 3 + [100.0] * 3), deep=1)
on_points.SetName("P")
lopsided.GetPointData().AddArray(on_points)

lopsided_average = slice_averages.SurfaceAverage()
lopsided_average.SetInputDataObject(0, lopsided)
lopsided_average.Update()
sheet = lopsided_average.GetOutputDataObject(0)
weighed = {sheet.GetColumnName(index): sheet.GetColumn(index).GetTuple1(0)
           for index in range(sheet.GetNumberOfColumns())
           if sheet.GetColumn(index).IsA("vtkDataArray")
           and sheet.GetColumn(index).GetNumberOfComponents() == 1}
small, large = 0.5, 50.0
by_area = (1.0 * small + 100.0 * large) / (small + large)
print("Triangles of area %g and %g carrying 1 and 100: area %g, average %.4f "
      "(%.4f by hand, %.1f unweighted)"
      % (small, large, weighed["Area"], weighed["C"], by_area, 101.0 / 2))
if not close(weighed["Area"], small + large):
    failures.append("two triangles of area %g and %g came to %g" %
                    (small, large, weighed["Area"]))
for name in ("C", "P"):
    if not close(weighed[name], by_area, 1e-9):
        failures.append("%s averaged to %g, which is not the %g the triangles' "
                        "areas call for" % (name, weighed[name], by_area))

# The same question one dimension down: the curve a band cuts is a chain of
# edges of very different lengths -- 771 to one on the rotor, where the mesh
# tightens against the blade -- and each has to count for its own length.

ends = np.array([[0, 0, 0], [1, 0, 0], [101, 0, 0]], dtype=float)
uneven = vtkPolyData()
stops = vtkPoints()
stops.SetData(numpy_support.numpy_to_vtk(ends, deep=1))
uneven.SetPoints(stops)
chain = vtkCellArray()
for first, second in ((0, 1), (1, 2)):
    edge = vtkLine()
    edge.GetPointIds().SetId(0, first)
    edge.GetPointIds().SetId(1, second)
    chain.InsertNextCell(edge)
uneven.SetLines(chain)
along_edges = numpy_support.numpy_to_vtk(np.array([1.0, 100.0]), deep=1)
along_edges.SetName("C")
uneven.GetCellData().AddArray(along_edges)
at_ends = numpy_support.numpy_to_vtk(np.array([0.0, 0.0, 100.0]), deep=1)
at_ends.SetName("P")
uneven.GetPointData().AddArray(at_ends)

integrals, measure, measured = slice_averages.integrate([uneven])
along = {name: float(total) / measure for name, total in integrals}
short, long = 1.0, 100.0
by_length = (1.0 * short + 100.0 * long) / (short + long)
ends_by_length = (short * 0.0 + long * 50.0) / (short + long)
print("Edges of length %g and %g, measured as %s = %g: 1 and 100 average to "
      "%.4f (%.4f by length, %.1f unweighted)"
      % (short, long, measured, measure, along["C"], by_length, 101.0 / 2))
if measured != "Length" or not close(measure, short + long):
    failures.append("two edges of length %g and %g came to %s = %g" %
                    (short, long, measured, measure))
if not close(along["C"], by_length, 1e-9):
    failures.append("along the curve, 1 and 100 averaged to %g rather than the "
                    "%g their lengths call for" % (along["C"], by_length))
# Point data is integrated along each edge, not averaged over the points: the
# plain mean of 0, 0 and 100 would be 33.3.
if not close(along["P"], ends_by_length, 1e-9):
    failures.append("point data along the curve averaged to %g, expected %g" %
                    (along["P"], ends_by_length))

# ================================================================ the CGNS files
# Arithmetic cannot say whether these are a compressor's numbers. Rotor 37 can.

reader = pvs.CGNSLabeledBoundaries(
    FileName=str(HERE / "r37.cgns"), Boundaries=[])
gas = pvs.Calculator(Input=reader, ResultArrayName="Density",
                     Function="Pressure/(287.058*Temperature)")
station = pvs.Slice(Input=gas, SliceType="Plane")
station.SliceType.Origin = [0.03, 0.0, 0.0]
station.SliceType.Normal = [1.0, 0.0, 0.0]
station.UpdatePipeline()

passage = pvs.SurfaceAverage(Input=station, Weighting="Mass Flow Average",
                             Velocity="Velocity", Density="Density")
passage.UpdatePipeline()
row = table_of(passage)
annulus = 36 * float(row["MassFlow"][0])
print("Rotor 37 at x = 30 mm: %.4f kg/s through one passage, %.2f kg/s through "
      "all 36 (the design value is 20.19)" % (row["MassFlow"][0], annulus))
if not 15.0 < annulus < 25.0:
    failures.append("the mass flow of the whole annulus is %g kg/s, which is "
                    "nowhere near rotor 37's 20.19" % annulus)

# Evenly spaced, because the rows are about to be averaged back into one
# number and only then do they carry equal weight.
span = pvs.RadialProfile(Input=station, RotationAxis="X", NumberOfBands=10,
                         Spacing="Uniform")
span.UpdatePipeline()
hub_to_tip = table_of(span)
print("  hub to casing: pressure %.0f to %.0f Pa over radius %.4f to %.4f m"
      % (hub_to_tip["Pressure"][0], hub_to_tip["Pressure"][-1],
         hub_to_tip["Radius"][0], hub_to_tip["Radius"][-1]))
if hub_to_tip["Pressure"][-1] <= hub_to_tip["Pressure"][0]:
    failures.append("the pressure does not rise from hub to casing")
area_row = pvs.SurfaceAverage(Input=station, Weighting="Area Average")
area_row.UpdatePipeline()
whole = table_of(area_row)
lengths = hub_to_tip["Length"]
banded = float(np.average(hub_to_tip["Pressure"], weights=lengths))
print("  the bands average to %.0f Pa, the whole slice to %.0f Pa"
      % (banded, whole["Pressure"][0]))
if abs(banded - whole["Pressure"][0]) > 0.02 * whole["Pressure"][0]:
    failures.append("the bands average to %g Pa but the slice to %g Pa" %
                    (banded, whole["Pressure"][0]))

# Every row has to say which surface it is, or a table of numbers means nothing.
patches = pvs.CGNSLabeledBoundaries(FileName=str(HERE / "r37.cgns"), Volumes=[])
patches.Boundaries = ["HUB_quad", "CASING_quad"]
each = pvs.SurfaceAverage(Input=patches)
each.UpdatePipeline()
listed = table_of(each)
print("Two boundary patches averaged: %s"
      % ", ".join("%s (area %.5f, centred %.4f from the axis)"
                  % (name, area, np.hypot(centre[1], centre[2]))
                  for name, area, centre in zip(listed["Surface"], listed["Area"],
                                                listed["Centre"])))
if sorted(listed["Surface"]) != ["Boundaries/CASING_quad", "Boundaries/HUB_quad"]:
    failures.append("the rows are not named after their blocks: %s" %
                    list(listed["Surface"]))
by_name = dict(zip(listed["Surface"], listed["Centre"]))
hub = float(np.hypot(*by_name["Boundaries/HUB_quad"][1:]))
casing = float(np.hypot(*by_name["Boundaries/CASING_quad"][1:]))
if not hub < casing:
    failures.append("the hub is not inside the casing: %g against %g" % (hub, casing))

each.Rows = "Everything Together"
each.UpdatePipeline()
combined = table_of(each)
print("  added up instead: %s, area %.5f against %.5f apart"
      % (combined["Surface"][0], combined["Area"][0], listed["Area"].sum()))
if combined["Surface"][0] != "2 blocks" or len(combined["Area"]) != 1:
    failures.append("Everything Together did not give one row: %s" %
                    list(combined["Surface"]))
if not close(combined["Area"][0], listed["Area"].sum(), 1e-9):
    failures.append("the blocks together are %g but apart they are %g" %
                    (combined["Area"][0], listed["Area"].sum()))

# Mass is conserved, so two stations of the same passage must carry the same
# flow. Nothing in the code makes that happen; the integration has to.
upstream = pvs.Slice(Input=gas, SliceType="Plane")
upstream.SliceType.Origin = [0.0, 0.0, 0.0]
upstream.SliceType.Normal = [1.0, 0.0, 0.0]
inlet = pvs.SurfaceAverage(Input=upstream, Weighting="Mass Flow Average",
                           Velocity="Velocity", Density="Density")
inlet.UpdatePipeline()
first = float(table_of(inlet)["MassFlow"][0])
second = float(row["MassFlow"][0])
print("  the same passage at x = 0 and x = 30 mm carries %.5f and %.5f kg/s, "
      "differing by %.2f%%" % (first, second, 100 * abs(first - second) / first))
if abs(first - second) > 0.01 * first:
    failures.append("mass is not conserved between the two stations: %g against "
                    "%g kg/s" % (first, second))

# ==================================================================== the buttons

scene = pvs.CreateRenderView()
pvs.AssignViewToLayout(view=scene)
pvs.SetActiveSource(reader)
macros = HERE / "macros"
exec(compile((macros / "Flow Properties.py").read_text(), "Flow Properties.py", "exec"), {})
made = pvs.GetActiveSource()
print("The Flow Properties button made %s." % made.GetXMLName())
if made.GetXMLName() != "FlowProperties":
    failures.append("the Flow Properties button did not leave one active")

pvs.SetActiveSource(reader)
exec(compile((macros / "Radial Profile.py").read_text(), "Radial Profile.py", "exec"), {})
plotted = pvs.GetActiveSource()
charts = [view for view in pvs.GetViews() if view.GetXMLName() == "XYChartView"]
print("The Radial Profile button made %s and %d chart view(s)."
      % (plotted.GetXMLName(), len(charts)))
if plotted.GetXMLName() != "RadialProfile":
    failures.append("the Radial Profile button did not leave a profile active")
elif plotted.GetDataInformation().GetNumberOfRows() < 1:
    failures.append("the Radial Profile button produced an empty profile")
if not charts:
    failures.append("the Radial Profile button did not open a chart view")
else:
    display = servermanager.GetRepresentation(plotted, charts[0])
    series = list(display.SeriesVisibility)
    known = list(display.SeriesLabel)[::2]
    print("  %r along the foot, up the side: %s" % (display.XArrayName,
                                                    ", ".join(series)))
    if display.XArrayName != "Radius" or display.UseIndexForXAxis != 0:
        failures.append("the foot of the chart is %r rather than the radius" %
                        display.XArrayName)
    if charts[0].BottomAxisTitle != "Radius":
        failures.append("the foot of the chart is not labelled Radius")
    if [name for name in series if name in _NOT_SERIES]:
        failures.append("the chart plots its own bookkeeping columns: %s" % series)
    if not [name for name in series if name not in _NOT_SERIES]:
        failures.append("no flow property is plotted at all")
    # A vector column becomes several series, so naming the column would name
    # none of them and the velocity would quietly go missing.
    if "Velocity_Magnitude" not in series:
        failures.append("the velocity is missing from the chart: %s" % series)

pvs.SetActiveSource(station)
exec(compile((macros / "Surface Average.py").read_text(), "Surface Average.py", "exec"), {})
totalled = pvs.GetActiveSource()
sheets = [view for view in pvs.GetViews() if view.GetXMLName() == "SpreadSheetView"]
print("The Surface Average button made %s and %d spreadsheet view(s)."
      % (totalled.GetXMLName(), len(sheets)))
if totalled.GetXMLName() != "SurfaceAverage":
    failures.append("the Surface Average button did not leave an average active")
if not sheets:
    failures.append("the Surface Average button did not open a spreadsheet view")

# Pressing a button again must not carve the window up a second time.
cells_before = sorted(pvs.GetLayout().GetViewLocation(view) for view in pvs.GetViews())
pvs.SetActiveSource(station)
exec(compile((macros / "Surface Average.py").read_text(), "Surface Average.py", "exec"), {})
again = [view for view in pvs.GetViews() if view.GetXMLName() == "SpreadSheetView"]
cells_after = sorted(pvs.GetLayout().GetViewLocation(view) for view in pvs.GetViews())
print("A second press reuses the view: %d spreadsheet(s), cells %s"
      % (len(again), cells_after))
if len(again) != 1 or cells_before != cells_after:
    failures.append("pressing the button twice split the window twice: %s then %s"
                    % (cells_before, cells_after))

# Both of them belong under the render view rather than beside it. Whatever the
# stack ends up looking like, every split on the way down to them has to be a
# top-and-bottom one; a single side-by-side split would put one on the right.
plan = pvs.GetLayout()


def splits_above(cell):
    """The direction of every split between the root and a cell."""
    directions = []
    while cell > 0:
        cell = (cell - 1) // 2
        directions.append(plan.SMProxy.GetSplitDirection(cell))
    return directions


print("  the layout: %s" % ", ".join(
    "%s in cell %d" % (view.GetXMLName(), plan.GetViewLocation(view))
    for view in pvs.GetViews() if plan.GetViewLocation(view) >= 0))
if plan.GetViewLocation(scene) < 0:
    failures.append("the render view was pushed out of the layout")
for view, what in [(charts[0], "chart") if charts else (None, None),
                   (sheets[0], "spreadsheet") if sheets else (None, None)]:
    if view is None:
        continue
    directions = splits_above(plan.GetViewLocation(view))
    print("  the %s is reached through %s" % (what, ", ".join(
        {1: "a top and bottom split", 2: "a side by side split"}.get(one, "?")
        for one in directions)))
    if any(one != 1 for one in directions):
        failures.append("the %s was put beside what it belongs to, not below "
                        "it" % what)

# A second station must get a plot of its own rather than drawing over the
# first, and the row of them must stay evenly wide.
elsewhere = pvs.Slice(Input=reader, SliceType="Plane")
elsewhere.SliceType.Origin = [0.06, 0.0, 0.0]
elsewhere.SliceType.Normal = [1.0, 0.0, 0.0]
elsewhere.UpdatePipeline()
pvs.SetActiveSource(elsewhere)
exec(compile((macros / "Radial Profile.py").read_text(), "Radial Profile.py", "exec"), {})
drawn = [view for view in pvs.GetViews() if view.GetXMLName() == "XYChartView"
         and plan.GetViewLocation(view) >= 0]
titles = [view.ChartTitle for view in sorted(drawn, key=plan.GetViewLocation)]
print("A second station: %d plots, titled %s" % (len(drawn), titles))
if len(drawn) != 2:
    failures.append("a second profile made %d plots, expected 2" % len(drawn))
elif titles[0] == titles[1]:
    failures.append("both plots are called %r, so neither says which station it "
                    "is" % titles[0])
else:
    cells = sorted(plan.GetViewLocation(view) for view in drawn)
    parent = (cells[-1] - 1) // 2
    share = plan.SMProxy.GetSplitFraction(parent)
    print("  they sit side by side in cells %s, sharing it %.2f/%.2f"
          % (cells, share, 1 - share))
    if plan.SMProxy.GetSplitDirection(parent) != 2:
        failures.append("the second plot went under the first, not beside it")
    if abs(share - 0.5) > 1e-6:
        failures.append("two plots share the row %g to %g" % (share, 1 - share))

# A profile crowding another's plot -- which is where the eye icon drops it,
# since ParaView draws a source into whatever view is active -- must be moved to
# its own when the button is pressed on it, not left sharing.
first_profile, second_profile = plotted, pvs.GetActiveSource()
first_chart = charts[0]


def charts_now():
    return [view for view in pvs.GetViews() if view.GetXMLName() == "XYChartView"
            and plan.GetViewLocation(view) >= 0]


def chart_indices(profile):
    return [index for index, chart in enumerate(charts_now())
            if (lambda rep: rep is not None and rep.Visibility)(
                servermanager.GetRepresentation(profile, chart))]

# Take the second profile's own plot away, so it is homeless like a station
# whose window was closed, then force it onto the first's plot the way the eye
# icon would.
for chart in charts_now():
    if chart is not first_chart and second_profile in [
            source for source in pvs.GetSources().values()
            if source.GetXMLName() == "RadialProfile"
            and (lambda r: r is not None and r.Visibility)(
                servermanager.GetRepresentation(source, chart))]:
        pvs.Delete(chart)
pvs.SetActiveView(first_chart)
pvs.Show(second_profile, first_chart)
pvs.Render(first_chart)
crowded = chart_indices(second_profile)
print("The homeless second profile, forced onto the first's chart, sits on %s"
      % crowded)
if crowded != [0]:
    failures.append("the setup for the crowding check is wrong: %s" % crowded)

pvs.SetActiveView(first_chart)
pvs.SetActiveSource(second_profile)
exec(compile((macros / "Radial Profile.py").read_text(),
             "Radial Profile.py", "exec"), {})
after_first = chart_indices(first_profile)
after_second = chart_indices(second_profile)
print("After pressing the button on it: first profile on %s, second on %s"
      % (after_first, after_second))
if after_second == after_first:
    failures.append("the crowded profile was not moved off the other's chart")
if after_first != [0]:
    failures.append("moving the crowded profile disturbed the first: %s"
                    % after_first)
if len(after_second) != 1:
    failures.append("the moved profile is on %d charts, expected 1"
                    % len(after_second))

# ------------------------------------------------------ plotting csv files
# A separate button opens several csv files and draws them on one chart, each
# against its own radius column and named after the file. The picker is stood in
# for by a zenity that answers out of the environment.

bench = Path(tempfile.mkdtemp(prefix="csvs-"))
was_on_path = os.environ["PATH"]
try:
    for name, pressure in (("runA", "121500"), ("runB", "119000")):
        (bench / (name + ".csv")).write_text(
            "Radius,Pressure,Temperature\n0.19,%s,309\n0.22,131500,321\n" % pressure)
    (bench / "zenity").write_text(
        "#!/bin/sh\n"
        "case \"$*\" in *--file-selection*) printf '%s' \"$FAKE_FILES\" ;; esac\n"
        "exit 0\n")
    (bench / "zenity").chmod(0o755)
    os.environ["PATH"] = "%s:%s" % (bench, was_on_path)
    files = [str(bench / (name + ".csv")) for name in ("runA", "runB")]
    os.environ["FAKE_FILES"] = "\x1f".join(files)

    # zenity's multi-select separator varies by version; the parse has to cope
    # with a pipe or a newline, not only the one asked for, or nothing loads.
    import paraview_diy_macros as diy_parse                       # noqa: E402
    for label, joined in (("pipe", "|".join(files)),
                          ("newline", "\n".join(files))):
        os.environ["FAKE_FILES"] = joined
        got = diy_parse.ask_for_files("t", (("Tables", "*.csv"),))
        if got != files:
            failures.append("a %s-separated multi-select parsed to %s" %
                            (label, got))
    print("Multi-select survives pipe and newline separators.")
    os.environ["FAKE_FILES"] = "\x1f".join(files)

    charts_before = len([view for view in pvs.GetViews()
                         if view.GetXMLName() == "XYChartView"])
    pvs.SetActiveSource(None)
    exec(compile((macros / "Plot CSV Files.py").read_text(),
                 "Plot CSV Files.py", "exec"), {})
    csv_charts = [view for view in pvs.GetViews()
                  if view.GetXMLName() == "XYChartView"]
    opened = [source for source in pvs.GetSources().values()
              if source.GetXMLName() == "CSVReader"]
    print("Plot CSV Files opened %d tables onto %d new chart"
          % (len(opened), len(csv_charts) - charts_before))
    if len(opened) != 2:
        failures.append("the CSV plotter opened %d tables, expected 2" % len(opened))
    if len(csv_charts) - charts_before != 1:
        failures.append("the CSV plotter did not make exactly one chart")
    elif opened:
        chart = csv_charts[-1]
        csv_tab = pvs.GetLayout(chart)
        tab_names = [name for (name, _id) in pvs.GetLayouts().keys()
                     if pvs.GetLayouts()[(name, _id)] == csv_tab]
        on_own_tab = all(pvs.GetLayout(view) != csv_tab for view in pvs.GetViews()
                         if view.GetXMLName() != "XYChartView")
        print("  the plot is in the %s tab, apart from the render view: %s"
              % (tab_names, on_own_tab))
        if not on_own_tab:
            failures.append("the CSV plot shares a tab with other views")
        for table in opened:
            display = servermanager.GetRepresentation(table, chart)
            if display is None:
                failures.append("a CSV file was not drawn on the chart")
                continue
            labels = dict(zip(list(display.SeriesLabel)[::2],
                              list(display.SeriesLabel)[1::2]))
            name = [reg for (reg, _id), proxy in pvs.GetSources().items()
                    if proxy == table][0]
            print("  %s against %s, Pressure labelled %r"
                  % (name, display.XArrayName, labels.get("Pressure")))
            if str(display.XArrayName).strip("'") != "Radius":
                failures.append("%s was not plotted against Radius" % name)
            if labels.get("Pressure") != "%s: Pressure" % name:
                failures.append("%s's series were not named after the file: %s"
                                % (name, labels.get("Pressure")))

    # Press again with a second set: it gets its own chart beside the first,
    # which stays drawn -- that side by side is how several sets are compared.
    second = Path(tempfile.mkdtemp(prefix="csvs2-"))
    try:
        for name, pressure in (("runC", "101000"), ("runD", "99000")):
            (second / (name + ".csv")).write_text(
                "Radius,Pressure,Temperature\n0.19,%s,309\n0.22,98000,321\n" % pressure)
        os.environ["FAKE_FILES"] = "\x1f".join(
            str(second / (name + ".csv")) for name in ("runC", "runD"))
        pvs.SetActiveSource(None)
        exec(compile((macros / "Plot CSV Files.py").read_text(),
                     "Plot CSV Files.py", "exec"), {})
        tab_charts = diy_parse.views_in_tab("XYChartView", "CSV Files")
        # Indexed only once it is known there is a chart to index, so a macro
        # that made none fails with the message below rather than an IndexError.
        first_drawn = None
        if opened and tab_charts:
            first_drawn = servermanager.GetRepresentation(opened[0], tab_charts[0])
        kept = first_drawn is not None and bool(first_drawn.Visibility)
        titles = [str(diy_parse.value(chart.ChartTitle)) for chart in tab_charts]
        print("A second set: %d charts side by side in the tab %s, the first kept: %s"
              % (len(tab_charts), titles, kept))
        if len(tab_charts) != 2:
            failures.append("a second set made %d charts in the tab, expected 2"
                            % len(tab_charts))
        if not kept:
            failures.append("the second set cleared the first set's chart")
        # Sets are told apart by their titles, so two charts must never share one.
        if len(set(titles)) != len(titles):
            failures.append("two charts carry the same title: %s" % titles)

        # A third set holding a file from the first: the same measurement often
        # belongs on more than one chart, and picking it again must draw the
        # one already open rather than put a second reader of it in the
        # Pipeline Browser under the same name.
        os.environ["FAKE_FILES"] = "\x1f".join(
            [files[0], str(second / "runC.csv")])
        pvs.SetActiveSource(None)
        # Run it with a namespace of its own kept, so the macro's own idea of
        # which source reads which file is what the count is made with.
        macro = {}
        exec(compile((macros / "Plot CSV Files.py").read_text(),
                     "Plot CSV Files.py", "exec"), macro)
        readers = [source for source in pvs.GetSources().values()
                   if source.GetXMLName() == "CSVReader"]
        shared = [source for source in readers
                  if os.path.realpath(files[0])
                  in [os.path.realpath(name)
                      for name in macro["files_of"](source) if name]]
        charts = diy_parse.views_in_tab("XYChartView", "CSV Files")
        drawn = [index for index, chart in enumerate(charts)
                 if shared and servermanager.GetRepresentation(shared[0], chart)
                 is not None]
        print("A file used again: %d reader(s) of it, drawn on charts %s of %d."
              % (len(shared), drawn, len(charts)))
        if len(shared) != 1:
            failures.append("a file picked twice left %d readers of it, expected "
                            "the one" % len(shared))
        if drawn != [0, 2]:
            failures.append("the file used again is drawn on charts %s, expected "
                            "its first and its third" % drawn)

        # The same file can be open as something other than a table as well --
        # and the search must not stop at whichever reads it first. Stood in
        # for here, since one csv is only ever read one way in practice.
        class Reads(object):
            def __init__(self, path, table):
                self.FileName, self.table = path, table

            def GetDataInformation(self):
                return self

            def GetDataSetTypeAsString(self):
                return "vtkTable" if self.table else "vtkUnstructuredGrid"

        class Pipeline(object):
            def __init__(self, sources):
                self.sources = sources

            def GetSources(self):
                return {("s%d" % index, index): source
                        for index, source in enumerate(self.sources)}

        mesh = Reads(files[0], table=False)
        as_well = Reads(files[0], table=True)
        was_pvs = macro["pvs"]
        try:
            macro["pvs"] = Pipeline([mesh, as_well])
            found = macro["already_open"](files[0])
        finally:
            macro["pvs"] = was_pvs
        print("A file open as a mesh and as a table: the table is the one found: "
              "%s" % (found is as_well))
        if found is not as_well:
            failures.append("the search for an open file stopped at a source "
                            "that is not a table")
    finally:
        shutil.rmtree(str(second), ignore_errors=True)
finally:
    os.environ["PATH"] = was_on_path
    os.environ.pop("FAKE_FILES", None)
    shutil.rmtree(str(bench), ignore_errors=True)

# With every view closed there is no active view, and pvs.GetLayout() raises
# then; the macro helpers have to weather that rather than crash.
import paraview_diy_macros as diy_helpers                        # noqa: E402
for view in list(pvs.GetViews()):
    pvs.Delete(view)
try:
    empty = diy_helpers.views_of("XYChartView")
    layout = diy_helpers._layout_of()
    print("With no view open: views_of gave %r, _layout_of did not raise." % empty)
    if empty:
        failures.append("views_of found charts after every view was closed")
except Exception as error:
    failures.append("the macro helpers crash with no active view: %s" % error)

if failures:
    raise SystemExit("ERROR: " + "; ".join(failures))
print("Flow properties, radial profiles and surface averages verified.")
