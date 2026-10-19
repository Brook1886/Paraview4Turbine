"""ParaView plugin: open a VECLYS case by its ``input.au3x``.

A solver run is a directory with an ``input.au3x`` in it, and the mesh it wrote
is buried a few folders down under names the input file only implies. This
reader takes the input file and works the rest out, so opening a case is opening
the file that describes it rather than remembering where the CGNS went.

What the file says is read by ``veclys_case`` in ``pylib``, which the copy
macro reads it with as well, so the plugin and the toolbar button agree about
what a case is: a device list of comma-separated chains naming the folders, one
mesh per leaf, and a ``[<row>]`` section apiece holding the settings that row was
run with.

The reading itself is the CGNS labeled-boundary reader's, one of them per row,
driven from in here the way that reader drives ParaView's own: a VECLYS case is
a set of CGNS files that have to be found first.
"""

import os
import sys
import weakref

try:                                  # ParaView imports a plugin by its path.
    _HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:                     # ...unless it does not, in which case the
    _HERE = os.getcwd()               # plugins are wherever we started.
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# The toolbar macros are run by ParaView as bare strings, with no __file__ of
# their own to find their common code by. A plugin has one, so it puts pylib
# where a macro can import it from however ParaView was started.
_PYLIB = os.path.join(os.path.dirname(_HERE), "pylib")
if _PYLIB not in sys.path:
    sys.path.append(_PYLIB)

# ParaView's decorators hand back a function that builds the class rather than
# the class itself, so this can be called but not inherited from.
from cgns_labeled_boundaries import CGNSLabeledBoundaryReader  # noqa: E402

from paraview.util.vtkAlgorithm import (smdomain, smhint,  # noqa: E402
                                        smproperty, smproxy)
from vtkmodules.util.vtkAlgorithm import VTKPythonAlgorithmBase  # noqa: E402
from vtkmodules.vtkCommonCore import vtkDataArraySelection  # noqa: E402
from vtkmodules.vtkCommonDataModel import (vtkCompositeDataSet,  # noqa: E402
                                           vtkMultiBlockDataSet)

# The case file's own reader, shared with the copy macro. device_chains comes
# with it because the verification reaches for that one through the plugin.
from veclys_case import case_meshes, device_chains  # noqa: E402,F401


# The label is what paraview.simple names its constructor after, so it stays as
# it is: VECLYS Reader would make pvs.VeclysReader into pvs.VECLYSReader and
# break every script that opens a case.
@smproxy.reader(name="VeclysReader", label="Veclys Reader",
                extensions="au3x", file_description="VECLYS case")
@smproperty.xml("""
    <Property name="Reload" label="Reload Case" command="Reload"
              panel_widget="command_button">
      <Documentation>Look for the case again. A run that had not written its
      mesh when the case was opened is picked up by this.</Documentation>
    </Property>""")
@smproperty.xml("""
    <StringVectorProperty name="CaseNumber" label="Case Number"
                          command="SetCaseNumber" number_of_elements="1"
                          default_values="0000">
      <Documentation>The case the run wrote, which names the folder each mesh is
      in. Runs so far have only ever been case 0000.</Documentation>
    </StringVectorProperty>""")
class VeclysReader(VTKPythonAlgorithmBase):
    """A VECLYS case: a CGNS reader per blade row, pointed at what input.au3x
    implies."""

    def __init__(self):
        VTKPythonAlgorithmBase.__init__(self, nInputPorts=0, nOutputPorts=1,
                                        outputType="vtkMultiBlockDataSet")
        self._case_file = None
        self._case_number = "0000"
        self._domains = []            # [(row name, mesh path)] from the input
        self._readers = {}            # row name -> its CGNS reader
        self._resolved = False
        self._described = False
        self._convert = True          # Cell Data to Point Data, see below
        # My title -> (row, the title that row's own reader knows it by), since
        # every row has a HUB and a CASING and they cannot all be called that.
        self._volume_titles = {}
        self._boundary_titles = {}

        self._active = self._make_selection()
        self._volumes = self._make_selection()
        self._boundaries = self._make_selection()
        self._cell_arrays = self._make_selection()
        self._point_arrays = self._make_selection()

    def _make_selection(self):
        selection = vtkDataArraySelection()
        reference = weakref.ref(self)

        def mark_modified(*_args, **_kwargs):
            target = reference()
            if target is not None:
                target._described = False
                target.Modified()

        selection.AddObserver("ModifiedEvent", mark_modified)
        return selection

    @staticmethod
    def _fill(selection, names):
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

    @staticmethod
    def _titles(selection):
        return [selection.GetArrayName(index)
                for index in range(selection.GetNumberOfArrays())]

    # ------------------------------------------------------------------ setup

    def _given_file(self):
        """The case file the panel points at.

        ParaView starts a string property off at the literal "None", so that is
        not a file anybody asked for.
        """
        return self._case_file if self._case_file not in (None, "", "None") else None

    def _resolve(self):
        """Find the mesh each row of the case wrote.

        Left until the pipeline asks rather than done as the panel is filled in,
        so that setting a file name does not go reading the disk. The flag is
        set by the work rather than before it, and only when the case came out
        whole: a run that has not written a mesh yet is worth asking about
        again, which Reload and anything else that touches the panel will do.
        """
        if self._resolved:
            return
        self._domains = []
        given = self._given_file()
        if not given:
            self._readers, self._resolved = {}, True
            return
        try:
            meshes = case_meshes(given, self._case_number)
        except Exception as error:
            print("VECLYS reader: %s" % error)
            self._readers = {}
            return
        whole, found = bool(meshes), {}
        for name, mesh in meshes:
            if not os.path.exists(mesh):
                print("VECLYS reader: %s has no mesh at %s yet." % (name, mesh))
                whole = False
                continue
            # The readers are dropped whenever the case changes, so any left
            # over belong to this one and are worth keeping: rebuilding one
            # would read its index again for nothing.
            reader = self._readers.get(name)
            if reader is None:
                reader = CGNSLabeledBoundaryReader()
                reader.SetFileName(mesh)
                reader.SetCellDataToPointData(self._convert)
            found[name] = reader
            self._domains.append((name, mesh))
        self._readers = found
        self._fill(self._active, [name for name, _mesh in self._domains])
        self._resolved = whole

    def _enabled_domains(self):
        return [(name, mesh) for name, mesh in self._domains
                if self._active.ArrayIsEnabled(name)]

    def _describe(self):
        """Ask every row what it holds and offer it all as one set of lists.

        A name only carries its row's name when more than one row uses it, the
        way the CGNS reader only qualifies a section with its zone when the
        section name is not unique. Which rows are ticked does not come into it:
        the count is over the whole case, so unticking a row leaves the names of
        the others -- and the ticks against them -- where they were.
        """
        if self._described:
            return
        self._volume_titles, self._boundary_titles = {}, {}

        answered, held = True, {}
        for name, _mesh in self._domains:
            reader = self._readers[name]
            try:
                reader.UpdateInformation()
            except Exception as error:
                print("VECLYS reader: %s: %s" % (name, error))
                answered = False
                continue
            held[name] = (self._titles(reader.GetVolumesSelection()),
                          self._titles(reader.GetBoundariesSelection()),
                          self._titles(reader.GetCellArraysSelection()),
                          self._titles(reader.GetPointArraysSelection()))

        enabled = [name for name, _mesh in self._enabled_domains() if name in held]
        for group, index in (("volume", 0), ("boundary", 1)):
            counts = {}
            for name in held:
                for title in held[name][index]:
                    counts[title] = counts.get(title, 0) + 1
            combined, order = {}, []
            for name in enabled:
                for title in held[name][index]:
                    mine = title if counts[title] == 1 else "%s/%s" % (name, title)
                    combined[mine] = (name, title)
                    order.append(mine)
            if group == "volume":
                self._volume_titles = combined
                self._fill(self._volumes, order)
            else:
                self._boundary_titles = combined
                self._fill(self._boundaries, order)

        for index, selection in ((2, self._cell_arrays), (3, self._point_arrays)):
            union = []
            for name in enabled:
                for title in held[name][index]:
                    if title not in union:
                        union.append(title)
            self._fill(selection, union)
        # Last, because filling the lists is itself a change and would otherwise
        # put the flag back; and only if every row answered, so one that did not
        # is asked again rather than left out for good.
        self._described = answered

    def _hand_down(self, name, reader):
        """Give one row's reader the part of the panel that belongs to it."""
        for selection, titles in ((reader.GetVolumesSelection(), self._volume_titles),
                                  (reader.GetBoundariesSelection(), self._boundary_titles)):
            wanted = {own for mine, (row, own) in titles.items()
                      if row == name and self._selected(mine, titles)}
            for title in self._titles(selection):
                if title in wanted:
                    selection.EnableArray(title)
                else:
                    selection.DisableArray(title)
        for selection, mine in ((reader.GetCellArraysSelection(), self._cell_arrays),
                                (reader.GetPointArraysSelection(), self._point_arrays)):
            for title in self._titles(selection):
                if mine.ArrayExists(title) and not mine.ArrayIsEnabled(title):
                    selection.DisableArray(title)
                else:
                    selection.EnableArray(title)

    def _selected(self, title, titles):
        return (self._volumes.ArrayIsEnabled(title)
                if titles is self._volume_titles
                else self._boundaries.ArrayIsEnabled(title))

    # ------------------------------------------------------------- properties

    def _forget(self):
        """Start the case again from the input file."""
        self._readers = {}
        self._resolved = self._described = False
        self.Modified()

    def Reload(self):
        """Look for the case again.

        There is nothing else to press when a run writes the mesh after the case
        was opened: the panel has not changed, so ParaView has no reason to ask
        the reader anything.
        """
        self._forget()

    def SetCaseNumber(self, value):
        value = (value or "").strip()
        if value != self._case_number:
            self._case_number = value
            self._forget()

    @smproperty.stringvector(name="FileName", panel_visibility="never")
    @smdomain.filelist()
    @smhint.filechooser(extensions="au3x", file_description="VECLYS case")
    def SetFileName(self, name):
        if name != self._case_file:
            self._case_file = name
            self._forget()

    @smproperty.dataarrayselection(name="Domains")
    def GetActiveDomainsSelection(self):
        return self._active

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
        self._convert = bool(value)
        for reader in self._readers.values():
            reader.SetCellDataToPointData(value)
        self.Modified()

    # ---------------------------------------------------------------- execute

    def RequestInformation(self, _request, _in_info, _out_info):
        # Fills the checkbox lists, which is what the panel shows before Apply.
        self._resolve()
        self._describe()
        return 1

    def RequestData(self, _request, _in_info, out_info):
        output = vtkMultiBlockDataSet.GetData(out_info, 0)
        output.Initialize()
        self._resolve()
        if not self._given_file():
            return 1
        try:
            self._describe()
            for name, _mesh in self._enabled_domains():
                reader = self._readers[name]
                self._hand_down(name, reader)
                reader.Update()
                row = vtkMultiBlockDataSet()
                row.ShallowCopy(reader.GetOutputDataObject(0))
                index = output.GetNumberOfBlocks()
                output.SetBlock(index, row)
                output.GetMetaData(index).Set(vtkCompositeDataSet.NAME(), name)
        except Exception as error:
            print("VECLYS reader: %s" % error)
            return 0
        return 1
