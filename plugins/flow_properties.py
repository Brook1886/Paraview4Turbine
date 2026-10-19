"""ParaView plugin: the derived quantities a CFD post-processor asks for.

A CGNS file carries what the solver wrote -- velocity, pressure, temperature --
and none of the quantities that are worked out from their derivatives. This
filter adds those: Q-criterion and lambda2 for finding vortices, vorticity and
its magnitude, helicity, divergence, and a numerical schlieren image of the
density gradient for shocks.

Everything comes from one pass of vtkGradientFilter per mesh, so asking for
several quantities costs little more than asking for one, and each lands on the
same attribute as the velocity or density it came from.
"""

import os
import sys

import numpy as np

from paraview.util.vtkAlgorithm import smdomain, smproperty, smproxy
from vtkmodules.util import numpy_support
from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase
from vtkmodules.vtkCommonCore import vtkDataArraySelection
from vtkmodules.vtkCommonDataModel import (vtkCompositeDataSet, vtkDataObject,
                                           vtkMultiBlockDataSet)
from vtkmodules.vtkFiltersGeneral import vtkGradientFilter

# The toolbar macros are run by ParaView as bare strings, with no __file__ of
# their own to find their common code by. A plugin has one, so it puts pylib
# where a macro can import it from however ParaView was started.
_PYLIB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "pylib")
if _PYLIB not in sys.path:
    sys.path.append(_PYLIB)

# Checkbox label -> the array each quantity produces.
_FROM_VELOCITY = [
    ("Q-Criterion", "Q"),
    ("Lambda2", "Lambda2"),
    ("Vorticity", "Vorticity"),
    ("Vorticity Magnitude", "VorticityMagnitude"),
    ("Helicity", "Helicity"),
    ("Divergence", "Divergence"),
]
_FROM_DENSITY = [
    ("Density Gradient Magnitude", "DensityGradientMagnitude"),
    ("Numerical Schlieren", "Schlieren"),
]
_QUANTITIES = _FROM_VELOCITY + _FROM_DENSITY

_POINTS = vtkDataObject.FIELD_ASSOCIATION_POINTS
_CELLS = vtkDataObject.FIELD_ASSOCIATION_CELLS

# Names vtkGradientFilter is told to use for its own outputs.
_GRADIENT = "VelocityGradient"
_DENSITY_GRADIENT = "DensityGradientVector"


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


def _map_leaves(node, convert):
    """A copy of the input tree with every dataset run through ``convert``."""
    if node is None:
        return None
    children = _children(node) if node.IsA("vtkCompositeDataSet") else None
    if children is None:
        return convert(node)
    branch = vtkMultiBlockDataSet()
    branch.SetNumberOfBlocks(len(children))
    for index, child in enumerate(children):
        branch.SetBlock(index, _map_leaves(child, convert))
        if node.HasMetaData(index):
            metadata = node.GetMetaData(index)
            if metadata.Has(vtkCompositeDataSet.NAME()):
                branch.GetMetaData(index).Set(vtkCompositeDataSet.NAME(),
                                              metadata.Get(vtkCompositeDataSet.NAME()))
    return branch


def _leaves(node):
    if node is None:
        return []
    children = _children(node) if node.IsA("vtkCompositeDataSet") else None
    if children is None:
        return [node]
    found = []
    for child in children:
        found += _leaves(child)
    return found


def _attributes(dataset, association):
    return (dataset.GetPointData() if association == _POINTS
            else dataset.GetCellData())


def find_array(dataset, name):
    """(association, array) for a name, looking at the points first."""
    for association in (_POINTS, _CELLS):
        array = _attributes(dataset, association).GetArray(name)
        if array is not None:
            return association, array
    return None, None


def _values(dataset, association, name):
    array = _attributes(dataset, association).GetArray(name)
    return None if array is None else numpy_support.vtk_to_numpy(array)


def _add(dataset, association, name, values):
    array = numpy_support.numpy_to_vtk(np.ascontiguousarray(values), deep=1)
    array.SetName(name)
    _attributes(dataset, association).AddArray(array)


def _drop(dataset, association, name):
    _attributes(dataset, association).RemoveArray(name)


def lambda2(gradient):
    """The middle eigenvalue of S^2 + W^2, negative inside a vortex core.

    S and W are the symmetric and antisymmetric halves of the velocity gradient,
    so their squares sum to a symmetric matrix and the eigenvalues are real.
    """
    tensor = gradient.reshape(-1, 3, 3)
    transposed = tensor.transpose(0, 2, 1)
    strain = 0.5 * (tensor + transposed)
    spin = 0.5 * (tensor - transposed)
    combined = np.matmul(strain, strain) + np.matmul(spin, spin)
    return np.linalg.eigvalsh(combined)[:, 1]


def schlieren(magnitude, largest, exponent):
    """The photograph a schlieren rig would have made, bright where the density
    is uniform and dark across a shock."""
    if largest <= 0.0:
        return np.ones_like(magnitude)
    return np.exp(-exponent * magnitude / largest)


@smproxy.filter(name="FlowProperties", label="Flow Properties")
@smproperty.xml("""
    <Documentation short_help="Quantities worked out from the flow's derivatives.">
      Adds the derived quantities a CGNS file does not carry: Q-criterion and
      lambda2 for vortex cores, vorticity, helicity, divergence, and a numerical
      schlieren image of the density gradient.
    </Documentation>""")
@smproperty.input(name="Input", port_index=0)
@smdomain.datatype(dataTypes=["vtkDataSet", "vtkMultiBlockDataSet"],
                   composite_data_supported=1)
@smdomain.xml("""
    <InputArrayDomain name="a_vector" attribute_type="any-except-field"
                      number_of_components="3" optional="1"/>""")
@smdomain.xml("""
    <InputArrayDomain name="a_scalar" attribute_type="any-except-field"
                      number_of_components="1" optional="1"/>""")
class FlowProperties(VTKPythonAlgorithmBase):
    """Derived flow quantities, added to a copy of the input."""

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=1, inputType="vtkDataObject",
                                        nOutputPorts=1, outputType="vtkDataObject")
        self._velocity = "Velocity"
        self._density = "Density"
        self._exponent = 20.0
        self._quantities = vtkDataArraySelection()
        for label, _array in _QUANTITIES:
            self._quantities.AddArray(label)
        # Only the two that any velocity field can give start ticked. The
        # density ones especially must not: the Density box lands on whatever
        # array comes first when the file has no density in it, and a schlieren
        # picture of the turbulent kinetic energy is worse than none at all.
        for label, _array in _QUANTITIES:
            if label not in ("Q-Criterion", "Vorticity Magnitude"):
                self._quantities.DisableArray(label)
        self._quantities.AddObserver("ModifiedEvent", lambda *_: self.Modified())

    # ------------------------------------------------------------- properties

    @smproperty.stringvector(name="Velocity", label="Velocity", default_values="Velocity")
    @smdomain.xml("""
        <ArrayListDomain name="array_list" input_domain_name="a_vector">
          <RequiredProperties><Property name="Input" function="Input"/></RequiredProperties>
        </ArrayListDomain>""")
    @smdomain.xml("""
        <Documentation>The velocity field everything but the schlieren picture
        is worked out from. Only three-component arrays are offered.
        </Documentation>""")
    def SetVelocity(self, name):
        if name != self._velocity:
            self._velocity = name
            self.Modified()

    @smproperty.stringvector(name="Density", label="Density", default_values="Density")
    @smdomain.xml("""
        <ArrayListDomain name="array_list" input_domain_name="a_scalar">
          <RequiredProperties><Property name="Input" function="Input"/></RequiredProperties>
        </ArrayListDomain>""")
    @smdomain.xml("""
        <Documentation>The density the shock pictures are made from. A CGNS file
        often has no density in it, in which case make one first with a
        Calculator -- Pressure/(287.058*Temperature) for air -- and pick it here.
        Nothing reads this unless one of the two density quantities is ticked.
        </Documentation>""")
    def SetDensity(self, name):
        if name != self._density:
            self._density = name
            self.Modified()

    @smproperty.dataarrayselection(name="Quantities")
    def GetQuantitiesSelection(self):
        return self._quantities

    @smproperty.doublevector(name="SchlierenExponent", label="Schlieren Exponent",
                             default_values=20.0)
    @smdomain.doublerange(min=0.0)
    @smdomain.xml("""
        <Documentation>How hard the schlieren image darkens with the density
        gradient. The picture is exp(-k |grad rho| / max |grad rho|), so a larger
        k brings out the weaker gradients and saturates the shocks.
        </Documentation>""")
    def SetSchlierenExponent(self, value):
        value = float(value)
        if value != self._exponent:
            self._exponent = value
            self.Modified()

    # ---------------------------------------------------------------- execute

    def _wanted(self, group):
        return [(label, array) for label, array in group
                if self._quantities.ArrayIsEnabled(label)]

    def _gradient(self, dataset, association, name, result):
        """vtkGradientFilter over one array, set up for what was asked for."""
        gradient = vtkGradientFilter()
        gradient.SetInputData(dataset)
        gradient.SetInputArrayToProcess(0, 0, 0, association, name)
        gradient.SetResultArrayName(result)
        return gradient

    def _velocity_quantities(self, dataset, wanted):
        association, array = find_array(dataset, self._velocity)
        if array is None:
            return dataset, "no array named %s" % self._velocity
        if array.GetNumberOfComponents() != 3:
            return dataset, "%s is not a three-component array" % self._velocity

        labels = {label for label, _array in wanted}
        # Q and lambda2 both come out of the gradient tensor; vorticity is
        # needed for helicity even when it is not wanted on its own.
        needs_tensor = "Lambda2" in labels
        needs_vorticity = bool(labels & {"Vorticity", "Vorticity Magnitude", "Helicity"})

        gradient = self._gradient(dataset, association, self._velocity, _GRADIENT)
        gradient.SetComputeGradient(needs_tensor)
        gradient.SetComputeQCriterion("Q-Criterion" in labels)
        gradient.SetQCriterionArrayName("Q")
        gradient.SetComputeVorticity(needs_vorticity)
        gradient.SetVorticityArrayName("Vorticity")
        gradient.SetComputeDivergence("Divergence" in labels)
        gradient.SetDivergenceArrayName("Divergence")
        gradient.Update()

        piece = gradient.GetOutput().NewInstance()
        piece.ShallowCopy(gradient.GetOutput())

        if needs_tensor:
            _add(piece, association, "Lambda2",
                 lambda2(_values(piece, association, _GRADIENT)))
            _drop(piece, association, _GRADIENT)
        if needs_vorticity:
            vorticity = _values(piece, association, "Vorticity")
            if "Vorticity Magnitude" in labels:
                _add(piece, association, "VorticityMagnitude",
                     np.linalg.norm(vorticity, axis=1))
            if "Helicity" in labels:
                _add(piece, association, "Helicity",
                     np.einsum("ij,ij->i", numpy_support.vtk_to_numpy(array), vorticity))
            if "Vorticity" not in labels:
                _drop(piece, association, "Vorticity")
        return piece, None

    def _density_gradient(self, dataset):
        """The magnitude of the density gradient, which both of its quantities
        are made from."""
        association, array = find_array(dataset, self._density)
        if array is None:
            return dataset, "no array named %s" % self._density
        if array.GetNumberOfComponents() != 1:
            return dataset, "%s is not a single-component array" % self._density

        gradient = self._gradient(dataset, association, self._density,
                                  _DENSITY_GRADIENT)
        gradient.SetComputeGradient(True)
        gradient.Update()
        piece = gradient.GetOutput().NewInstance()
        piece.ShallowCopy(gradient.GetOutput())
        _add(piece, association, "DensityGradientMagnitude",
             np.linalg.norm(_values(piece, association, _DENSITY_GRADIENT), axis=1))
        _drop(piece, association, _DENSITY_GRADIENT)
        return piece, None

    def RequestDataObject(self, _request, in_info, out_info):
        source = self.GetInputData(in_info, 0, 0)
        target = self.GetOutputData(out_info, 0)
        if source is not None and (target is None or
                                   not target.IsA(source.GetClassName())):
            target = source.NewInstance()
            out_info.GetInformationObject(0).Set(target.DATA_OBJECT(), target)
        return 1

    def RequestData(self, _request, in_info, out_info):
        source = vtkDataObject.GetData(in_info[0], 0)
        output = vtkDataObject.GetData(out_info, 0)
        if source is None:
            return 1
        try:
            from_velocity = self._wanted(_FROM_VELOCITY)
            from_density = self._wanted(_FROM_DENSITY)
            complaints = set()

            def convert(dataset):
                piece = dataset.NewInstance()
                piece.ShallowCopy(dataset)
                if from_velocity:
                    piece, complaint = self._velocity_quantities(piece, from_velocity)
                    complaints.add(complaint)
                if from_density:
                    piece, complaint = self._density_gradient(piece)
                    complaints.add(complaint)
                return piece

            result = _map_leaves(source, convert)

            # The schlieren picture is scaled by the steepest gradient anywhere
            # in the mesh, so it can only be made once every block has one.
            if from_density and any(label == "Numerical Schlieren"
                                    for label, _array in from_density):
                self._add_schlieren(result)
            # The gradient magnitude is made whether or not it was asked for,
            # because the schlieren picture needs it; clear it away again if it
            # was only ever a means to that. Anything the input arrived with is
            # left alone.
            wanted = {label for label, _array in from_density}
            if from_density and "Density Gradient Magnitude" not in wanted:
                for leaf in _leaves(result):
                    for association in (_POINTS, _CELLS):
                        _drop(leaf, association, "DensityGradientMagnitude")

            output.ShallowCopy(result)
            for complaint in sorted(complaint for complaint in complaints if complaint):
                print("Flow properties: %s, so those quantities are missing." % complaint)
        except Exception as error:
            print("Flow properties: %s" % error)
            return 0
        return 1

    def _add_schlieren(self, result):
        largest = 0.0
        for leaf in _leaves(result):
            for association in (_POINTS, _CELLS):
                values = _values(leaf, association, "DensityGradientMagnitude")
                if values is not None and values.size:
                    largest = max(largest, float(values.max()))
        for leaf in _leaves(result):
            for association in (_POINTS, _CELLS):
                values = _values(leaf, association, "DensityGradientMagnitude")
                if values is not None:
                    _add(leaf, association, "Schlieren",
                         schlieren(values, largest, self._exponent))
