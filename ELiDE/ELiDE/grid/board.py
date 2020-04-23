from functools import partial

from kivy.clock import Clock
from kivy.logger import Logger
from kivy.properties import (
    DictProperty,
    NumericProperty,
    ObjectProperty,
    ReferenceListProperty
)
from kivy.uix.relativelayout import RelativeLayout
from kivy.lang.builder import Builder
from .spot import GridSpot
from .pawn import GridPawn
from ..boardview import BoardView


class GridBoard(RelativeLayout):
    selection = ObjectProperty()
    character = ObjectProperty()
    tile_width = NumericProperty()
    tile_height = NumericProperty()
    tile_size = ReferenceListProperty(tile_width, tile_height)
    pawn = DictProperty({})
    spot = DictProperty({})
    spot_cls = ObjectProperty(GridSpot)
    pawn_cls = ObjectProperty(GridPawn)

    def do_layout(self, *args):
        Logger.debug("GridBoard laying out at size {}".format(self.size))
        rows = cols = 0
        colw = rowh = 0
        for name, spot in self.spot.items():
            row, col = name
            rows = max((rows, row))
            cols = max((cols, col))
            colw = max((spot.width, colw))
            rowh = max((spot.height, rowh))
        self.width = colw * cols
        self.height = rowh * rows
        for name, tile in self.spot.items():
            gx, gy = name
            tile.pos = gx * colw, gy * rowh

    def add_spot(self, placen, *args):
        if (
            placen in self.character.place and
            placen not in self.spot
        ):
            self.add_widget(self.make_spot(self.character.place[placen]))

    def make_spot(self, place):
        if place["name"] in self.spot:
            raise KeyError("Already have a Spot for this Place")
        r = self.spot_cls(
            board=self,
            proxy=place
        )
        self.spot[place["name"]] = r
        return r

    def make_pawn(self, thing):
        if thing["name"] in self.pawn:
            raise KeyError("Already have a Pawn for this Thing")
        r = self.pawn_cls(
            board=self,
            proxy=thing
        )
        self.pawn[thing["name"]] = r
        return r

    def _trigger_add_tile(self, placen):
        part = partial(self.add_spot, placen)
        Clock.unschedule(part)
        Clock.schedule_once(part, 0)

    def add_new_spots(self, *args):
        placemap = self.character.place
        tilemap = self.spot
        default_image_paths = self.spot_cls.default_image_paths
        default_zeroes = [0] * len(default_image_paths)
        places2add = []
        nodes_patch = {}
        for place_name, place in placemap.items():
            if place_name not in tilemap:
                places2add.append(place)
                patch = {}
                if '_image_paths' in place:
                    zeroes = [0] * len(place['_image_paths'])
                else:
                    patch['_image_paths'] = default_image_paths
                    zeroes = default_zeroes
                if '_offxs' not in place:
                    patch['_offxs'] = zeroes
                if '_offys' not in place:
                    patch['_offys'] = zeroes
                if patch:
                    nodes_patch[place_name] = patch
        if nodes_patch:
            self.character.node.patch(nodes_patch)
        make_tile = self.make_spot
        add_widget = self.add_widget
        for place in places2add:
            add_widget(make_tile(place))

    def add_pawn(self, thingn, *args):
        if (
            thingn in self.character.thing and
            thingn not in self.pawn
        ):
            pwn = self.make_pawn(self.character.thing[thingn])
            whereat = self.spot[pwn.thing['location']]
            whereat.add_widget(pwn)
            self.pawn[thingn] = pwn

    def _trigger_add_pawn(self, thingn):
        part = partial(self.add_pawn, thingn)
        Clock.unschedule(part)
        Clock.schedule_once(part, 0)

    def add_new_pawns(self, *args):
        nodes_patch = {}
        things2add = []
        pawns_added = []
        pawnmap = self.pawn
        default_image_paths = GridPawn.default_image_paths
        default_zeroes = [0] * len(default_image_paths)
        for thingn, thing in self.character.thing.items():
            if thingn not in pawnmap:
                things2add.append(thing)
                patch = {}
                if '_image_paths' in thing:
                    zeroes = [0] * len(thing['_image_paths'])
                else:
                    patch['_image_paths'] = default_image_paths
                    zeroes = default_zeroes
                if '_offxs' not in thing:
                    patch['_offxs'] = zeroes
                if '_offys' not in thing:
                    patch['_offys'] = zeroes
                if patch:
                    nodes_patch[thingn] = patch
        if nodes_patch:
            self.character.node.patch(nodes_patch)
        make_pawn = self.make_pawn
        tilemap = self.spot
        for thing in things2add:
            pwn = make_pawn(thing)
            pawns_added.append(pwn)
            whereat = tilemap[thing['location']]
            whereat.add_widget(pwn)

    def on_parent(self, *args):
        if not self.parent or hasattr(self, '_parented'):
            return
        self._parented = True
        self.update()

    def remove_absent_pawns(self):
        pawnmap = self.pawn
        thingmap = self.character.thing
        for name, pawn in list(pawnmap.items()):
            if name not in thingmap:
                pawn.parent.remove_widget(pawn)
                del pawnmap[name]

    def remove_absent_spots(self):
        spotmap = self.spot
        placemap = self.character.place
        remove_widget = self.remove_widget
        for name, spot in list(spotmap.items()):
            if name not in placemap:
                remove_widget(spot)
                del spotmap[name]

    def update(self, *args):
        self.remove_absent_pawns()
        self.remove_absent_spots()
        self.add_new_spots()
        self.add_new_pawns()
        for spot in self.spot.values():
            spot.finalize()
        for pawn in self.pawn.values():
            pawn.finalize()


    def update_from_delta(self, delta, *args):
        pass

    def _trigger_update_from_delta(self, delta, *args):
        part = partial(self.update_from_delta, delta)
        Clock.unschedule(part)
        Clock.schedule_once(part, 0)


class GridBoardView(BoardView):
    pass


Builder.load_string("""
<GridBoard>:
    app: app
    size_hint: None, None
<GridBoardView>:
    plane: boardplane
    BoardScatterPlane:
        id: boardplane
        board: root.board
        scale_min: root.scale_min
        scale_max: root.scale_max
""")