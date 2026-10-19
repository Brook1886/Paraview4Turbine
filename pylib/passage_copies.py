"""Repeating a passage, whichever way the machine repeats it.

Two filters do it -- Circumferential Copy turns the copies about the machine
axis, Linear Copy slides them along a straight cascade -- and only the step
differs. The walk over the input, the table that gives a blade row its own pitch
and its own number of copies, the labels each copy carries and the pass-by-
reference of everything but the points are all here, so neither filter is a copy
of the other.

A filter brings a :class:`Repeat`: what a named block repeats at and how often,
and what one step does to a dataset. :func:`copy_into` is then the whole of its
RequestData.

Nothing here imports paraview, only VTK, so a plugin can use it without the
servermanager having to be up.
"""

import re

import numpy as np

from vtkmodules.vtkCommonCore import (VTK_DOUBLE, VTK_FLOAT, vtkDoubleArray,
                                      vtkIntArray)
from vtkmodules.vtkCommonDataModel import vtkCompositeDataSet, vtkMultiBlockDataSet
from vtkmodules.vtkFiltersGeneral import (vtkImageDataToPointSet,
                                          vtkRectilinearGridToPointSet,
                                          vtkTransformFilter)


def unit(vector, what):
    """A direction of length one. ``what`` names it, for the complaint."""
    direction = np.asarray(vector, dtype=float)
    length = float(np.linalg.norm(direction))
    if length == 0.0:
        raise ValueError("The %s is a zero-length vector." % what)
    return direction / length


def _as_point_set(dataset):
    """vtkTransformFilter moves points, so a grid defined by extents needs its
    points made explicit first."""
    if dataset.IsA("vtkPointSet"):
        return dataset
    if dataset.IsA("vtkImageData"):
        convert = vtkImageDataToPointSet()
    elif dataset.IsA("vtkRectilinearGrid"):
        convert = vtkRectilinearGridToPointSet()
    else:
        return None
    convert.SetInputData(dataset)
    convert.Update()
    return convert.GetOutput()


def _would_be_mangled(array):
    """A triple the rotation must not be let near.

    Only a floating-point triple can hold a rotated vector. An integer one -- a
    colour, a triple of indices -- would come back with the components mixed and
    then wrapped around by its own type, so (10, 20, 30) as unsigned bytes turns
    into (237, 10, 30) rather than anything anyone asked for.
    """
    return (array.GetNumberOfComponents() == 3
            and array.GetDataType() not in (VTK_FLOAT, VTK_DOUBLE))


def _hold_back(dataset, rotate_vectors):
    """A shallow copy with what the rotation must not touch taken out of its
    way, and what to put back afterwards.

    vtkTransformFilter turns whatever carries the active-vectors or
    active-normals mark no matter what it has been told about the other arrays,
    and, when it is told to turn them all, every three-component array whatever
    its type. So the marks come off when Rotate Vectors is unticked, and the
    triples that cannot hold a vector come out either way.
    """
    plain = dataset.NewInstance()
    plain.ShallowCopy(dataset)
    held = []
    for attributes in (plain.GetPointData(), plain.GetCellData()):
        vectors, normals = attributes.GetVectors(), attributes.GetNormals()
        marks = (vectors.GetName() if vectors else None,
                 normals.GetName() if normals else None)
        if not rotate_vectors:
            attributes.SetActiveVectors(None)
            attributes.SetActiveNormals(None)
            removed = []
        else:
            removed = []
            for index in range(attributes.GetNumberOfArrays()):
                array = attributes.GetArray(index)
                if array is not None and _would_be_mangled(array):
                    removed.append(array)
            for array in removed:
                attributes.RemoveArray(array.GetName())
        held.append((marks, removed))
    return plain, held


def _put_back(dataset, held):
    """Undo :func:`_hold_back`, once the moving is done."""
    for attributes, (marks, removed) in zip(
            (dataset.GetPointData(), dataset.GetCellData()), held):
        for array in removed:
            attributes.AddArray(array)
        vectors, normals = marks
        if vectors:
            attributes.SetActiveVectors(vectors)
        if normals:
            attributes.SetActiveNormals(normals)


def transformed(dataset, transform, move_vectors):
    """One copy of a dataset, put where the transform says.

    Only the points are new: vtkTransformFilter passes the cells and the
    untouched arrays through by reference, so a full annulus costs one set of
    coordinates per copy rather than one whole mesh.

    The linear copy next door uses this too, with ``move_vectors`` off: a
    translation leaves every vector pointing where it did, so there is nothing
    for it to turn.
    """
    grid = _as_point_set(dataset)
    if grid is None:
        return None
    grid, held = _hold_back(grid, move_vectors)

    turn = vtkTransformFilter()
    turn.SetInputData(grid)
    turn.SetTransform(transform)
    # Rotates every three-component array left in front of it, not just the one
    # ParaView happens to have marked as the active vectors.
    turn.SetTransformAllInputVectors(bool(move_vectors))
    turn.Update()
    piece = turn.GetOutput().NewInstance()
    piece.ShallowCopy(turn.GetOutput())
    _put_back(piece, held)
    return piece


def _free_name(container, base):
    """A name nothing in the container is using yet.

    Copying an already copied mesh -- a sector of three, then twelve of those --
    would otherwise label the second round over the first, so the later stage
    becomes CopyId2 and the first one survives.
    """
    if container.GetAbstractArray(base) is None:
        return base
    stage = 2
    while container.GetAbstractArray("%s%d" % (base, stage)) is not None:
        stage += 1
    return "%s%d" % (base, stage)


def label_copy(piece, index, step, step_name):
    """Identify the copy, for coloring and for reading off how far it moved.

    CopyId goes on whichever attribute the data already uses, so it appears once
    in the coloring dropdown instead of twice. ``step_name`` is what the field
    data holding the step is called -- an angle here, a distance next door in
    the linear copy.
    """
    point_data = piece.GetPointData()
    cell_data = piece.GetCellData()
    on_cells = cell_data.GetNumberOfArrays() > point_data.GetNumberOfArrays()
    target = cell_data if on_cells else point_data
    count = piece.GetNumberOfCells() if on_cells else piece.GetNumberOfPoints()

    ids = vtkIntArray()
    ids.SetName(_free_name(target, "CopyId"))
    ids.SetNumberOfTuples(count)
    ids.Fill(index)
    target.AddArray(ids)

    steps = vtkDoubleArray()
    steps.SetName(_free_name(piece.GetFieldData(), step_name))
    steps.SetNumberOfTuples(1)
    steps.SetValue(0, step)
    piece.GetFieldData().AddArray(steps)


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


def _block_name(source, index):
    """What a block of a composite dataset is called, or None."""
    if not source.HasMetaData(index):
        return None
    metadata = source.GetMetaData(index)
    return (metadata.Get(vtkCompositeDataSet.NAME())
            if metadata.Has(vtkCompositeDataSet.NAME()) else None)


def _copy_name(source, source_index, target, target_index):
    """Give a block of the output the name its block of the input had."""
    name = _block_name(source, source_index)
    if name is not None:
        target.GetMetaData(target_index).Set(vtkCompositeDataSet.NAME(), name)


def number_of_copies(count):
    """A cell of the table's copies column, which has to be a count."""
    if count < 1:
        raise ValueError("a row cannot be copied %g times" % count)
    return int(round(count))


def _sentence(words):
    """A complaint with a capital letter on it, and its own name left alone --
    str.capitalize() would turn the IGV line into the igv one."""
    return words[:1].upper() + words[1:]


def _cell(written, convert, line, column):
    """One number out of the table, or None where the cell is empty.

    An empty cell is a line saying nothing about that column rather than a line
    saying zero, so it keeps whatever the nameless line says instead. Anything
    the cell cannot be is complained about by where it is, since a table of
    three lines has three places to have gone wrong.
    """
    written = (written or "").strip()
    if not written or written == "None":
        return None
    try:
        number = float(written)
    except ValueError:
        raise ValueError('"%s" is not a number, in the %s of %s.'
                         % (written, column, line))
    try:
        return convert(number)
    except ValueError as error:
        raise ValueError(_sentence("%s, on %s." % (str(error).rstrip("."), line)))


def read_rows(rows, measure, column="blade count"):
    """{block name: (pitch, copies)} from the panel's table of blade rows.

    A line is (name, what the copies are apart, how many of them), and either
    number may be left out. ``measure`` turns the middle column into a pitch --
    a blade count into degrees for a rotation, a distance straight through for a
    translation -- so each filter's table is written in its own unit, and
    ``column`` is what to call that column when it does not read as a number.

    The line with no name is kept along with the rest, under the empty name: it
    is what a block none of the others name goes by, and a mesh with no blade
    rows in it has nothing but that line.
    """
    table = {}
    for name, apart, copies in rows:
        name = (name or "").strip()
        line = "the %s line" % name if name else "the line with no name"
        if name in table:
            # Two lines for one row is not a thing to pick a winner between.
            raise ValueError(_sentence("%s is there twice, and the two "
                                       "disagree; delete one." % line))
        table[name] = (_cell(apart, measure, line, column),
                       _cell(copies, number_of_copies, line, "copies"))
    return table


def _plain(name):
    """A block name without the "Block 3_" a nameless block picks up on its way
    through ParaView, so a table entry matches what the user sees."""
    return re.sub(r"^Block \d+_", "", name)


class Repeat(object):
    """How a filter turns one dataset into a row of copies.

    The walk over the input tree is the same whether the copies are turned about
    an axis or slid along one, so :func:`duplicate` takes one of these: what a
    named block repeats at and how often, the steps to make from that, and what
    one step does to a dataset. Circumferential Copy makes a :class:`Turn`; the
    linear copy next door makes a Slide of its own.

    Everything comes off the table. A blade row is a named block; the line with
    no name is what the rest of the input goes by. Which names the input turned
    out to have is remembered, so the filter can say which lines matched nothing
    rather than leaving a mistyped one to do nothing at all -- and so is any
    block the table left with no pitch, which is copied once, where it is.
    """

    # Set by each filter: what the field data saying how far a copy moved is
    # called, and the word for what it does to a dataset.
    step_name = None
    verb = None

    def __init__(self, rows):
        self._rows = rows             # block name -> (its pitch, its copies)
        self._matched = set()
        self._unset = set()
        self._unmovable = set()

    @property
    def start(self):
        """(pitch, copies) for a block none of the named lines claim."""
        return self._rows.get("", (None, None))

    @property
    def unmatched(self):
        """The named lines of the table that named nothing in the input."""
        return sorted(set(self._rows) - self._matched - {""})

    @property
    def unset(self):
        """The blocks the table said nothing about."""
        return sorted(self._unset)

    def under(self, name, inherited):
        """(pitch, copies) under a block: its own where the table names it.

        Matched on the block's name at whatever depth it sits, so a row of a
        VECLYS case hands both down to every boundary underneath it. A cell left
        empty keeps what came from above, so a line can set the copies alone.
        """
        plain = None if name is None else _plain(name)
        own = self._rows.get(plain) if plain is not None else None
        if own is None:
            return inherited
        self._matched.add(plain)
        pitch, copies = inherited
        return (pitch if own[0] is None else own[0],
                copies if own[1] is None else own[1])

    def count(self, pitch, copies):
        """How many copies of a block at this pitch. One, when nothing says."""
        return copies or 1

    def steps(self, setting, name):
        """How far each copy of a dataset here is moved.

        A block with no pitch anywhere in the table is left where it is rather
        than failing the whole filter, and named in the report afterwards.
        """
        pitch, copies = setting
        if not pitch:
            self._unset.add(name or "the input")
            return [0.0]
        count = max(1, int(self.count(pitch, copies)))
        return [index * pitch for index in range(count)]

    def move(self, dataset, step):
        """One copy, moved by a step. None for a dataset that cannot be."""
        raise NotImplementedError

    def skip(self, dataset):
        """Give up on a dataset there is no moving, for the report to name."""
        self._unmovable.add(dataset.GetClassName())

    def report(self):
        """Everything that did not go as asked, in words, for the filter to
        print. Nothing here fails the filter: what could be copied was."""
        told = []
        if self._unmovable:
            told.append("cannot %s %s."
                        % (self.verb, ", ".join(sorted(self._unmovable))))
        if self.unmatched:
            told.append("the table names %s, which is not a block of the input."
                        % ", ".join(self.unmatched))
        if self.unset:
            told.append("nothing in the table says how far apart the copies of "
                        "%s are, so it is left where it is."
                        % ", ".join(self.unset))
        return told


def duplicate(node, repeat, setting, name=None):
    """Walk the input, replacing every dataset with its multiblock of copies.

    The input tree is kept, so a boundary that was one block stays one block and
    the Multi-block Inspector can still show or hide it across every copy at
    once. A block the table of blade rows names takes its pitch and its count
    from there and hands them down, which is how one filter copies a whole stage
    with each row turning by its own pitch as often as its own line says; a
    block none of them name goes by the line with no name on it.
    """
    if node is None:
        return None
    setting = repeat.under(name, setting)
    children = _children(node) if node.IsA("vtkCompositeDataSet") else None
    if children is not None:
        branch = vtkMultiBlockDataSet()
        branch.SetNumberOfBlocks(len(children))
        for index, child in enumerate(children):
            branch.SetBlock(index, duplicate(child, repeat, setting,
                                             _block_name(node, index)))
            _copy_name(node, index, branch, index)
        return branch

    steps = repeat.steps(setting, name)
    copies = vtkMultiBlockDataSet()
    copies.SetNumberOfBlocks(len(steps))
    for index, step in enumerate(steps):
        piece = repeat.move(node, step)
        if piece is None:
            repeat.skip(node)
            return None
        label_copy(piece, index, step, repeat.step_name)
        copies.SetBlock(index, piece)
        copies.GetMetaData(index).Set(vtkCompositeDataSet.NAME(), "Copy %d" % index)
    return copies


def copy_into(label, make_repeat, source, output):
    """Fill the output with the copies of the source, and say what went wrong.

    Both filters do this and nothing else, so it lives here and takes the panel
    -- as the method that reads it, since reading a table of nonsense is one of
    the things that can go wrong. False when nothing could be made of it at all,
    which is the only thing that fails the pipeline: a block that could not be
    moved, or that the table says nothing about, is reported and the rest is
    still drawn.
    """
    try:
        repeat = make_repeat()
        result = duplicate(source, repeat, repeat.start)
        for message in repeat.report():
            print("%s: %s" % (label, message))
        if result is not None:
            output.ShallowCopy(result)
    except Exception as error:
        print("%s: %s" % (label, error))
        return False
    return True
