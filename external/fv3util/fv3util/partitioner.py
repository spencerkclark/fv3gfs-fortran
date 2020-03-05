from typing import Tuple
import functools
import dataclasses
from . import constants, utils
from .constants import (
    TOP, BOTTOM, LEFT, RIGHT, TOP_LEFT, TOP_RIGHT, BOTTOM_LEFT, BOTTOM_RIGHT
)
import numpy as np
from . import boundary as bd
from .quantity import QuantityMetadata, Quantity

BOUNDARY_CACHE_SIZE = None


def get_tile_index(rank, total_ranks):
    """Returns the tile number for a given rank and total number of ranks.
    """
    if total_ranks % 6 != 0:
        raise ValueError(f'total_ranks {total_ranks} is not evenly divisible by 6')
    ranks_per_tile = total_ranks // 6
    return rank // ranks_per_tile


def get_tile_number(tile_rank, total_ranks):
    """Returns the tile number for a given rank and total number of ranks.
    """
    FutureWarning(
        'get_tile_number will be removed in a later version, '
        'use get_tile_index(rank, total_ranks) + 1 instead'
    )
    if total_ranks % 6 != 0:
        raise ValueError(f'total_ranks {total_ranks} is not evenly divisible by 6')
    ranks_per_tile = total_ranks // 6
    return tile_rank // ranks_per_tile + 1


@dataclasses.dataclass
class HorizontalGridSpec:
    layout: Tuple[int, int]

    @classmethod
    def from_namelist(cls, namelist):
        """Create a Partitioner from a Fortran namelist. Infers dimensions in number
        of grid cell centers based on namelist parameters.

        Args:
            namelist (dict): the Fortran namelist
        """
        return cls(layout=namelist['fv_core_nml']['layout'])

    @property
    def is_square(self):
        return self.layout[0] == self.layout[1]


class TilePartitioner:

    def __init__(
            self,
            grid: HorizontalGridSpec
    ):
        """Create an object for fv3gfs tile decomposition.
        """
        self.grid = grid

    @functools.lru_cache(maxsize=BOUNDARY_CACHE_SIZE)
    def subtile_index(self, rank):
        """Return the (y, x) subtile position of a given rank as an integer number of subtiles."""
        return subtile_index(rank, self.total_ranks, self.grid.layout)

    @property
    def total_ranks(self):
        return self.grid.layout[0] * self.grid.layout[1]

    def tile_extent(self, rank_metadata: QuantityMetadata) -> Tuple[int, ...]:
        """Return the shape of a full tile representation for the given dimensions.

        Args:
            metadata: quantity metadata

        Returns:
            extent: shape of full tile representation
        """
        return tile_extent_from_rank_metadata(rank_metadata.dims, rank_metadata.extent, self.grid.layout)

    def subtile_extent(self, tile_metadata: QuantityMetadata) -> Tuple[int, ...]:
        """Return the shape of a single rank representation for the given dimensions."""
        return rank_extent_from_tile_metadata(
            tile_metadata.dims, tile_metadata.extent, self.grid.layout)

    def subtile_slice(
            self,
            rank,
            tile_metadata: QuantityMetadata,
            overlap: bool = False) -> Tuple[slice, slice]:
        """Return the subtile slice of a given rank on an array.

        Args:
            rank: the rank of the process
            tile_metadata: the metadata for a quantity on a tile
            overlap (optional): if True, for interface variables include the part
                of the array shared by adjacent ranks in both ranks. If False, ensure
                only one of those ranks (the greater rank) is assigned the overlapping
                section. Default is False.

        Returns:
            y_range: the y range of the array on the tile
            x_range: the x range of the array on the tile
        """
        return subtile_slice(
            tile_metadata.dims,
            tile_metadata.extent,
            self.grid.layout,
            self.subtile_index(rank),
            overlap=overlap
        )

    def on_tile_top(self, rank):
        return on_tile_top(self.subtile_index(rank), self.grid.layout)

    def on_tile_bottom(self, rank):
        return on_tile_bottom(self.subtile_index(rank))

    def on_tile_left(self, rank):
        return on_tile_left(self.subtile_index(rank))

    def on_tile_right(self, rank):
        return on_tile_right(self.subtile_index(rank), self.grid.layout)


class CubedSpherePartitioner:

    def __init__(
            self,
            grid: HorizontalGridSpec
    ):
        """Create an object for fv3gfs domain decomposition.
        
        Args:
            ny: number of grid cell centers along the y-direction
            nx: number of grid cell centers along the x-direction
            layout: (x_subtiles, y_subtiles) specifying how the tile is split in the
                horizontal across multiple processes each with their own subtile.
        """
        self.grid = grid
        self.tile = TilePartitioner(self.grid)

    def _ensure_square_layout(self):
        if not self.grid.is_square:
            raise NotImplementedError('currently only square layouts are supported')

    def tile_index(self, rank):
        """Return the tile index of a given rank"""
        return get_tile_index(rank, self.total_ranks)

    def tile_master_rank(self, rank):
        """Return the lowest rank on the same tile as a given rank."""
        return self.tile.total_ranks * (rank // self.tile.total_ranks)

    @property
    def layout(self):
        return self.grid.layout

    @property
    def total_ranks(self):
        return 6 * self.tile.total_ranks

    def boundary(self, boundary_type, rank):
        return {
            LEFT: self._left_edge,
            RIGHT: self._right_edge,
            TOP: self._top_edge,
            BOTTOM: self._bottom_edge,
            TOP_LEFT: self._top_left_corner,
            TOP_RIGHT: self._top_right_corner,
            BOTTOM_LEFT: self._bottom_left_corner,
            BOTTOM_RIGHT: self._bottom_right_corner,
        }[boundary_type](rank)

    @functools.lru_cache(maxsize=BOUNDARY_CACHE_SIZE)
    def _left_edge(self, rank):
        self._ensure_square_layout()
        if self.tile.on_tile_left(rank):
            if is_even(self.tile_index(rank)):
                to_master_rank = self.tile_master_rank(rank - 2 * self.tile.total_ranks)
                tile_rank = rank % self.tile.total_ranks
                to_tile_rank = fliplr_subtile_rank(
                    rotate_subtile_rank(
                        tile_rank, self.layout, n_clockwise_rotations=1
                    ),
                    self.layout
                )
                to_rank = to_master_rank + to_tile_rank
                rotations = 1
            else:
                to_rank = rank - self.tile.total_ranks + self.layout[0] - 1
                rotations = 0
        else:
            to_rank = rank - 1
            rotations = 0
        to_rank = to_rank % self.total_ranks
        return bd.SimpleBoundary(
            boundary_type=constants.LEFT,
            from_rank=rank,
            to_rank=to_rank,
            n_clockwise_rotations=rotations
        )

    @functools.lru_cache(maxsize=BOUNDARY_CACHE_SIZE)
    def _right_edge(self, rank):
        self._ensure_square_layout()
        self._ensure_square_layout()
        if self.tile.on_tile_right(rank):
            if not is_even(self.tile_index(rank)):
                to_master_rank = self.tile_master_rank(rank + 2 * self.tile.total_ranks)
                tile_rank = rank % self.tile.total_ranks
                to_tile_rank = fliplr_subtile_rank(
                    rotate_subtile_rank(
                        tile_rank, self.layout, n_clockwise_rotations=1
                    ),
                    self.layout
                )
                to_rank = to_master_rank + to_tile_rank
                rotations = 1
            else:
                to_rank = rank + self.tile.total_ranks - self.layout[0] + 1
                rotations = 0
        else:
            to_rank = rank + 1
            rotations = 0
        to_rank = to_rank % self.total_ranks
        return bd.SimpleBoundary(
            boundary_type=constants.RIGHT,
            from_rank=rank,
            to_rank=to_rank,
            n_clockwise_rotations=rotations
        )

    @functools.lru_cache(maxsize=BOUNDARY_CACHE_SIZE)
    def _top_edge(self, rank):
        self._ensure_square_layout()
        if self.tile.on_tile_top(rank):
            if is_even(self.tile_index(rank)):
                to_master_rank = (self.tile_index(rank) + 2) * self.tile.total_ranks
                tile_rank = rank % self.tile.total_ranks
                to_tile_rank = fliplr_subtile_rank(
                    rotate_subtile_rank(
                        tile_rank, self.layout, n_clockwise_rotations=1
                    ),
                    self.layout
                )
                to_rank = to_master_rank + to_tile_rank
                rotations = 3
            else:
                to_master_rank = (self.tile_index(rank) + 1) * self.tile.total_ranks
                tile_rank = rank % self.tile.total_ranks
                to_tile_rank = flipud_subtile_rank(tile_rank, self.layout)
                to_rank = to_master_rank + to_tile_rank
                rotations = 0
        else:
            to_rank = rank + self.layout[1]
            rotations = 0
        to_rank = to_rank % self.total_ranks
        return bd.SimpleBoundary(
            boundary_type=constants.TOP,
            from_rank=rank,
            to_rank=to_rank,
            n_clockwise_rotations=rotations
        )

    @functools.lru_cache(maxsize=BOUNDARY_CACHE_SIZE)
    def _bottom_edge(self, rank):
        self._ensure_square_layout()
        if (
                self.tile.on_tile_bottom(rank) and
                not is_even(self.tile_index(rank))
        ):
            to_master_rank = (self.tile_index(rank) - 2) * self.tile.total_ranks
            tile_rank = rank % self.tile.total_ranks
            to_tile_rank = fliplr_subtile_rank(
                rotate_subtile_rank(
                    tile_rank, self.layout, n_clockwise_rotations=1
                ),
                self.layout
            )
            to_rank = to_master_rank + to_tile_rank
            rotations = 3
        else:
            to_rank = rank - self.layout[1]
            rotations = 0
        to_rank = to_rank % self.total_ranks
        return bd.SimpleBoundary(
            boundary_type=constants.BOTTOM,
            from_rank=rank,
            to_rank=to_rank,
            n_clockwise_rotations=rotations
        )

    def _top_left_corner(self, rank):
        if (self.tile.on_tile_top(rank) and
                self.tile.on_tile_left(rank)):
            corner = None
        else:
            if is_even(self.tile_index(rank)) and on_tile_left(self.tile.subtile_index(rank)):
                second_edge = self._left_edge
            else:
                second_edge = self._top_edge
            corner = self._get_corner(constants.TOP_LEFT, rank, self._left_edge, second_edge)
        return corner

    def _top_right_corner(self, rank):
        if (on_tile_top(self.tile.subtile_index(rank), self.layout) and
                on_tile_right(self.tile.subtile_index(rank), self.layout)):
            corner = None
        else:
            if is_even(self.tile_index(rank)) and on_tile_top(self.tile.subtile_index(rank), self.layout):
                second_edge = self._bottom_edge
            else:
                second_edge = self._right_edge
            corner = self._get_corner(constants.TOP_RIGHT, rank, self._top_edge, second_edge)
        return corner

    def _bottom_left_corner(self, rank):
        if (on_tile_bottom(self.tile.subtile_index(rank)) and
                on_tile_left(self.tile.subtile_index(rank))):
            corner = None
        else:
            if not is_even(self.tile_index(rank)) and on_tile_bottom(self.tile.subtile_index(rank)):
                second_edge = self._top_edge
            else:
                second_edge = self._left_edge
            corner = self._get_corner(constants.BOTTOM_LEFT, rank, self._bottom_edge, second_edge)
        return corner

    def _bottom_right_corner(self, rank):
        if (on_tile_bottom(self.tile.subtile_index(rank)) and
                on_tile_right(self.tile.subtile_index(rank), self.layout)):
            corner = None
        else:
            if not is_even(self.tile_index(rank)) and on_tile_bottom(self.tile.subtile_index(rank)):
                second_edge = self._bottom_edge
            else:
                second_edge = self._right_edge
            corner = self._get_corner(constants.BOTTOM_RIGHT, rank, self._bottom_edge, second_edge)
        return corner

    def _get_corner(self, boundary_type, rank, edge_func_1, edge_func_2):
        edge_1 = edge_func_1(rank)
        edge_2 = edge_func_2(edge_1.to_rank)
        rotations = edge_1.n_clockwise_rotations + edge_2.n_clockwise_rotations
        return bd.SimpleBoundary(
            boundary_type=boundary_type,
            from_rank=rank,
            to_rank=edge_2.to_rank,
            n_clockwise_rotations=rotations
        )


def on_tile_left(subtile_index):
    return subtile_index[1] == 0


def on_tile_right(subtile_index, layout):
    return subtile_index[1] == layout[1] - 1


def on_tile_top(subtile_index, layout):
    return subtile_index[0] == layout[0] - 1


def on_tile_bottom(subtile_index):
    return subtile_index[0] == 0


def rotate_subtile_rank(rank, layout, n_clockwise_rotations):
    if n_clockwise_rotations == 0:
        to_tile_rank = rank
    elif n_clockwise_rotations == 1:
        total_ranks = layout[0] * layout[1]
        rank_array = np.arange(total_ranks).reshape(layout)
        rotated_rank_array = np.rot90(rank_array)
        to_tile_rank = rank_array[np.where(rotated_rank_array == rank)][0]
    else:
        raise NotImplementedError()
    return to_tile_rank


def transpose_subtile_rank(rank, layout):
    return transform_subtile_rank(np.transpose, rank, layout)


def fliplr_subtile_rank(rank, layout):
    return transform_subtile_rank(np.fliplr, rank, layout)


def flipud_subtile_rank(rank, layout):
    return transform_subtile_rank(np.flipud, rank, layout)


def transform_subtile_rank(transform_func, rank, layout):
    total_ranks = layout[0] * layout[1]
    rank_array = np.arange(total_ranks).reshape(layout)
    fliplr_rank_array = transform_func(rank_array)
    return rank_array[np.where(fliplr_rank_array == rank)][0]


def subtile_index(rank, ranks_per_tile, layout):
    within_tile_rank = rank % ranks_per_tile
    j = within_tile_rank // layout[1]
    i = within_tile_rank % layout[1]
    return j, i


def is_even(value):
    return value % 2 == 0


def tile_extent_from_rank_metadata(dims, rank_extent, layout):
    layout_factors = np.asarray(
        utils.list_by_dims(dims, layout, non_horizontal_value=1))
    return extent_from_metadata(dims, rank_extent, layout_factors)


def rank_extent_from_tile_metadata(dims, tile_extent, layout):
    layout_factors = 1 / np.asarray(
        utils.list_by_dims(dims, layout, non_horizontal_value=1))
    return extent_from_metadata(dims, tile_extent, layout_factors)


def extent_from_metadata(dims: Tuple[str, ...], extent: Tuple[int, ...], layout_factors: np.ndarray):
    return_extents = []
    for dim, rank_extent, layout_factor in zip(dims, extent, layout_factors):
        if dim in constants.INTERFACE_DIMS:
            add_extent = -1
        else:
            add_extent = 0
        tile_extent = (rank_extent + add_extent) * layout_factor - add_extent
        return_extents.append(int(tile_extent))  # layout_factor is float, need to cast
    return tuple(return_extents)


@dataclasses.dataclass
class _IndexData1D:
    dim: str
    extent: int
    i_subtile: int
    n_ranks: int

    @property
    def base_extent(self):
        return self.extent - self.extent_minus_gridcell_count

    @property
    def extent_minus_gridcell_count(self):
        if self.dim in constants.INTERFACE_DIMS:
            return 1
        else:
            return 0

    @property
    def is_end_index(self):
        return self.i_subtile == self.n_ranks - 1


def _index_generator(dims, tile_extent, subtile_index, horizontal_layout):
    subtile_extent = rank_extent_from_tile_metadata(dims, tile_extent, horizontal_layout)
    quantity_layout = utils.list_by_dims(dims, horizontal_layout, non_horizontal_value=1)
    quantity_subtile_index = utils.list_by_dims(dims, subtile_index, non_horizontal_value=0)
    for dim, extent, i_subtile, n_ranks in zip(
            dims, subtile_extent, quantity_subtile_index, quantity_layout):
        yield _IndexData1D(dim, extent, i_subtile, n_ranks)


def subtile_slice(dims, tile_extent, layout, subtile_index, overlap=False):
    return_list = []
    # discard last index for interface variables, unless you're the last rank
    # done so that only one rank is responsible for the shared interface point
    for index in _index_generator(dims, tile_extent, subtile_index, layout):
        start = index.i_subtile * index.base_extent
        if index.is_end_index or overlap:
            end = start + index.extent
        else:
            end = start + index.base_extent
        return_list.append(slice(start, end))
    return tuple(return_list)
