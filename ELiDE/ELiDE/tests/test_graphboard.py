from kivy.tests.common import GraphicUnitTest, UnitTestTouch
import networkx as nx

from LiSE import Engine
from LiSE.character import Facade
from ELiDE.app import ELiDEApp
from ELiDE.graph.board import GraphBoard, GraphBoardView, FinalLayout
from .util import idle_until, window_with_widget, ELiDEAppTest


class GraphBoardTest(GraphicUnitTest):
    @staticmethod
    def test_layout_grid():
        spots_wide = 3
        spots_tall = 3
        graph = nx.grid_2d_graph(spots_wide, spots_tall)
        char = Facade(graph)
        app = ELiDEApp()
        spotlayout = FinalLayout()
        arrowlayout = FinalLayout()
        board = GraphBoard(app=app,
                           character=char,
                           spotlayout=spotlayout,
                           arrowlayout=arrowlayout)
        spotlayout.pos = board.pos
        board.bind(pos=spotlayout.setter('pos'))
        spotlayout.size = board.size
        board.bind(size=spotlayout.setter('size'))
        board.add_widget(spotlayout)
        arrowlayout.pos = board.pos
        board.bind(pos=arrowlayout.setter('pos'))
        arrowlayout.size = board.size
        board.bind(size=arrowlayout.setter('size'))
        board.add_widget(arrowlayout)
        board.update()
        boardview = GraphBoardView(board=board)
        win = window_with_widget(boardview)

        @idle_until(timeout=1000, message="Never finished placing spots")
        def all_spots_placed():
            for x in range(spots_wide):
                for y in range(spots_tall):
                    if (x, y) not in board.spot:
                        return False
            return True

        # Don't get too picky about the exact proportions of the grid; just make sure the
        # spots are positioned logically with respect to one another
        for name, spot in board.spot.items():
            x, y = name
            if x > 0:
                assert spot.x > board.spot[x - 1, y].x
            if y > 0:
                assert spot.y > board.spot[x, y - 1].y
            if x < spots_wide - 1:
                assert spot.x < board.spot[x + 1, y].x
            if y < spots_tall - 1:
                assert spot.y < board.spot[x, y + 1].y

    @staticmethod
    def test_select_arrow():
        char = Facade()
        char.add_place(0, _x=0.1, _y=0.1)
        char.add_place(1, _x=0.2, _y=0.1)
        char.add_portal(0, 1)
        app = ELiDEApp()
        board = GraphBoard(app=app, character=char)
        boardview = GraphBoardView(board=board)
        win = window_with_widget(boardview)
        idle_until(lambda: 0 in board.arrow and 1 in board.arrow[0] and board.
                   arrow[0][1] in board.arrowlayout.children)
        ox, oy = board.spot[0].center
        dx, dy = board.spot[1].center
        motion = UnitTestTouch((ox + ((dx - ox) / 2)), dy)
        motion.touch_down()
        motion.touch_up()
        assert app.selection == board.arrow[0][1]

    @staticmethod
    def test_select_spot():
        char = Facade()
        char.add_place(0, _x=0.1, _y=0.1)
        app = ELiDEApp()
        board = GraphBoard(app=app, character=char)
        boardview = GraphBoardView(board=board)
        win = window_with_widget(boardview)
        idle_until(lambda: 0 in board.spot and board.spot[0] in board.
                   spotlayout.children)
        x, y = board.spot[0].center
        motion = UnitTestTouch(x, y)
        motion.touch_down()
        motion.touch_up()
        assert app.selection == board.spot[0]

    @staticmethod
    def test_select_pawn():
        char = Facade()
        char.add_place(0, _x=0.1, _y=0.1)
        char.add_thing('that', location=0)
        app = ELiDEApp()
        board = GraphBoard(app=app, character=char)
        boardview = GraphBoardView(board=board)
        win = window_with_widget(boardview)
        idle_until(
            lambda: 0 in board.spot and board.spot[0] in board.spotlayout.
            children and board.pawn['that'] in board.spot[0].children)
        motion = UnitTestTouch(*board.pawn['that'].center)
        motion.touch_down()
        motion.touch_up()
        assert app.selection == board.pawn['that']

    @staticmethod
    def test_pawn_relocate():
        char = Facade()
        char.add_place(0, _x=0.1, _y=0.1)
        char.add_place(1, _x=0.2, _y=0.1)
        char.add_thing('that', location=0)
        app = ELiDEApp()
        board = GraphBoard(app=app, character=char)
        boardview = GraphBoardView(board=board)
        win = window_with_widget(boardview)
        idle_until(lambda: 0 in board.spot and board.spot[
            0] in board.spotlayout.children and 1 in board.spot and board.spot[
                1] in board.spotlayout.children and 'that' in board.pawn and
                   board.pawn['that'] in board.spot[0].children)
        that = board.pawn['that']
        one = board.spot[1]
        # In a real ELiDE session, the following would happen as a
        # result of a Board.update() call
        char.thing['that']['location'] = that.loc_name = 1
        idle_until(lambda: that in one.children, 1000,
                   "pawn did not relocate within 1000 ticks")


class SwitchGraphTest(ELiDEAppTest):
    def test_character_switch_graph(self):
        with Engine(self.prefix) as eng:
            eng.add_character('physical', nx.grid_2d_graph(10, 1))
            eng.add_character('tall', nx.grid_2d_graph(1, 10))
        app = self.app
        window_with_widget(app.build())
        idle_until(
            lambda: hasattr(app, 'mainscreen') and app.mainscreen.mainview and
            app.mainscreen.statpanel and hasattr(app.mainscreen, 'gridview'))
        idle_until(lambda: app.mainscreen.boardview in app.mainscreen.mainview.
                   children)
        idle_until(lambda: app.mainscreen.boardview.board.children)
        print(
            f'test_character_switch_graph got app {id(app)}, engine proxy {id(app.engine)}'
        )
        first_y = next(iter(
            app.mainscreen.boardview.board.spotlayout.children)).y
        assert all(
            child.y == first_y
            for child in app.mainscreen.boardview.board.spotlayout.children)
        assert len(
            set(child.x for child in
                app.mainscreen.boardview.board.spotlayout.children)) == len(
                    app.mainscreen.boardview.board.spotlayout.children)
        app.character_name = 'tall'

        def all_x_same():
            if app.mainscreen.boardview.board is None or app.mainscreen.boardview.board.spotlayout is None or not app.mainscreen.boardview.board.spotlayout.children:
                return False
            first_x = next(
                iter(app.mainscreen.boardview.board.spotlayout.children)).x
            return all(child.x == first_x for child in
                       app.mainscreen.boardview.board.spotlayout.children)

        idle_until(all_x_same, 1000, "Never got the new board")
        idle_until(
            lambda: len(
                set(child.y for child in app.mainscreen.boardview.board.
                    spotlayout.children)) == len(app.mainscreen.boardview.board
                                                 .spotlayout.children), 1000,
            "New board arranged weird")
