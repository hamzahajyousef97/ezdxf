# Created: 28.12.2018
# Copyright (C) 2018-2019, Manfred Moitzi
# License: MIT License
from typing import TYPE_CHECKING, Tuple, Iterable, List, cast, Optional
import math
from ezdxf.math import Vector, Vec2, ConstructionRay, xround, ConstructionLine, ConstructionBox
from ezdxf.math import UCS, PassTroughUCS
from ezdxf.lldxf import const
from ezdxf.options import options
from ezdxf.lldxf.const import DXFValueError, DXFUndefinedBlockError
from ezdxf.tools import suppress_zeros
from ezdxf.render.arrows import ARROWS, connection_point
from ezdxf.entities.dimstyleoverride import DimStyleOverride

if TYPE_CHECKING:
    from ezdxf.eztypes import Dimension, Vertex, Drawing, GenericLayoutType, Style


class TextBox(ConstructionBox):
    """
    Text boundaries representation.

    """

    def __init__(self, center: 'Vertex', width: float, height: float, angle: float, gap: float = 0):
        height += (2 * gap)
        super().__init__(center, width, height, angle)


PLUS_MINUS = '±'
_TOLERANCE_COMMON = r"\A{align};{txt}{{\H{fac:.2f}x;"
TOLERANCE_TEMPLATE1 = _TOLERANCE_COMMON + r"{tol}}}"
TOLERANCE_TEMPLATE2 = _TOLERANCE_COMMON + r"\S{upr}^ {lwr};}}"
LIMITS_TEMPLATE = r"{{\H{fac:.2f}x;\S{upr}^ {lwr};}}"


def OptionalVec2(v) -> Optional[Vec2]:
    if v is not None:
        return Vec2(v)
    else:
        return None


class BaseDimensionRenderer:
    """
    Base rendering class for DIMENSION entities.

    """

    def __init__(self, dimension: 'Dimension', ucs: 'UCS' = None, override: DimStyleOverride = None):
        # DXF document
        self.drawing = dimension.drawing  # type: Drawing

        # DIMENSION entity
        self.dimension = dimension  # type: Dimension

        self.dxfversion = self.drawing.dxfversion  # type: str
        self.supports_dxf_r2000 = self.dxfversion >= 'AC1015'  # type: bool
        self.supports_dxf_r2007 = self.dxfversion >= 'AC1021'  # type: bool
        # Target BLOCK of the graphical representation of the DIMENSION entity
        self.block = None  # type: GenericLayoutType

        # DimStyleOverride object, manages dimension style overriding
        if override:
            self.dim_style = override
        else:
            self.dim_style = DimStyleOverride(dimension)

        # User defined coordinate system for DIMENSION entity
        self.ucs = ucs or PassTroughUCS()
        self.requires_extrusion = self.ucs.uz != (0, 0, 1)  # type: bool

        # ezdxf specific attributes beyond DXF reference, therefore not stored in the DXF file (DSTYLE)
        # Some of these are just an rendering effect, which will be ignored by CAD applications if they modify the
        # DIMENSION entity

        # user location override as UCS coordinates, stored as text_midpoint in the DIMENSION entity
        self.user_location = OptionalVec2(self.dim_style.pop('user_location', None))

        # user location override relative to dimline center if True
        self.relative_user_location = self.dim_style.pop('relative_user_location', False)  # type: bool

        # shift text away from default text location - implemented as user location override without leader
        # shift text along in text direction
        self.text_shift_h = self.dim_style.pop('text_shift_h', 0.)  # type: float
        # shift text perpendicular to text direction
        self.text_shift_v = self.dim_style.pop('text_shift_v', 0.)  # type: float

        # suppress arrow rendering - only rendering is suppressed (rendering effect), all placing related calculations
        # are done without this settings. Used for multi point linear dimensions to avoid double rendering of non arrow
        # ticks.
        self.suppress_arrow1 = self.dim_style.pop('suppress_arrow1', False)  # type: bool
        self.suppress_arrow2 = self.dim_style.pop('suppress_arrow2', False)  # type: bool
        # end of ezdxf specific attributes

        # ---------------------------------------------
        # GENERAL PROPERTIES
        # ---------------------------------------------
        self.default_color = self.dimension.dxf.color  # type: int
        self.default_layer = self.dimension.dxf.layer  # type: str

        # ezdxf creates ALWAYS attachment points in the text center.
        self.text_attachment_point = 5  # type: int # fixed for ezdxf rendering

        # ignored by ezdxf
        self.horizontal_direction = self.dimension.get_dxf_attrib('horizontal_direction', None)  # type: bool

        get = self.dim_style.get
        # overall scaling of DIMENSION entity
        self.dim_scale = get('dimscale', 1)  # type: float
        if self.dim_scale == 0:
            self.dim_scale = 1

        # Controls drawing of circle or arc center marks and centerlines, for DIMDIAMETER and DIMRADIUS, the center
        # mark is drawn only if you place the dimension line outside the circle or arc.
        # 0 = No center marks or lines are drawn
        # <0 = Center lines are drawn
        # >0 = Center marks are drawn
        self.dim_center_marks = get('dimcen', 0)  # type: int  # not supported yet

        # ---------------------------------------------
        # TEXT
        # ---------------------------------------------
        # dimension measurement factor
        self.dim_measurement_factor = get('dimlfac', 1)  # type: float
        self.text_style_name = get('dimtxsty', options.default_dimension_text_style)  # type: str
        self.text_style = self.drawing.styles.get(self.text_style_name)  # type: Style
        self.text_height = self.char_height * self.dim_scale  # type: float
        self.text_width_factor = self.text_style.get_dxf_attrib('width', 1.)  # type: float
        # text_gap: gap between dimension line an dimension text
        self.text_gap = get('dimgap', 0.625) * self.dim_scale  # type: float
        # user defined text rotation - overrides everything
        self.user_text_rotation = self.dimension.get_dxf_attrib('text_rotation', None)  # type: float
        # calculated text rotation
        self.text_rotation = self.user_text_rotation  # type: float
        self.text_color = get('dimclrt', self.default_color)  # type: int
        self.text_round = get('dimrnd', None)  # type: float
        self.text_decimal_places = get('dimdec', None)  # type: int

        # Controls the suppression of zeros in the primary unit value.
        # Values 0-3 affect feet-and-inch dimensions only and are not supported
        # 4 (Bit 3) = Suppresses leading zeros in decimal dimensions (for example, 0.5000 becomes .5000)
        # 8 (Bit 4) = Suppresses trailing zeros in decimal dimensions (for example, 12.5000 becomes 12.5)
        # 12 (Bit 3+4) = Suppresses both leading and trailing zeros (for example, 0.5000 becomes .5)
        self.text_suppress_zeros = get('dimzin', 0)  # type: int

        dimdsep = self.dim_style.get('dimdsep', 0)
        self.text_decimal_separator = ',' if dimdsep == 0 else chr(dimdsep)  # type: str
        self.text_format = self.dim_style.get('dimpost', '<>')  # type: str
        self.text_fill = self.dim_style.get('dimtfill', 0)  # type: int # 0= None, 1=Background, 2=DIMTFILLCLR
        self.text_fill_color = self.dim_style.get('dimtfillclr', 1)  # type: int
        self.text_box_fill_scale = 1.1

        # text_halign = 0: center; 1: left; 2: right; 3: above ext1; 4: above ext2
        self.text_halign = get('dimjust', 0)  # type: int

        # text_valign = 0: center; 1: above; 2: farthest away?; 3: JIS?; 4: below (2, 3 ignored by ezdxf)
        self.text_valign = get('dimtad', 0)  # type: int

        # Controls the vertical position of dimension text above or below the dimension line, when DIMTAD = 0.
        # The magnitude of the vertical offset of text is the product of the text height (+gap?) and DIMTVP.
        # Setting DIMTVP to 1.0 is equivalent to setting DIMTAD = 1.
        self.text_vertical_position = get('dimtvp', 0.)  # type: float  # not supported yet

        self.text_movement_rule = get('dimtmove', 2)  # type: int # move text freely
        if self.text_movement_rule == 0:
            # moves the dimension line with dimension text and makes no sense for ezdxf (just set `base` argument)
            self.text_movement_rule = 2

        # requires a leader?
        self.text_has_leader = self.user_location is not None and self.text_movement_rule == 1  # type: bool

        # text_rotation=0 if dimension text is 'inside', ezdxf defines 'inside' as at the default text location
        self.text_inside_horizontal = get('dimtih', 0)  # type: bool

        # text_rotation=0 if dimension text is 'outside', ezdxf defines 'outside' as NOT at the default text location
        self.text_outside_horizontal = get('dimtoh', 0)  # type: bool

        # force text location 'inside', even if the text should be moved 'outside'
        self.force_text_inside = bool(get('dimtix', 0))  # type: bool

        # how dimension text and arrows are arranged when space is not sufficient to place both 'inside'
        # 0 = Places both text and arrows outside extension lines
        # 1 = Moves arrows first, then text
        # 2 = Moves text first, then arrows
        # 3 = Moves either text or arrows, whichever fits best
        self.text_fitting_rule = get('dimatfit', 2)  # type: int  # not supported yet - ezdxf behaves like 2

        # units for all dimension types except Angular.
        # 1 = Scientific
        # 2 = Decimal
        # 3 = Engineering
        # 4 = Architectural (always displayed stacked)
        # 5 = Fractional (always displayed stacked)
        self.text_length_unit = get('dimlunit', 2)  # type: int  # not supported yet - ezdxf behaves like 2

        # fraction format when DIMLUNIT is set to 4 (Architectural) or 5 (Fractional).
        # 0 = Horizontal stacking
        # 1 = Diagonal stacking
        # 2 = Not stacked (for example, 1/2)
        self.text_fraction_format = get('dimfrac', 0)  # type: int  # not supported

        # units format for angular dimensions
        # 0 = Decimal degrees
        # 1 = Degrees/minutes/seconds (not supported) same as 0
        # 2 = Grad
        # 3 = Radians
        self.text_angle_unit = get('dimaunit', 0)  # type: int

        # text_outside is only True if really placed outside of default text location
        # remark: user defined text location is always outside per definition (not by real location)
        self.text_outside = False

        # calculated or overridden dimension text location
        self.text_location = None  # type: Vec2

        # bounding box of dimension text including border space
        self.text_box = None  # type: TextBox

        # formatted dimension text
        self.text = ""

        # True if dimension text doesn't fit between extension lines
        self.is_wide_text = False

        # ---------------------------------------------
        # ARROWS & TICKS
        # ---------------------------------------------
        self.tick_size = get('dimtsz') * self.dim_scale
        if self.tick_size > 0:
            # use oblique strokes as 'arrows', disables usual 'arrows' and user defined blocks
            self.arrow1_name, self.arrow2_name = None, None  # type: str
            # tick size is per definition double the size of arrow size
            # adjust arrow size to reuse the 'oblique' arrow block
            self.arrow_size = self.tick_size * 2  # type: float
        else:
            # arrow name or block name if user defined arrow
            self.arrow1_name, self.arrow2_name = self.dim_style.get_arrow_names()  # type: str
            self.arrow_size = get('dimasz') * self.dim_scale  # type: float

        # Suppresses arrowheads if not enough space is available inside the extension lines.
        # Only if force_text_inside is True
        self.suppress_arrow_heads = get('dimsoxd', 0)  # type: bool # not supported yet

        # ---------------------------------------------
        # DIMENSION LINE
        # ---------------------------------------------
        self.dim_line_color = get('dimclrd', self.default_color)  # type: int

        # dimension line extension, along the dimension line direction ('left' and 'right')
        self.dim_line_extension = get('dimdle', 0.) * self.dim_scale  # type: float
        self.dim_linetype = get('dimltype', None)  # type: str
        self.dim_lineweight = get('dimlwd', const.LINEWEIGHT_BYBLOCK)  # type: int

        # suppress first part of the dimension line
        self.suppress_dim1_line = get('dimsd1', 0)  # type: bool

        # suppress second part of the dimension line
        self.suppress_dim2_line = get('dimsd2', 0)  # type: bool

        # Controls whether a dimension line is drawn between the extension lines even when the text is placed outside.
        # For radius and diameter dimensions (when DIMTIX is off), draws a dimension line inside the circle or arc and
        # places the text, arrowheads, and leader outside.
        # 0 = no dimension line
        # 1 = draw dimension line
        self.dim_line_if_text_outside = get('dimtofl', 1)  # type: int  # not supported yet - ezdxf behaves like 1

        # ---------------------------------------------
        # EXTENSION LINES
        # ---------------------------------------------
        self.ext_line_color = get('dimclre', self.default_color)
        self.ext1_linetype_name = get('dimltex1', None)  # type: str
        self.ext2_linetype_name = get('dimltex2', None)  # type: str
        self.ext_lineweight = get('dimlwe', const.LINEWEIGHT_BYBLOCK)
        self.suppress_ext1_line = get('dimse1', 0)  # type: bool
        self.suppress_ext2_line = get('dimse2', 0)  # type: bool

        # extension of extension line above the dimension line, in extension line direction
        # in most cases perpendicular to dimension line (oblique!)
        self.ext_line_extension = get('dimexe', 0.) * self.dim_scale  # type: float

        # distance of extension line from the measurement point in extension line direction
        self.ext_line_offset = get('dimexo', 0.) * self.dim_scale  # type: float

        # fixed length extension line, leenght above dimension line is still self.ext_line_extension
        self.ext_line_fixed = get('dimflxon', 0)  # type: bool

        # length below the dimension line:
        self.ext_line_length = get('dimflx', self.ext_line_extension) * self.dim_scale  # type: float

        # ---------------------------------------------
        # TOLERANCES & LIMITS
        # ---------------------------------------------
        # appends tolerances to dimension text. Setting DIMTOL to on turns DIMLIM off.
        self.dim_tolerance = get('dimtol', 0)  # type: bool
        # generates dimension limits as the default text. Setting DIMLIM to On turns DIMTOL off.
        self.dim_limits = get('dimlim', 0)  # type: bool

        if self.dim_tolerance:
            self.dim_limits = 0

        if self.dim_limits:
            self.dim_tolerance = 0

        # scale factor for the text height of fractions and tolerance values relative to the dimension text height
        self.tol_text_scale_factor = get('dimtfac', .5)
        self.tol_line_spacing = 1.35  # default MTEXT line spacing for tolerances (BricsCAD)
        # sets the minimum (or lower) tolerance limit for dimension text when DIMTOL or DIMLIM is on.
        # DIMTM accepts signed values. If DIMTOL is on and DIMTP and DIMTM are set to the same value, a tolerance value
        # is drawn. If DIMTM and DIMTP values differ, the upper tolerance is drawn above the lower, and a plus sign is
        # added to the DIMTP value if it is positive. For DIMTM, the program uses the negative of the value you enter
        # (adding a minus sign if you specify a positive number and a plus sign if you specify a negative number).
        self.tol_minimum = get('dimtm', 0)  # type: float

        # Sets the maximum (or upper) tolerance limit for dimension text when DIMTOL or DIMLIM is on. DIMTP accepts
        # signed values. If DIMTOL is on and DIMTP and DIMTM are set to the same value, a tolerance value is drawn.
        # If DIMTM and DIMTP values differ, the upper tolerance is drawn above the lower and a plus sign is added to
        # the DIMTP value if it is positive.
        self.tol_maximum = get('dimtp', 0)  # type: float

        # number of decimal places to display in tolerance values
        self.tol_decimal_places = get('dimtdec', 4)  # type: int

        # vertical justification for tolerance values relative to the nominal dimension text
        # 0 = Bottom
        # 1 = Middle
        # 2 = Top
        self.tol_valign = get('dimtolj', 0)  # type: int

        # same as DIMZIN for tolerances (self.text_suppress_zeros)
        self.tol_suppress_zeros = get('dimtzin', 0)  # type: int
        self.tol_text = None
        self.tol_text_height = 0.
        self.tol_text_upper = None
        self.tol_text_lower = None
        self.tol_char_height = self.char_height * self.tol_text_scale_factor * self.dim_scale
        # tolerances
        if self.dim_tolerance:
            # single tolerance value +/- value
            if self.tol_minimum == self.tol_maximum:
                self.tol_text = PLUS_MINUS + self.format_tolerance_text(abs(self.tol_maximum))
                self.tol_text_height = self.tol_char_height
                self.tol_text_width = self.tolerance_text_width(len(self.tol_text))
            else:  # 2 stacked values: +upper tolerance <above> -lower tolerance
                self.tol_text_upper = sign_char(self.tol_maximum) + self.format_tolerance_text(
                    abs(self.tol_maximum))
                self.tol_text_lower = sign_char(self.tol_minimum * -1) + self.format_tolerance_text(
                    abs(self.tol_minimum))
                # requires 2 text lines
                self.tol_text_height = self.tol_char_height + (self.tol_text_height * self.tol_line_spacing)
                self.tol_text_width = self.tolerance_text_width(max(len(self.tol_text_upper), len(self.tol_text_lower)))
            # reset text height
            self.text_height = max(self.text_height, self.tol_text_height)

        elif self.dim_limits:
            self.tol_text = None  # always None for limits
            # limits text is always 2 stacked numbers and requires actual measurement
            self.tol_text_upper = None  # text for upper limit
            self.tol_text_lower = None  # text for lower limit
            self.tol_text_height = self.tol_char_height + (self.tol_text_height * self.tol_line_spacing)
            self.tol_text_width = None  # requires actual measurement
            self.text_height = max(self.text_height, self.tol_text_height)

    @property
    def text_inside(self):
        return not self.text_outside

    def render(self, block: 'GenericLayoutType'):  # interface definition
        self.block = block
        # tolerance requires MTEXT support, switch of rendering of tolerances and limits
        if not self.supports_dxf_r2000:
            self.dim_tolerance = 0
            self.dim_limits = 0


    @property
    def char_height(self) -> float:
        """
        Unscaled (self.dim_scale) character height defined by text style or DIMTXT.
        Hint: Use self.text_height for proper scaled text height in drawing units.

        """
        height = self.text_style.get_dxf_attrib('height', 0)  # type: float
        if height == 0:  # variable text height (not fixed)
            height = self.dim_style.get('dimtxt', 1.)
        return height

    def text_width(self, text: str) -> float:
        """
        Return width of `text` in drawing units.

        """
        char_width = self.text_height * self.text_width_factor  # type: float
        return len(text) * char_width

    def tolerance_text_width(self, count: int) -> float:
        """
        Return width of `count` characters in drawing units.

        """
        return self.tol_text_height * self.text_width_factor * count

    def default_attributes(self) -> dict:
        """
        Returns default DXF attributes as dict.

        """
        return {
            'layer': self.default_layer,  # type: str
            'color': self.default_color,  # type: int
        }

    def wcs(self, point: 'Vertex') -> Vector:
        """
        Transform `point` in UCS coordinates into WCS coordinates.

        """
        return self.ucs.to_wcs(point)

    def ocs(self, point: 'Vertex') -> Vector:
        """
        Transform `point` in UCS coordinates into OCS coordinates.

        """
        return self.ucs.to_ocs(point)

    def to_ocs_angle(self, angle: float) -> float:
        """
        Transform `angle` from UCS to OCS.

        """
        return self.ucs.to_ocs_angle_deg(angle)

    def text_override(self, measurement: float) -> str:
        """
        Create dimension text for `measurement` in drawing units and applies text overriding properties.

        """
        text = self.dimension.dxf.text  # type: str
        if text == ' ':  # suppress text
            return ''
        elif text == '' or text == '<>':  # measured distance
            return self.format_text(measurement)
        else:  # user override
            return text

    def format_text(self, value: float) -> str:
        """
        Rounding and text formatting of `value`, removes leading and trailing zeros if necessary.

        """
        return format_text(
            value,
            self.text_round,
            self.text_decimal_places,
            self.text_suppress_zeros,
            self.text_decimal_separator,
            self.text_format,
        )

    def compile_mtext(self) -> str:
        text = self.text
        if self.dim_tolerance:
            align = max(int(self.tol_valign), 0)
            align = min(align, 2)
            if self.tol_text is None:
                text = TOLERANCE_TEMPLATE2.format(
                    align=align,
                    txt=text,
                    fac=self.tol_text_scale_factor,
                    upr=self.tol_text_upper,
                    lwr=self.tol_text_lower,
                )
            else:
                text = TOLERANCE_TEMPLATE1.format(
                    align=align,
                    txt=text,
                    fac=self.tol_text_scale_factor,
                    tol=self.tol_text,
                )
        elif self.dim_limits:
            text = LIMITS_TEMPLATE.format(
                upr=self.tol_text_upper,
                lwr=self.tol_text_lower,
                fac=self.tol_text_scale_factor,
            )
        return text

    def format_tolerance_text(self, value: float) -> str:
        """
        Rounding and text formatting of tolerance `value`, removes leading and trailing zeros if necessary.

        """
        return format_text(
            value=value,
            dimrnd=None,
            dimdec=self.tol_decimal_places,
            dimzin=self.tol_suppress_zeros,
            dimdsep=self.text_decimal_separator,
        )

    def location_override(self, location: 'Vertex', leader=False, relative=False) -> None:
        """
        Set user defined dimension text location. ezdxf defines a user defined location per definition as 'outside'.

        Args:
            location: text midpoint
            leader: use leader or not (movement rules)
            relative: is location absolute (in UCS) or relative to dimension line center.

        """
        self.dim_style.set_location(location, leader, relative)
        self.user_location = Vec2(location)
        self.text_movement_rule = 1 if leader else 2
        self.relative_user_location = relative
        self.text_outside = True

    def add_line(self, start: 'Vertex', end: 'Vertex', dxfattribs: dict = None, remove_hidden_lines=False) -> None:
        """
        Add a LINE entity to the dimension BLOCK. Removes parts of the line hidden by dimension text if
        `remove_hidden_lines` is True.

        Args:
            start: start point of line
            end: end point of line
            dxfattribs: additional or overridden DXF attributes
            remove_hidden_lines: removes parts of the line hidden by dimension text if True

        """

        def order(a: Vec2, b: Vec2) -> Tuple[Vec2, Vec2]:
            if (start - a).magnitude < (start - b).magnitude:
                return a, b
            else:
                return b, a

        attribs = self.default_attributes()
        if dxfattribs:
            attribs.update(dxfattribs)
        text_box = self.text_box
        wcs = self.ucs.to_wcs
        if remove_hidden_lines and (text_box is not None):
            start_inside = int(text_box.is_inside(start))
            end_inside = int(text_box.is_inside(end))
            inside = start_inside + end_inside
            if inside == 2:  # start and end inside text_box
                return  # do not draw line
            elif inside == 1:  # one point inside text_box
                intersection_points = text_box.intersect(ConstructionLine(start, end))
                # one point inside one point outside -> one intersection point
                p1 = intersection_points[0]
                p2 = start if start_inside else end
                self.block.add_line(wcs(p1), wcs(p2), dxfattribs=attribs)
                return
            else:
                intersection_points = text_box.intersect(ConstructionLine(start, end))
                if len(intersection_points) == 2:
                    # sort intersection points by distance to start point
                    p1, p2 = order(intersection_points[0], intersection_points[1])
                    # line[start-p1] - gap - line[p2-end]
                    self.block.add_line(wcs(start), wcs(p1), dxfattribs=attribs)
                    self.block.add_line(wcs(p2), wcs(end), dxfattribs=attribs)
                    return
                # else: fall trough
        self.block.add_line(wcs(start), wcs(end), dxfattribs=attribs)

    def add_blockref(self, name: str, insert: 'Vertex', rotation: float = 0,
                     scale: float = 1., dxfattribs: dict = None) -> None:
        """
        Add block references and standard arrows to the dimension BLOCK.

        Args:
            name: block or arrow name
            insert: insertion point in UCS
            rotation: rotation angle in degrees in UCS (x-axis is 0 degrees)
            scale: scaling factor for x- and y-direction
            dxfattribs: additional or overridden DXF attributes

        """
        attribs = self.default_attributes()
        insert = self.ocs(insert)
        rotation = self.to_ocs_angle(rotation)
        if self.requires_extrusion:
            attribs['extrusion'] = self.ucs.uz
        if name in ARROWS:  # generates automatically BLOCK definitions for arrows if needed
            if dxfattribs:
                attribs.update(dxfattribs)
            self.block.add_arrow_blockref(name, insert=insert, size=scale, rotation=rotation, dxfattribs=attribs)
        else:
            if name not in self.drawing.blocks:
                raise DXFUndefinedBlockError('Undefined block: "{}"'.format(name))
            attribs['rotation'] = rotation
            if scale != 1.:
                attribs['xscale'] = scale
                attribs['yscale'] = scale
            if dxfattribs:
                attribs.update(dxfattribs)
            self.block.add_blockref(name, insert=insert, dxfattribs=attribs)

    def add_text(self, text: str, pos: Vector, rotation: float, dxfattribs: dict = None) -> None:
        """
        Add TEXT (DXF R12) or MTEXT (DXF R2000+) entity to the dimension BLOCK.

        Args:
            text: text as string
            pos: insertion location in UCS
            rotation: rotation angle in degrees in UCS (x-axis is 0 degrees)
            dxfattribs: additional or overridden DXF attributes

        """
        attribs = self.default_attributes()
        attribs['style'] = self.text_style_name
        attribs['color'] = self.text_color
        if self.requires_extrusion:
            attribs['extrusion'] = self.ucs.uz

        if self.supports_dxf_r2000:
            text_direction = self.ucs.to_wcs(Vec2.from_deg_angle(rotation)) - self.ucs.origin
            attribs['text_direction'] = text_direction
            attribs['char_height'] = self.text_height
            attribs['insert'] = self.wcs(pos)
            attribs['attachment_point'] = self.text_attachment_point

            if self.supports_dxf_r2007:
                if self.text_fill:
                    attribs['box_fill_scale'] = self.text_box_fill_scale
                    attribs['bg_fill_color'] = self.text_fill_color
                    attribs['bg_fill'] = 3 if self.text_fill == 1 else 1

            if dxfattribs:
                attribs.update(dxfattribs)
            self.block.add_mtext(text, dxfattribs=attribs)
        else:
            attribs['rotation'] = self.ucs.to_ocs_angle_deg(rotation)
            attribs['height'] = self.text_height
            if dxfattribs:
                attribs.update(dxfattribs)
            dxftext = self.block.add_text(text, dxfattribs=attribs)
            dxftext.set_pos(self.ocs(pos), align='MIDDLE_CENTER')

    def add_defpoints(self, points: Iterable['Vertex']) -> None:
        """
        Add POINT entities at layer 'DEFPOINTS' for all points in `points`.

        """
        attribs = {
            'layer': 'DEFPOINTS',
        }
        for point in points:
            self.block.add_point(self.wcs(point), dxfattribs=attribs)

    def add_leader(self, p1: Vec2, p2: Vec2, p3: Vec2, dxfattribs: dict = None):
        """
        Add simple leader line from p1 to p2 to p3.

        Args:
            p1: target point
            p2: first text point
            p3: second text point
            dxfattribs: DXF attribute

        """
        self.add_line(p1, p2, dxfattribs)
        self.add_line(p2, p3, dxfattribs)

    def transform_ucs_to_wcs(self) -> None:
        """
        Transforms dimension definition points into WCS or if required into OCS.

        Can not be called in __init__(), because inherited classes may be need unmodified values.

        """

        def from_ucs(attr, func):
            point = self.dimension.get_dxf_attrib(attr)
            self.dimension.set_dxf_attrib(attr, func(point))

        from_ucs('defpoint', self.wcs)
        from_ucs('defpoint2', self.wcs)
        from_ucs('defpoint3', self.wcs)
        from_ucs('text_midpoint', self.ocs)
        self.dimension.dxf.angle = self.ucs.to_ocs_angle_deg(self.dimension.dxf.angle)

    def finalize(self) -> None:
        self.transform_ucs_to_wcs()


def order_leader_points(p1: Vec2, p2: Vec2, p3: Vec2) -> Tuple[Vec2, Vec2]:
    if (p1 - p2).magnitude > (p1 - p3).magnitude:
        return p3, p2
    else:
        return p2, p3


class LinearDimension(BaseDimensionRenderer):
    """
    Linear dimension line renderer, used for horizontal, vertical, rotated and aligned DIMENSION entities.

    Args:
        dimension: DXF entity DIMENSION
        ucs: user defined coordinate system
        override: dimension style override management object

    """

    def __init__(self, dimension: 'Dimension', ucs: 'UCS' = None, override: 'DimStyleOverride' = None):
        super().__init__(dimension, ucs, override)
        self.oblique_angle = self.dimension.get_dxf_attrib('oblique_angle', 90)  # type: float
        self.dim_line_angle = self.dimension.get_dxf_attrib('angle', 0)  # type: float
        self.dim_line_angle_rad = math.radians(self.dim_line_angle)  # type: float
        self.ext_line_angle = self.dim_line_angle + self.oblique_angle  # type: float
        self.ext_line_angle_rad = math.radians(self.ext_line_angle)  # type: float

        # text is aligned to dimension line
        self.text_rotation = self.dim_line_angle  # type: float
        if self.text_halign in (3, 4):  # text above extension line, is always aligned with extension lines
            self.text_rotation = self.ext_line_angle

        self.ext1_line_start = Vec2(self.dimension.dxf.defpoint2)
        self.ext2_line_start = Vec2(self.dimension.dxf.defpoint3)

        ext1_ray = ConstructionRay(self.ext1_line_start, angle=self.ext_line_angle_rad)
        ext2_ray = ConstructionRay(self.ext2_line_start, angle=self.ext_line_angle_rad)
        dim_line_ray = ConstructionRay(self.dimension.dxf.defpoint, angle=self.dim_line_angle_rad)

        self.dim_line_start = dim_line_ray.intersect(ext1_ray)  # type: Vec2
        self.dim_line_end = dim_line_ray.intersect(ext2_ray)  # type: Vec2
        self.dim_line_center = self.dim_line_start.lerp(self.dim_line_end)  # type: Vec2

        if self.dim_line_start == self.dim_line_end:
            self.dim_line_vec = Vec2.from_angle(self.dim_line_angle_rad)
        else:
            self.dim_line_vec = (self.dim_line_end - self.dim_line_start).normalize()  # type: Vec2

        # set dimension defpoint to expected location - 3D vertex required!
        self.dimension.dxf.defpoint = Vector(self.dim_line_start)

        self.measurement = (self.dim_line_end - self.dim_line_start).magnitude  # type: float
        self.text = self.text_override(self.measurement * self.dim_measurement_factor)  # type: str

        # only for linear dimension in multi point mode
        self.multi_point_mode = override.pop('multi_point_mode', False)

        # 1 .. move wide text up
        # 2 .. move wide text down
        # None .. ignore
        self.move_wide_text = override.pop('move_wide_text', None)  # type: bool

        # actual text width in drawing units
        self.dim_text_width = 0  # type: float

        # arrows
        self.required_arrows_space = 2 * self.arrow_size + self.text_gap  # type: float
        self.arrows_outside = self.required_arrows_space > self.measurement  # type: bool

        # text location and rotation
        if self.text:
            # text width and required space
            self.dim_text_width = self.text_width(self.text)  # type: float
            if self.dim_tolerance:
                self.dim_text_width += self.tol_text_width

            elif self.dim_limits:
                # limits show the upper and lower limit of the measurement as stacked values
                # and with the size of tolerances
                measurement = self.measurement * self.dim_measurement_factor
                self.measurement_upper_limit = measurement + self.tol_maximum
                self.measurement_lower_limit = measurement - self.tol_minimum
                self.tol_text_upper = self.format_tolerance_text(self.measurement_upper_limit)
                self.tol_text_lower = self.format_tolerance_text(self.measurement_lower_limit)
                self.tol_text_width = self.tolerance_text_width(max(len(self.tol_text_upper), len(self.tol_text_lower)))

                # only limits are displayed so:
                self.dim_text_width = self.tol_text_width

            if self.multi_point_mode:
                # ezdxf has total control about vertical text position in multi point mode
                self.text_vertical_position = 0.

            if self.text_valign == 0 and abs(self.text_vertical_position) < 0.7:
                # vertical centered text needs also space for arrows
                required_space = self.dim_text_width + 2 * self.arrow_size
            else:
                required_space = self.dim_text_width
            self.is_wide_text = required_space > self.measurement

            if not self.force_text_inside:
                # place text outside if wide text and not forced inside
                self.text_outside = self.is_wide_text
            elif self.is_wide_text and self.text_halign < 3:
                # center wide text horizontal
                self.text_halign = 0

            # use relative text shift to move wide text up or down in multi point mode
            if self.multi_point_mode and self.is_wide_text and self.move_wide_text:
                shift_value = self.text_height + self.text_gap
                if self.move_wide_text == 1:  # move text up
                    self.text_shift_v = shift_value
                    if self.vertical_placement == -1:  # text below dimension line
                        # shift again
                        self.text_shift_v += shift_value
                elif self.move_wide_text == 2:  # move text down
                    self.text_shift_v = -shift_value
                    if self.vertical_placement == 1:  # text above dimension line
                        # shift again
                        self.text_shift_v -= shift_value

            # get final text location - no altering after this line
            self.text_location = self.get_text_location()  # type: Vec2

            # text rotation override
            rotation = self.text_rotation  # type: float
            if self.user_text_rotation is not None:
                rotation = self.user_text_rotation
            elif self.text_outside and self.text_outside_horizontal:
                rotation = 0
            elif self.text_inside and self.text_inside_horizontal:
                rotation = 0
            self.text_rotation = rotation

            self.text_box = TextBox(
                center=self.text_location,
                width=self.dim_text_width,
                height=self.text_height,
                angle=self.text_rotation,
                gap=self.text_gap * .75
            )
            if self.text_has_leader:
                p1, p2, *_ = self.text_box.corners
                self.leader1, self.leader2 = order_leader_points(self.dim_line_center, p1, p2)
                # not exact what BricsCAD (AutoCAD) expect, but close enough
                self.dimension.dxf.text_midpoint = self.leader1
            else:
                # write final text location into DIMENSION entity
                self.dimension.dxf.text_midpoint = self.text_location

    @property
    def has_relative_text_movement(self):
        return bool(self.text_shift_h or self.text_shift_v)

    def apply_text_shift(self, location: Vec2, text_rotation: float) -> Vec2:
        """
        Add `self.text_shift_h` and `sel.text_shift_v` to point `location`, shifting along and perpendicular to
        text orientation defined by `text_rotation`

        Args:
            location: location point
            text_rotation: text rotation in degrees

        Returns: new location

        """
        shift_vec = Vec2((self.text_shift_h, self.text_shift_v))
        location += shift_vec.rotate(text_rotation)
        return location

    def render(self, block: 'GenericLayoutType') -> None:
        """
        Main method to create dimension geometry as basic DXF entities in the associated BLOCK layout.

        Args:
            block: target BLOCK for rendering

        """
        # call required to setup some requirements
        super().render(block)

        # add extension line 1
        if not self.suppress_ext1_line:
            above_ext_line1 = self.text_halign == 3
            start, end = self.extension_line_points(self.ext1_line_start, self.dim_line_start, above_ext_line1)
            self.add_extension_line(start, end, linetype=self.ext1_linetype_name)

        # add extension line 2
        if not self.suppress_ext2_line:
            above_ext_line2 = self.text_halign == 4
            start, end = self.extension_line_points(self.ext2_line_start, self.dim_line_end, above_ext_line2)
            self.add_extension_line(start, end, linetype=self.ext2_linetype_name)

        # add arrow symbols (block references), also adjust dimension line start and end point
        dim_line_start, dim_line_end = self.add_arrows()

        # add dimension line
        self.add_dimension_line(dim_line_start, dim_line_end)

        # add measurement text as last entity to see text fill properly
        if self.text:
            if self.supports_dxf_r2000:
                text = self.compile_mtext()
            else:
                text = self.text
            self.add_measurement_text(text, self.text_location, self.text_rotation)
            if self.text_has_leader:
                self.add_leader(self.dim_line_center, self.leader1, self.leader2)

        # add POINT entities at definition points
        self.add_defpoints([self.dim_line_start, self.ext1_line_start, self.ext2_line_start])

    def get_text_location(self) -> Vec2:
        """
        Get text midpoint in UCS from user defined location or default text location.

        """
        # apply relative text shift as user location override without leader
        if self.has_relative_text_movement:
            location = self.default_text_location()
            location = self.apply_text_shift(location, self.text_rotation)
            self.location_override(location)

        if self.user_location is not None:
            location = self.user_location
            if self.relative_user_location:
                location = self.dim_line_center + location
            # define overridden text location as outside
            self.text_outside = True
        else:
            location = self.default_text_location()

        return location

    def default_text_location(self) -> Vec2:
        """
        Calculate default text location in UCS based on `self.text_halign`, `self.text_valign` and `self.text_outside`

        """
        start = self.dim_line_start
        end = self.dim_line_end
        halign = self.text_halign
        # positions the text above and aligned with the first/second extension line
        if halign in (3, 4):
            # horizontal location
            hdist = self.text_gap + self.text_height / 2.
            hvec = self.dim_line_vec * hdist
            location = (start if halign == 3 else end) - hvec
            # vertical location
            vdist = self.ext_line_extension + self.dim_text_width / 2.
            location += Vec2.from_deg_angle(self.ext_line_angle).normalize(vdist)
        else:
            # relocate outside text to center location
            if self.text_outside:
                halign = 0

            if halign == 0:
                location = self.dim_line_center  # center of dimension line
            else:
                hdist = self.dim_text_width / 2. + self.arrow_size + self.text_gap
                if halign == 1:  # positions the text next to the first extension line
                    location = start + (self.dim_line_vec * hdist)
                else:  # positions the text next to the second extension line
                    location = end - (self.dim_line_vec * hdist)

            if self.text_outside:  # move text up
                vdist = self.ext_line_extension + self.text_gap + self.text_height / 2.
            else:
                # distance from extension line to text midpoint
                vdist = self.text_vertical_distance()
            location += self.dim_line_vec.orthogonal().normalize(vdist)

        return location

    def add_arrows(self) -> Tuple[Vec2, Vec2]:
        """
        Add arrows or ticks to dimension.

        Returns: dimension line connection points

        """
        attribs = {
            'color': self.dim_line_color,
        }
        start = self.dim_line_start
        end = self.dim_line_end
        outside = self.arrows_outside
        arrow1 = not self.suppress_arrow1
        arrow2 = not self.suppress_arrow2
        if self.tick_size > 0.:  # oblique stroke, but double the size
            if arrow1:
                self.add_blockref(
                    ARROWS.oblique,
                    insert=start,
                    rotation=self.dim_line_angle,
                    scale=self.tick_size * 2,
                    dxfattribs=attribs,
                )
            if arrow2:
                self.add_blockref(
                    ARROWS.oblique,
                    insert=end,
                    rotation=self.dim_line_angle,
                    scale=self.tick_size * 2,
                    dxfattribs=attribs,
                )
        else:
            scale = self.arrow_size
            start_angle = self.dim_line_angle + 180.
            end_angle = self.dim_line_angle
            if outside:
                start_angle, end_angle = end_angle, start_angle

            if arrow1:
                self.add_blockref(self.arrow1_name, insert=start, scale=scale, rotation=start_angle,
                                  dxfattribs=attribs)  # reverse
            if arrow2:
                self.add_blockref(self.arrow2_name, insert=end, scale=scale, rotation=end_angle, dxfattribs=attribs)

            if not outside:
                # arrows inside extension lines: adjust connection points for the remaining dimension line
                if arrow1:
                    start = connection_point(self.arrow1_name, start, scale, start_angle)
                if arrow2:
                    end = connection_point(self.arrow2_name, end, scale, end_angle)
            else:
                # add additional extension lines to arrows placed outside of dimension extension lines
                self.add_arrow_extension_lines()
        return start, end

    def add_arrow_extension_lines(self):
        """
        Add extension lines to arrows placed outside of dimension extension lines. Called by `self.add_arrows()`.

        """

        def has_arrow_extension(name: str) -> bool:
            return (name is not None) and (name in ARROWS) and (name not in ARROWS.ORIGIN_ZERO)

        attribs = {
            'color': self.dim_line_color,
        }
        start = self.dim_line_start
        end = self.dim_line_end
        arrow_size = self.arrow_size

        if not self.suppress_arrow1 and has_arrow_extension(self.arrow1_name):
            self.add_line(
                start - self.dim_line_vec * arrow_size,
                start - self.dim_line_vec * (2 * arrow_size),
                dxfattribs=attribs,
            )

        if not self.suppress_arrow2 and has_arrow_extension(self.arrow2_name):
            self.add_line(
                end + self.dim_line_vec * arrow_size,
                end + self.dim_line_vec * (2 * arrow_size),
                dxfattribs=attribs,
            )

    def add_measurement_text(self, dim_text: str, pos: Vec2, rotation: float) -> None:
        """
        Add measurement text to dimension BLOCK.

        Args:
            dim_text: dimension text
            pos: text location
            rotation: text rotation in degrees

        """
        attribs = {
            'color': self.text_color,
        }
        self.add_text(dim_text, pos=Vector(pos), rotation=rotation, dxfattribs=attribs)

    def add_dimension_line(self, start: 'Vertex', end: 'Vertex') -> None:
        """
        Add dimension line to dimension BLOCK, adds extension DIMDLE if required, and uses DIMSD1 or DIMSD2 to suppress
        first or second part of dimension line. Removes line parts hidden by dimension text.

        Args:
            start: dimension line start
            end: dimension line end

        """
        extension = self.dim_line_vec * self.dim_line_extension
        if self.arrow1_name is None or ARROWS.has_extension_line(self.arrow1_name):
            start = start - extension
        if self.arrow2_name is None or ARROWS.has_extension_line(self.arrow2_name):
            end = end + extension

        attribs = {
            'color': self.dim_line_color
        }
        if self.dim_linetype is not None:
            attribs['linetype'] = self.dim_linetype

        if self.supports_dxf_r2000:
            attribs['lineweight'] = self.dim_lineweight

        if self.suppress_dim1_line or self.suppress_dim2_line:
            if not self.suppress_dim1_line:
                self.add_line(start, self.dim_line_center, dxfattribs=attribs, remove_hidden_lines=True)
            if not self.suppress_dim2_line:
                self.add_line(self.dim_line_center, end, dxfattribs=attribs, remove_hidden_lines=True)
        else:
            self.add_line(start, end, dxfattribs=attribs, remove_hidden_lines=True)

    def extension_line_points(self, start: Vec2, end: Vec2, text_above_extline=False) -> Tuple[Vec2, Vec2]:
        """
        Adjust start and end point of extension line by dimension variables DIMEXE, DIMEXO, DIMEXFIX, DIMEXLEN.

        Args:
            start: start point of extension line (measurement point)
            end: end point at dimension line
            text_above_extline: True if text is above and aligned with extension line

        Returns: adjusted start and end point

        """
        if start == end:
            direction = Vec2.from_deg_angle(self.ext_line_angle)
        else:
            direction = (end - start).normalize()
        if self.ext_line_fixed:
            start = end - (direction * self.ext_line_length)
        else:
            start = start + direction * self.ext_line_offset
        extension = self.ext_line_extension
        if text_above_extline:
            extension += self.dim_text_width
        end = end + direction * extension
        return start, end

    def add_extension_line(self, start: 'Vertex', end: 'Vertex', linetype: str = None) -> None:
        """
        Add extension lines from dimension line to measurement point.

        """
        attribs = {
            'color': self.ext_line_color
        }
        if linetype is not None:
            attribs['linetype'] = linetype

        # lineweight requires DXF R2000 or later
        if self.supports_dxf_r2000:
            attribs['lineweight'] = self.ext_lineweight

        self.add_line(start, end, dxfattribs=attribs)

    @property
    def vertical_placement(self) -> float:
        """
        Returns vertical placement of dimension text as 1 for above, 0 for center and -1 for below dimension line.

        """
        if self.text_valign == 0:
            return 0
        elif self.text_valign == 4:
            return -1
        else:
            return 1

    def text_vertical_distance(self) -> float:
        """
        Returns the vertical distance for dimension line to text midpoint. Positive values are above the line, negative
        values are below the line.

        """
        if self.text_valign == 0:
            return self.text_height * self.text_vertical_position
        else:
            return (self.text_height / 2. + self.text_gap) * self.vertical_placement


class DimensionRenderer:
    def dispatch(self, override: 'DimStyleOverride', ucs: 'UCS') -> BaseDimensionRenderer:
        dimension = override.dimension
        dim_type = dimension.dimtype

        if dim_type in (0, 1):
            return self.linear(dimension, ucs, override)
        elif dim_type == 2:
            return self.angular(dimension, ucs, override)
        elif dim_type == 3:
            return self.diameter(dimension, ucs, override)
        elif dim_type == 4:
            return self.radius(dimension, ucs, override)
        elif dim_type == 5:
            return self.angular3p(dimension, ucs, override)
        elif dim_type == 6:
            return self.ordinate(dimension, ucs, override)
        else:
            raise DXFValueError("Unknown DIMENSION type: {}".format(dim_type))

    def linear(self, dimension: 'Dimension', ucs: 'UCS', override: 'DimStyleOverride' = None):
        """
        Call renderer for linear dimension lines: horizontal, vertical and rotated
        """
        return LinearDimension(dimension, ucs, override)

    def angular(self, dimension: 'Dimension', ucs: 'UCS', override: 'DimStyleOverride' = None):
        raise NotImplemented

    def diameter(self, dimension: 'Dimension', ucs: 'UCS', override: 'DimStyleOverride' = None):
        raise NotImplemented

    def radius(self, dimension: 'Dimension', ucs: 'UCS', override: 'DimStyleOverride' = None):
        raise NotImplemented

    def angular3p(self, dimension: 'Dimension', ucs: 'UCS', override: 'DimStyleOverride' = None):
        raise NotImplemented

    def ordinate(self, dimension: 'Dimension', ucs: 'UCS', override: 'DimStyleOverride' = None):
        raise NotImplemented


def format_text(value: float, dimrnd: float = None, dimdec: int = None, dimzin: int = 0, dimdsep: str = '.',
                dimpost: str = '<>') -> str:
    if dimrnd is not None:
        value = xround(value, dimrnd)

    if dimdec is None:
        fmt = "{:f}"
        dimzin = dimzin | 8  # remove pending zeros for undefined decimal places, '{:f}'.format(0) -> '0.000000'
    else:
        fmt = "{:." + str(dimdec) + "f}"
    text = fmt.format(value)

    leading = bool(dimzin & 4)
    pending = bool(dimzin & 8)
    text = suppress_zeros(text, leading, pending)
    if dimdsep != '.':
        text = text.replace('.', dimdsep)
    if dimpost:
        if '<>' in dimpost:
            fmt = dimpost.replace('<>', '{}', 1)
            text = fmt.format(text)
        else:
            raise DXFValueError('Invalid dimpost string: "{}"'.format(dimpost))
    return text


CAN_SUPPRESS_ARROW1 = {
    ARROWS.dot,
    ARROWS.dot_small,
    ARROWS.dot_blank,
    ARROWS.origin_indicator,
    ARROWS.origin_indicator_2,
    ARROWS.dot_smallblank,
    ARROWS.none,
    ARROWS.oblique,
    ARROWS.box_filled,
    ARROWS.box,
    ARROWS.integral,
    ARROWS.architectural_tick,
}


def sign_char(value: float) -> str:
    if value < 0.:
        return '-'
    elif value > 0:
        return '+'
    else:
        return ' '


def sort_projected_points(points: Iterable['Vertex'], angle: float = 0) -> List[Vec2]:
    direction = Vec2.from_deg_angle(angle)
    projected_vectors = [(direction.project(Vec2(p)), p) for p in points]
    return [p for projection, p in sorted(projected_vectors)]


def multi_point_linear_dimension(
        layout: 'GenericLayoutType',
        base: 'Vertex',
        points: Iterable['Vertex'],
        angle: float = 0,
        ucs: 'UCS' = None,
        avoid_double_rendering: bool = True,
        dimstyle: str = 'EZDXF',
        override: dict = None,
        dxfattribs: dict = None,
        discard=False) -> None:
    """
    Creates multiple DIMENSION entities for each point pair in `points`. Measurement points will be sorted by appearance
    on the dimension line vector.

    Args:
        layout: target layout (model space, paper space or block)
        base: base point, any point on the dimension line vector will do
        points: iterable of measurement points
        angle: dimension line rotation in degrees (0=horizontal, 90=vertical)
        ucs: user defined coordinate system
        avoid_double_rendering: removes first extension line and arrow of following DIMENSION entity
        dimstyle: dimension style name
        override: dictionary of overridden dimension style attributes
        dxfattribs: DXF attributes for DIMENSION entities
        discard: discard rendering result for friendly CAD applications like BricsCAD to get a native and likely better
                 rendering result. (does not work with AutoCAD)

    """

    def suppress_arrow1(dimstyle_override) -> bool:
        arrow_name1, arrow_name2 = dimstyle_override.get_arrow_names()
        if (arrow_name1 is None) or (arrow_name1 in CAN_SUPPRESS_ARROW1):
            return True
        else:
            return False

    points = sort_projected_points(points, angle)
    base = Vec2(base)
    override = override or {}
    override['dimtix'] = 1  # do not place measurement text outside
    override['dimtvp'] = 0  # do not place measurement text outside
    override['multi_point_mode'] = True
    # 1 .. move wide text up; 2 .. move wide text down; None .. ignore
    # moving text down, looks best combined with text fill bg: DIMTFILL = 1
    move_wide_text = 1
    _suppress_arrow1 = False
    first_run = True

    for p1, p2 in zip(points[:-1], points[1:]):
        _override = dict(override)
        _override['move_wide_text'] = move_wide_text
        if avoid_double_rendering and not first_run:
            _override['dimse1'] = 1
            _override['suppress_arrow1'] = _suppress_arrow1

        style = layout.add_linear_dim(
            Vector(base),
            Vector(p1),
            Vector(p2),
            angle=angle,
            dimstyle=dimstyle,
            override=_override,
            dxfattribs=dxfattribs,
        )
        if first_run:
            _suppress_arrow1 = suppress_arrow1(style)

        renderer = cast(LinearDimension, style.render(ucs, discard=discard))
        if renderer.is_wide_text:
            # after wide text switch moving direction
            if move_wide_text == 1:
                move_wide_text = 2
            else:
                move_wide_text = 1
        else:  # reset to move text up
            move_wide_text = 1
        first_run = False
