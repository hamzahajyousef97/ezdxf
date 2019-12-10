# Created: 2019-01-04
# Copyright (c) 2019 Manfred Moitzi
# License: MIT License
from typing import Union, Iterable, List, TYPE_CHECKING
import math
from .vector import Vec2
from .construct2d import ConstructionTool
from .bbox import BoundingBox2d

if TYPE_CHECKING:
    from ezdxf.eztypes import Vertex


class Shape2d(ConstructionTool):
    """
    2D geometry object as list of :class:`Vec2` objects, vertices can be moved, rotated and scaled.

    Args:
        vertices: iterable of :class:`Vec2` compatible objects.

    """

    def __init__(self, vertices: Iterable['Vertex'] = None):
        self.vertices = [] if vertices is None else Vec2.list(vertices)  # type: List[Vec2]

    def move(self, dx: float, dy: float) -> None:
        """
        Move shape about `dx` in x-axis and about `dy` in y-axis.

        Args:
            dx: translation in x-axis
            dy: translation in y-axis

        """
        self.translate(Vec2((dx, dy)))

    @property
    def bounding_box(self) -> BoundingBox2d:
        """ :class:`BoundingBox2d` """
        return BoundingBox2d(self.vertices)

    def translate(self, vector: 'Vertex') -> None:
        """ Translate shape about `vector`. """
        delta = Vec2(vector)
        self.vertices = [v + delta for v in self.vertices]

    def scale(self, sx: float = 1., sy: float = 1.) -> None:
        """ Scale shape about `sx` in x-axis and `sy` in y-axis. """
        self.vertices = [Vec2((v.x * sx, v.y * sy)) for v in self.vertices]

    def scale_uniform(self, scale: float) -> None:
        """ Scale shape uniform about `scale` in x- and y-axis. """
        self.vertices = [v * scale for v in self.vertices]

    def rotate(self, angle: float, center: 'Vertex' = None) -> None:
        """ Rotate shape around rotation `center` about `angle` in degrees. """
        self.rotate_rad(math.radians(angle), center)

    def rotate_rad(self, angle: float, center: 'Vertex' = None) -> None:
        """ Rotate shape around rotation `center` about `angle` in radians. """
        if center is not None:
            center = Vec2(center)
            self.translate(-center)  # faster than a Matrix44 multiplication
        self.vertices = [v.rotate(angle) for v in self.vertices]
        if center is not None:
            self.translate(center)  # faster than a Matrix44 multiplication

    # Sequence interface
    def __len__(self) -> int:
        """ Returns `count` of vertices. """
        return len(self.vertices)

    def __getitem__(self, item: Union[int, slice]) -> Vec2:
        """ Get vertex by index `item`, supports ``list`` like slicing. """
        return self.vertices[item]

    # limited List interface
    def append(self, vertex: 'Vertex') -> None:
        """ Append `vertex`.
        Args:
             vertex: vertex as :class:`Vec2` compatible object

        """
        self.vertices.append(Vec2(vertex))

    def extend(self, vertices: Iterable) -> None:
        """ Append multiple `vertices`.
        Args:
             vertices: iterable of vertices as :class:`Vec2` compatible objects

        """
        self.vertices.extend(Vec2.generate(vertices))
