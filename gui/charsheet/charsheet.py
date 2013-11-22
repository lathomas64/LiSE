# This file is part of LiSE, a framework for life simulation games.
# Copyright (c) 2013 Zachary Spector,  zacharyspector@gmail.com
from calendar import (
    CAL_TYPE,
    CalendarView)
from gui.kivybits import SaveableWidgetMetaclass
from table import (
    TableView)
from kivy.uix.gridlayout import GridLayout
from kivy.uix.relativelayout import RelativeLayout
from kivy.properties import (
    NumericProperty,
    DictProperty,
    ObjectProperty)


SHEET_ITEM_TYPE = {
    "THINGTAB": 0,
    "PLACETAB": 1,
    "PORTALTAB": 2,
    "STATTAB": 3,
    "SKILLTAB": 4,
    "THINGCAL": 5,
    "PLACECAL": 6,
    "PORTALCAL": 7,
    "STATCAL": 8,
    "SKILLCAL": 9}
SHEET_TO_CAL_TYPE = dict(
    [(SHEET_ITEM_TYPE[a], CAL_TYPE[a[:-3]]) for a in
     ("THINGCAL", "PLACECAL", "PORTALCAL", "STATCAL", "SKILLCAL")])


class CharSheet(GridLayout):
    __metaclass__ = SaveableWidgetMetaclass
    demands = ["character"]

    tables = [
        (
            "charsheet",
            {"character": "TEXT NOT NULL",
             "visible": "BOOLEAN NOT NULL DEFAULT 0",
             "interactive": "BOOLEAN NOT NULL DEFAULT 1",
             "x_hint": "FLOAT NOT NULL DEFAULT 0.8",
             "y_hint": "FLOAT NOT NULL DEFAULT 0.0",
             "w_hint": "FLOAT NOT NULL DEFAULT 0.2",
             "h_hint": "FLOAT NOT NULL DEFAULT 1.0",
             "style": "TEXT NOT NULL DEFAULT 'default_style'"},
            ("character",),
            {"character": ("character", "name"),
             "style": ("style", "name")},
            []),
        (
            "charsheet_item",
            {"character": "TEXT NOT NULL",
             "idx": "INTEGER NOT NULL",
             "type": "INTEGER NOT NULL",
             "key0": "TEXT",
             "key1": "TEXT",
             "key2": "TEXT"},
            ("character", "idx"),
            {"character": ("charsheet", "character")},
            ["CASE key1 WHEN NULL THEN type NOT IN ({0}) END".format(
                ", ".join([str(SHEET_ITEM_TYPE[typ]) for typ in (
                    "THINGTAB", "THINGCAL",
                    "PLACETAB", "PLACECAL",
                    "PORTALTAB", "PORTALCAL")])),
             "CASE key2 WHEN NULL THEN type<>{0} END".format(
                 str(SHEET_ITEM_TYPE["PORTALTAB"])),
             "idx>=0",
             "idx<={}".format(max(SHEET_ITEM_TYPE.values()))])
    ]
    bone = ObjectProperty()
    style = ObjectProperty()
    completedness = NumericProperty()

    def on_parent(self, i, parent):
        character = parent.parent.character
        self.bone = character.closet.skeleton["charsheet"][unicode(character)]
        for bone in character.closet.skeleton[u"charsheet_item"][
                unicode(character)].iterbones():
            keylst = [bone["key0"], bone["key1"], bone["key2"]]
            if bone["type"] < 5:
                self.add_widget(
                    TableView(
                        character=character,
                        style=character.closet.get_style(self.bone["style"]),
                        item_type=bone["type"],
                        keys=keylst))
            else:
                # from the charsheet's perspective, the calendar's
                # background is the foreground.
                self.add_widget(CalendarView(
                    character=character,
                    style=character.closet.get_style(self.bone["style"]),
                    item_type=bone["type"],
                    keys=keylst))

    def on_touch_down(self, touch):
        for child in self.children:
            if child.on_touch_down(touch):
                return True

    def on_touch_move(self, touch):
        for child in self.children:
            child.on_touch_move(touch)

    def on_touch_up(self, touch):
        for child in self.children:
            child.on_touch_up(touch)


class CharSheetView(RelativeLayout):
    character = ObjectProperty()

    def collide_point(self, x, y):
        return super(CharSheetView, self).collide_point(*self.to_local(x, y))
