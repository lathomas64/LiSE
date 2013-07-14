# TODO: hands are not being loaded. fix.
from util import SaveableMetaclass, dictify_row, stringlike
from menu import read_menus_in_boards
from img import load_imgs
from style import read_styles
from spot import read_spots_in_boards
from calendar import Calendar, read_calendar_cols_in_boards
from pawn import read_pawns_in_boards
from dimension import read_dimensions, load_dimensions
from card import read_hands_in_boards
from edge import Edge


"""Class for user's view on gameworld, and support functions."""


__metaclass__ = SaveableMetaclass


class Board:
    """A widget notionally representing the game board on which the rest
of the game pieces lie.

Each board represents exactly one dimension in the world model. It has
a width and height in pixels, which do not necessarily match the width
or height of the window it's displayed in--a board may be scrolled
horizontally or vertically. Every board has a static background image,
and may have menus. The menus' positions are relative to the window
rather than the board, but they are linked to the board anyhow, on the
assumption that each board will be open in at most one window at a
time.

"""
    tables = [
        ("board",
         {"dimension": "text not null DEFAULT 'Physical'",
          "wallpaper": "text DEFAULT 'default_wallpaper'",
          "width": "integer not null DEFAULT 4000",
          "height": "integer not null DEFAULT 3000",
          "view_left": "integer not null DEFAULT 0",
          "view_bot": "integer not null DEFAULT 0",
          "calendar_visible": "boolean not null DEFAULT 1",
          "calendar_interactive": "boolean not null DEFAULT 1",
          "calendar_left": "float not null DEFAULT 0.8",
          "calendar_right": "float not null DEFAULT 1.0",
          "calendar_top": "float not null DEFAULT 1.0",
          "calendar_bot": "float not null DEFAULT 0.0",
          "calendar_rows_on_screen": "integer not null DEFAULT 240",
          "calendar_scrolled_to": "integer not null DEFAULT 0"},
         ("dimension",),
         {"wallpaper": ("image", "name")},
         ["calendar_rows_on_screen>0", "calendar_scrolled_to>=0",
          "view_left>=0", "view_bot>=0", "view_left<width",
          "view_bot<height"])
    ]

    def __init__(self, db, dimension, width, height, view_left, view_bot,
                 wallpaper,
                 calendar_left, calendar_right, calendar_top,
                 calendar_bot, calendar_visible, calendar_interactive,
                 calendar_rows_on_screen, calendar_scrolled_to):
        """Return a board representing the given dimension.

        """
        self.db = db
        self._dimension = dimension
        self.width = width
        self.height = height
        self._wallpaper = wallpaper
        self.view_left = view_left
        self.view_bot = view_bot
        caldict = {
            "board": self,
            "left": calendar_left,
            "bot": calendar_bot,
            "right": calendar_right,
            "top": calendar_top,
            "visible": calendar_visible,
            "interactive": calendar_interactive,
            "rows_on_screen": calendar_rows_on_screen,
            "scrolled_to": calendar_scrolled_to}
        self.calendar = Calendar(**caldict)
        self.db.boarddict[self._dimension] = self

    def __getattr__(self, attrn):
        if attrn == "dimension":
            return self.db.dimensiondict[self._dimension]
        elif attrn in ("wallpaper", "img"):
            return self.db.imgdict[self._wallpaper]
        elif attrn == "offset_x":
            return -1 * self.view_left
        elif attrn == "offset_y":
            return -1 * self.view_bot
        elif attrn == "pawndict":
            return self.db.pawndict[str(self)]
        elif attrn == "spotdict":
            return self.db.spotdict[str(self)]
        elif attrn == "menudict":
            return self.db.menudict[str(self)]
        elif attrn == "handdict":
            return self.db.boardhanddict[str(self)]
        elif attrn == "edgedict":
            return self.db.edgedict[str(self)]
        elif attrn == "pawns":
            return self.pawndict.itervalues()
        elif attrn == "spots":
            return self.spotdict.itervalues()
        elif attrn == "menus":
            return self.menudict.itervalues()
        elif attrn == "hands":
            return self.handdict.itervalues()
        elif attrn == "edges":
            return self.edgedict.itervalues()
        else:
            return getattr(self.dimension, attrn)

    def __eq__(self, other):
        return (
            isinstance(other, Board) and
            self.dimension == other.dimension)

    def __hash__(self):
        """Return the hash of the represented dimension.

As there's supposed to be only one board per dimension (though there
may be dimensions without boards), boards can be identified with their
dimension's hash.

        """
        return hash(self.dimension)

    def __repr__(self):
        return "A board, %d pixels wide by %d tall, representing the "\
            "dimension %s, containing %d spots, %d pawns, and %d menus."\
            % (self.width, self.height, self.dimension, len(self.spotdict),
               len(self.pawndict), len(self.menudict))

    def __str__(self):
        return self._dimension

    def get_spot_at(self, x, y):
        for spot in self.spots:
            if (
                    spot.window_left < x < spot.window_right and
                    spot.window_bot < y < spot.window_top):
                return spot
        return None

    def unravel(self):
        """Grab the Python objects referred to by self.wallpaper and
self.dimension, if they are strings; then unravel all pawns, spots,
and menus herein.

        """
        self.dimension.unravel()
        if str(self) not in self.db.pawndict:
            self.db.pawndict[str(self)] = {}
        if str(self) not in self.db.spotdict:
            self.db.spotdict[str(self)] = {}
        if str(self) not in self.db.menudict:
            self.db.menudict[str(self)] = {}
        if str(self) not in self.db.boardhanddict:
            self.db.boardhanddict[str(self)] = {}
        if str(self) not in self.db.edgedict:
            self.db.edgedict[str(self)] = {}
        for pwn in self.pawndict.itervalues():
            pwn.unravel()
        for spt in self.spotdict.itervalues():
            spt.unravel()
            spt.board = self
        for mnu in self.menudict.itervalues():
            mnu.unravel()
        for hand in self.handdict.itervalues():
            hand.unravel()
        for edge in self.edgedict.itervalues():
            edge.unravel()
        self.calendar.unravel()

    def set_gw(self, gw):
        self.gw = gw
        self.calendar.adjust()
        for menu in self.menudict.itervalues():
            menu.adjust()
        for hand in self.handdict.itervalues():
            hand.adjust()
        for port in self.portals:
            if str(port) not in self.db.edgedict[port._dimension]:
                port.edge = Edge(self.gw, port)

    def get_tabdict(self):
        return {
            "board": {
                "dimension": self._dimension,
                "wallpaper": self._wallpaper,
                "width": self.width,
                "height": self.height,
                "view_left": self.view_left,
                "view_bot": self.view_bot,
                "calendar_visible": self.calendar._visible,
                "calendar_interactive": self.calendar._interactive,
                "calendar_left": self.calendar.left_prop,
                "calendar_right": self.calendar.right_prop,
                "calendar_top": self.calendar.top_prop,
                "calendar_bot": self.calendar.bot_prop,
                "calendar_rows_on_screen": self.calendar.rows_on_screen,
                "calendar_scrolled_to": self.calendar.scrolled_to}}

    def delete(self):
        del self.db.boarddict[self._dimension]
        self.erase()

read_some_boards_format = (
    "SELECT {0} FROM board WHERE dimension IN ({1})".format(
        ", ".join(Board.colns), "{0}"))


def read_boards(db, boards):
    """From the given database, read the boards representing the given
dimensions, and everything in them.

The boards don't get unraveled, and are not very useful until they are
unraveled. If you want boards that do things, use load_boards().

    """
    qryfmt = read_some_boards_format
    qrystr = qryfmt.format(", ".join(["?"] * len(boards)))
    db.c.execute(qrystr, boards)
    r = {}
    imgs = set()
    styles = set()
    for row in db.c:
        rowdict = dictify_row(row, Board.colns)
        rowdict["db"] = db
        r[rowdict["dimension"]] = Board(**rowdict)
        imgs.add(rowdict["wallpaper"])
    read_dimensions(db, boards)
    for menus in read_menus_in_boards(db, boards).itervalues():
        for menu in menus.itervalues():
            if stringlike(menu.style):
                styles.add(menu.style)
    for spots in read_spots_in_boards(db, boards).itervalues():
        for spot in spots.itervalues():
            imgs.add(spot._img)
    for pawns in read_pawns_in_boards(db, boards).itervalues():
        for pawn in pawns.itervalues():
            if stringlike(pawn.img):
                imgs.add(pawn.img)
    for calcols in read_calendar_cols_in_boards(db, boards).itervalues():
        for calcol in calcols.itervalues():
            if stringlike(calcol.style):
                styles.add(calcol.style)
            if stringlike(calcol.cel_style):
                styles.add(calcol.cel_style)
    load_imgs(db, list(imgs))
    read_styles(db, list(styles))
    return r


def load_boards(db, boards):
    """From the given database, load the boards representing the
dimensions by the given names, returning a dictionary keyed with the
dimension names."""
    qryfmt = read_some_boards_format
    qrystr = qryfmt.format(", ".join(["?"] * len(boards)))
    db.c.execute(qrystr, boards)
    r = {}
    imgs = set()
    styles = set()
    for row in db.c:
        rowdict = dictify_row(row, Board.colns)
        rowdict["db"] = db
        r[rowdict["dimension"]] = Board(**rowdict)
        imgs.add(rowdict["wallpaper"])
    load_dimensions(db, boards)
    for menus in read_menus_in_boards(db, boards).itervalues():
        for menu in menus.itervalues():
            styles.add(menu._style)
    for spots in read_spots_in_boards(db, boards).itervalues():
        for spot in spots.itervalues():
            imgs.add(spot._img)
    for pawns in read_pawns_in_boards(db, boards).itervalues():
        for pawn in pawns.itervalues():
            imgs.add(pawn._img)
    for calcols in read_calendar_cols_in_boards(db, boards).itervalues():
        for calcol in calcols.itervalues():
            styles.add(calcol._style)
            styles.add(calcol._cel_style)
    for hands in read_hands_in_boards(db, boards).itervalues():
        for handname in hands.iterkeys():
            for card in db.handcarddict[handname]:
                card = db.carddict[str(card)]
                imgs.add(card._img)
    load_imgs(db, list(imgs))
    read_styles(db, list(styles))
    for board in r.itervalues():
        board.unravel()
    return r


def load_board(db, boardn):
    """From the given database, load the board representing the dimension
named thus."""
    return load_boards(db, [boardn])[boardn]
