from util import SaveableMetaclass, dictify_row, stringlike


"""Widgets to represent places. Pawns move around on top of these."""


__metaclass__ = SaveableMetaclass


class Spot:
    """Controller for the icon that represents a Place.

    Return a Spot representing the given place; at the given x and y
    coordinates on the screen; in the given graph of Spots. The Spot
    will be magically connected to the other Spots in the same way
    that the underlying Places are connected.

    """
    tables = [
        ("spot",
         {"dimension": "text not null default 'Physical'",
          "place": "text not null",
          "img": "text not null default 'default_spot'",
          "x": "integer not null default 50",
          "y": "integer not null default 50",
          "visible": "boolean not null default 1",
          "interactive": "boolean not null default 1"},
         ("dimension", "place"),
         {"dimension, place": ("place", "dimension, name"),
          "img": ("img", "name")},
         [])]

    def __init__(self, db, dimension, place, img, x, y,
                 visible, interactive):
        """Return a new spot on the given board, representing the given place
with the given image. It will be at the given coordinates, and visible
or interactive as indicated.

With db, register the spot with spotdict.

        """
        self.dimension = dimension
        self.place = place
        self.img = img
        self.x = x
        self.y = y
        self._visible = visible
        self._interactive = interactive
        self.grabpoint = None
        self.sprite = None
        self.oldstate = None
        self.newstate = None
        self.hovered = False
        self.tweaks = 0
        if stringlike(self.dimension):
            dimname = self.dimension
        else:
            dimname = self.dimension.name
        if stringlike(self.place):
            placename = self.place
        else:
            placename = self.place.name
        if dimname not in db.spotdict:
            db.spotdict[dimname] = {}
        db.spotdict[dimname][placename] = self
        self.db = db

    def __getattr__(self, attrn):
        if attrn == 'visible':
            return self._visible
        elif attrn == 'interactive':
            return self._interactive
        elif attrn == 'left':
            return self.y
        elif attrn == 'bot':
            return self.x
        elif attrn == 'width':
            return self.img.getwidth()
        elif attrn == 'height':
            return self.img.getheight()
        elif attrn == 'top':
            return self.bot + self.height
        elif attrn == 'right':
            return self.left + self.width
        elif attrn == 'rx':
            return self.width / 2
        elif attrn == 'ry':
            return self.height / 2
        elif attrn == 'r':
            if self.rx > self.ry:
                return self.rx
            else:
                return self.ry
        else:
            raise AttributeError(
                "Spot instance has no such attribute: " +
                attrn)

    def __repr__(self):
        """Represent the coordinates and the name of the place"""
        return "spot(%i,%i)->%s" % (self.x, self.y, str(self.place))

    def __eq__(self, other):
        """Compare the dimension and the name"""
        return (
            isinstance(other, Spot) and
            self.dimension == other.dimension and
            self.name == other.name)

    def unravel(self):
        """Dereference dimension, place, and image. Compute some constants for
graphics calculations."""
        db = self.db
        if stringlike(self.dimension):
            self.dimension = db.dimensiondict[self.dimension]
        if stringlike(self.place):
            self.place = db.itemdict[self.dimension.name][self.place]
        if stringlike(self.img):
            self.img = db.imgdict[self.img]
        self.place.spot = self
        self.rx = self.img.getwidth() / 2
        self.ry = self.img.getheight() / 2
        self.left = self.x - self.rx
        self.right = self.x + self.rx
        self.top = self.y + self.ry
        self.bot = self.y - self.ry
        self.place.spot = self

    def gettup(self):
        """Return my image, left, and bottom"""
        return (self.img, self.getleft(), self.getbot())

    def onclick(self, button, modifiers):
        """Does nothing yet"""
        pass

    def set_hovered(self):
        """Become hovered"""
        if not self.hovered:
            self.hovered = True
            self.tweaks += 1

    def unset_hovered(self):
        """Stop being hovered"""
        if self.hovered:
            self.hovered = False
            self.tweaks += 1

    def set_pressed(self):
        """Become pressed"""
        pass

    def unset_pressed(self):
        """Stop being pressed"""
        pass

    def dropped(self, x, y, button, modifiers):
        """Stop being dragged by the mouse, forget the grabpoint"""
        self.grabpoint = None

    def move_with_mouse(self, x, y, dx, dy, buttons, modifiers):
        """Remember where exactly I was grabbed, then move around with the
mouse, always keeping the same relative position with respect to the
mouse."""
        if self.grabpoint is None:
            self.grabpoint = (x - self.x, y - self.y)
        (grabx, graby) = self.grabpoint
        self.x = x - grabx + dx
        self.left = self.x - self.rx
        self.right = self.x + self.rx
        self.y = y - graby + dy
        self.top = self.y + self.ry
        self.bot = self.y - self.ry

    def get_state_tup(self):
        """Return a tuple with all the information you might need to draw
me."""
        return (
            self.img.name,
            self.x,
            self.y,
            self.visible,
            self.interactive,
            self.grabpoint,
            self.hovered,
            self.tweaks)


spot_dimension_qryfmt = (
    "SELECT {0} FROM spot WHERE dimension IN ({1})".format(
        ", ".join(Spot.colnames["spot"]), "{0}"))


def read_spots_in_boards(db, names):
    """Read all spots in the given boards. Instantiate them, but don't
unravel yet.

Return a 2D dictionary keyed with dimension name, then thing name.

    """
    qryfmt = spot_dimension_qryfmt
    qrystr = qryfmt.format(", ".join(["?"] * len(names)))
    db.c.execute(qrystr, names)
    r = {}
    for name in names:
        r[name] = {}
    for row in db.c:
        rowdict = dictify_row(row, Spot.colnames["spot"])
        rowdict["db"] = db
        r[rowdict["dimension"]][rowdict["place"]] = Spot(**rowdict)
    return r


def unravel_spots(spd):
    """Take a dictionary of spots keyed by place name. Return it with the
contents unraveled."""
    for spot in spd.itervalues():
        spot.unravel()
    return spd


def unravel_spots_in_boards(db, spdd):
    """Unravel the output of read_spots_in_boards."""
    for spots in spdd.itervalues():
        unravel_spots(spots)
    return spdd


def load_spots_in_boards(db, names):
    """Load all spots in the given boards.

Return a 2D dictionary keyed first by board dimension name, then by
place name.

    """
    return unravel_spots_in_boards(read_spots_in_boards(db, names))
