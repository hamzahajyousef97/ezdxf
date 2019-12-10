# Created: 13.03.2010
# Copyright (c) 2010, Manfred Moitzi
# License: MIT License
from typing import TYPE_CHECKING, Optional
import math
from .construct2d import ConstructionTool
from .bbox import BoundingBox2d
from .vector import Vec2

if TYPE_CHECKING:
    from ezdxf.eztypes import Vertex


class ParallelRaysError(ArithmeticError):
    pass


HALF_PI = math.pi / 2.
THREE_PI_HALF = 1.5 * math.pi
DOUBLE_PI = math.pi * 2.


class ConstructionRay:
    """
    Infinite 2D construction ray as immutable object.

    Args:
        p1: definition point 1
        p2: ray direction as 2nd point or ``None``
        angle: ray direction as angle in radians or ``None``

    """

    def __init__(self, p1: 'Vertex', p2: 'Vertex' = None, angle: float = None):
        self._location = Vec2(p1)
        if p2 is not None:
            p2 = Vec2(p2)
            if self._location.x < p2.x:
                self._direction = (p2 - self._location).normalize()
            else:
                self._direction = (self._location - p2).normalize()
            self._angle = self._direction.angle
        elif angle is not None:
            self._angle = angle
            self._direction = Vec2.from_angle(angle)
        else:
            raise ValueError('p2 or angle required.')

        if math.isclose(self._direction.x, 0., abs_tol=1e-12):
            self._slope = None
            self._yof0 = None
        else:
            self._slope = self._direction.y / self._direction.x
            self._yof0 = self._location.y - self._slope * self._location.x
        self._is_vertical = self._slope is None
        self._is_horizontal = math.isclose(self._direction.y, 0., abs_tol=1e-12)

    @property
    def location(self) -> Vec2:
        """ Location vector as :class:`Vec2`. """
        return self._location

    @property
    def direction(self) -> Vec2:
        """ Direction vector as :class:`Vec2`. """
        return self._direction

    @property
    def slope(self) -> float:
        """ Slope of ray or ``None`` if vertical. """
        return self._slope

    @property
    def angle(self) -> float:
        """ Angle between x-axis and ray in radians. """
        return self._angle

    @property
    def angle_deg(self) -> float:
        """ Angle between x-axis and ray in degrees. """
        return math.degrees(self._angle)

    @property
    def is_vertical(self) -> bool:
        """ ``True`` if ray is vertical (parallel to y-axis). """
        return self._is_vertical

    @property
    def is_horizontal(self) -> bool:
        """ ``True`` if ray is horizontal (parallel to x-axis). """
        return self._is_horizontal

    def __str__(self) -> str:
        """ Returns string representation ``ConstructionRay(x, y, phi)``. """
        return 'ConstructionRay(x={0._x:.3f}, y={0._y:.3f}, phi={0.angle:.5f} rad)'.format(self)

    def is_parallel(self, other: 'ConstructionRay') -> bool:
        """ Returns ``True`` if rays are parallel. """
        if self._is_vertical:
            return other._is_vertical

        if other._is_vertical:
            return False

        return math.isclose(self._slope, other._slope, abs_tol=1e-12)

    def intersect(self, other: 'ConstructionRay') -> Vec2:
        """
        Returns the intersection point as ``(x, y)`` tuple of `self` and `other`.

        Raises:
             ParallelRaysError: if rays are parallel

        """
        ray1 = self
        ray2 = other
        if not ray1.is_parallel(ray2):
            if ray1._is_vertical:
                x = self._location.x
                y = ray2.yof(x)
            elif ray2._is_vertical:
                x = ray2._location.x
                y = ray1.yof(x)
            else:
                # calc intersection with the 'straight-line-equation'
                # based on y(x) = y0 + x*slope
                x = (ray1._yof0 - ray2._yof0) / (ray2._slope - ray1._slope)
                y = ray1.yof(x)
            return Vec2((x, y))
        else:
            raise ParallelRaysError("Rays are parallel")

    def orthogonal(self, location: 'Vertex') -> 'ConstructionRay':
        """ Returns orthogonal ray at `location`. """
        return ConstructionRay(location, angle=self._angle + HALF_PI)

    def yof(self, x: float) -> float:
        """ Returns y-value of ray for `x` location.

        Raises:
            ArithmeticError: for vertical rays

        """
        if self._is_vertical:
            raise ArithmeticError
        return self._yof0 + float(x) * self._slope

    def xof(self, y: float) -> float:
        """ Returns x-value of ray for `y` location.

        Raises:
            ArithmeticError: for horizontal rays

        """
        if self._is_vertical:
            return self._location.x
        elif not self._is_horizontal:
            return (float(y) - self._yof0) / self._slope
        else:
            raise ArithmeticError

    def bisectrix(self, other: 'ConstructionRay') -> 'ConstructionRay':
        """ Bisectrix between `self` and `other`. """
        if self.is_parallel(other):
            raise ParallelRaysError
        intersection = self.intersect(other)
        alpha = (self._angle + other._angle) / 2.
        return ConstructionRay(intersection, angle=alpha)


class ConstructionLine(ConstructionTool):
    """
    2D ConstructionLine is similar to :class:`ConstructionRay`, but has a start- and endpoint.
    The direction of line goes from start- to endpoint, "left of line" is always in relation
    to this line direction.

    Args:
        start: start point of line as :class:`Vec2` compatible object
        end: end point of line as :class:`Vec2` compatible object

    """
    def __init__(self, start: 'Vertex', end: 'Vertex'):
        self.start = Vec2(start)
        self.end = Vec2(end)

    def __str__(self) -> str:
        """ Returns string representation of line ``ConstructionLine(start, end)``. """
        return 'ConstructionLine({0.start}, {0.end})'.format(self)

    # ConstructionTool interface
    @property
    def bounding_box(self) -> BoundingBox2d:
        """ bounding box of line as :class:`BoundingBox2d` object. """
        return BoundingBox2d((self.start, self.end))

    def move(self, dx: float, dy: float) -> None:
        """
        Move line about `dx` in x-axis and about `dy` in y-axis.

        Args:
            dx: translation in x-axis
            dy: translation in y-axis

        """
        v = Vec2((dx, dy))
        self.start += v
        self.end += v

    @property
    def sorted_points(self):
        return (self.end, self.start) if self.start > self.end else (self.start, self.end)

    @property
    def ray(self):
        """ collinear :class:`ConstructionRay`. """
        return ConstructionRay(self.start, self.end)

    def __eq__(self, other: 'ConstructionLine') -> bool:
        return self.sorted_points == other.sorted_points

    def __lt__(self, other: 'ConstructionLine') -> bool:
        return self.sorted_points < other.sorted_points

    def length(self) -> float:
        """ Returns length of line. """
        return (self.end - self.start).magnitude

    def midpoint(self) -> 'Vec2':
        """ Returns mid point of line. """
        return self.start.lerp(self.end)

    @property
    def is_vertical(self) -> bool:
        """ ``True`` if line is vertical. """
        return math.isclose(self.start.x, self.end.x)

    def inside_bounding_box(self, point: 'Vertex') -> bool:
        """ Returns ``True`` if `point` is inside of line bounding box. """
        return self.bounding_box.inside(point)

    def intersect(self, other: 'ConstructionLine') -> Optional['Vec2']:
        """
        Returns the intersection point of to lines or ``None`` if they have no intersection point.

        Args:
            other: other :class:`ConstructionLine`

        """
        try:
            point = self.ray.intersect(other.ray)
        except ParallelRaysError:
            return None
        else:
            if self.inside_bounding_box(point) and other.inside_bounding_box(point):
                return point
            else:
                return None

    def has_intersection(self, other: 'ConstructionLine') -> bool:
        """ Returns ``True`` if has intersection with `other` line. """
        # required because intersection Vector(0, 0, 0) is also False
        return self.intersect(other) is not None

    def left_of_line(self, point: 'Vertex') -> bool:
        """
        Returns ``True`` if `point` is left of construction line in relation to the line direction from start to end.

        Points exact at the line are not left of the line.

        """
        start, end = self.start, self.end
        point = Vec2(point)
        if self.is_vertical:
            # compute on which site of the line self should be
            should_be_left = self.start.y < self.end.y
            if should_be_left:
                return point.x < self.start.x
            else:
                return point.x > self.start.x
        else:
            y = self.ray.yof(point.x)
            # compute if point should be above or below the line
            should_be_above = start.x < end.x
            if should_be_above:
                return point.y > y
            else:
                return point.y < y
