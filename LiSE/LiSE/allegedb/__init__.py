# This file is part of allegedb, an object relational mapper for versioned graphs.
# Copyright (C) Zachary Spector. public@zacharyspector.com
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, version 3.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""The main interface to the allegedb ORM, and some supporting functions and classes"""
from contextlib import ContextDecorator, contextmanager
import gc
from weakref import WeakValueDictionary

from blinker import Signal
import networkx as nx

from .window import update_window, update_backward_window
from .cache import HistoryError
from .graph import (DiGraph, Node, Edge, GraphsMapping)
from .query import QueryEngine, TimeError
from .window import HistoryError


def loaded_keep_test(test_turn, test_tick, past_turn, past_tick, future_turn,
                     future_tick):
    return (past_turn < test_turn or
            (past_turn == test_turn and past_tick <= test_tick)) and (
                future_turn > test_turn or
                (future_turn == test_turn and future_tick >= test_tick))


class GraphNameError(KeyError):
    """For errors involving graphs' names"""


class PlanningContext(ContextDecorator):
    """A context manager for 'hypothetical' edits.

    Start a block of code like:

    ```
    with orm.plan():
        ...
    ```

    and any changes you make to the world state within that block will be
    'plans,' meaning that they are used as defaults. The world will
    obey your plan unless you make changes to the same entities outside
    of the plan, in which case the world will obey those, and cancel any
    future plan.

    New branches cannot be started within plans. The ``with orm.forward():``
    optimization is disabled within a ``with orm.plan():`` block, so
    consider another approach instead of making a very large plan.

    """
    __slots__ = ['orm', 'id', 'forward']

    def __init__(self, orm):
        self.orm = orm

    def __enter__(self):
        orm = self.orm
        if orm._planning:
            raise ValueError("Already planning")
        orm._planning = True
        branch, turn, tick = orm._btt()
        self.id = myid = orm._last_plan = orm._last_plan + 1
        self.forward = orm._forward
        if orm._forward:
            orm._forward = False
        orm._plans[myid] = branch, turn, tick
        orm._plans_uncommitted.append((myid, branch, turn, tick))
        orm._branches_plans[branch].add(myid)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.orm._planning = False
        if self.forward:
            self.orm._forward = True


class TimeSignal:
    """Acts like a tuple of ``(branch, turn)`` for the most part.

    This wraps a ``Signal``. To set a function to be called whenever the branch
    or turn changes, pass it to my ``connect`` method.

    """
    def __init__(self, engine, sig):
        self.engine = engine
        self.branch = self.engine.branch
        self.turn = self.engine.turn
        self.sig = sig

    def __iter__(self):
        yield self.branch
        yield self.turn

    def __len__(self):
        return 2

    def __getitem__(self, i):
        if i in ('branch', 0):
            return self.branch
        if i in ('turn', 1):
            return self.turn
        raise IndexError

    def connect(self, *args, **kwargs):
        self.sig.connect(*args, **kwargs)

    def send(self, *args, **kwargs):
        self.sig.send(*args, **kwargs)

    def __str__(self):
        return str(tuple(self))

    def __eq__(self, other):
        return tuple(self) == other

    def __ne__(self, other):
        return tuple(self) != other

    def __gt__(self, other):
        return tuple(self) > other

    def __ge__(self, other):
        return tuple(self) >= other

    def __lt__(self, other):
        return tuple(self) < other

    def __le__(self, other):
        return tuple(self) <= other


class TimeSignalDescriptor:
    __doc__ = TimeSignal.__doc__
    signals = {}

    def __get__(self, inst, cls):
        if id(inst) not in self.signals:
            self.signals[id(inst)] = Signal()
        return TimeSignal(inst, self.signals[id(inst)])

    def __set__(self, inst, val):
        if id(inst) not in self.signals:
            self.signals[id(inst)] = Signal()
        sig = self.signals[id(inst)]
        branch_then, turn_then, tick_then = inst._btt()
        branch_now, turn_now = val
        if (branch_then, turn_then) == (branch_now, turn_now):
            return
        e = inst
        # enforce the arrow of time, if it's in effect
        if e._forward and not e._planning:
            if branch_now != branch_then:
                raise TimeError("Can't change branches in a forward context")
            if turn_now < turn_then:
                raise TimeError(
                    "Can't time travel backward in a forward context")
            if turn_now > turn_then + 1:
                raise TimeError("Can't skip turns in a forward context")
        # make sure I'll end up within the revision range of the
        # destination branch
        branches = e._branches

        if branch_now in branches:
            tick_now = e._turn_end_plan.setdefault((branch_now, turn_now),
                                                   tick_then)
            parent, turn_start, tick_start, turn_end, tick_end = branches[
                branch_now]
            if turn_now < turn_start:
                raise ValueError("The turn number {} "
                                 "occurs before the start of "
                                 "the branch {}".format(turn_now, branch_now))
            if turn_now == turn_start and tick_now < tick_start:
                raise ValueError("The tick number {}"
                                 "on turn {} "
                                 "occurs before the start of "
                                 "the branch {}".format(
                                     tick_now, turn_now, branch_now))
            if not e._planning and (turn_now > turn_end or
                                    (turn_now == turn_end
                                     and tick_now > tick_end)):
                branches[
                    branch_now] = parent, turn_start, tick_start, turn_now, tick_now
        else:
            tick_now = tick_then
            branches[branch_now] = (branch_then, turn_now, tick_now, turn_now,
                                    tick_now)
            e.query.new_branch(branch_now, branch_then, turn_now, tick_now)
        e._obranch, e._oturn = val

        if not e._planning:
            if tick_now > e._turn_end[val]:
                e._turn_end[val] = tick_now
        e._otick = e._turn_end_plan[val] = tick_now
        sig.send(e,
                 branch_then=branch_then,
                 turn_then=turn_then,
                 tick_then=tick_then,
                 branch_now=branch_now,
                 turn_now=turn_now,
                 tick_now=tick_now)


def setgraphval(delta, graph, key, val):
    """Change a delta to say that a graph stat was set to a certain value"""
    delta.setdefault(graph, {})[key] = val


def setnode(delta, graph, node, exists):
    """Change a delta to say that a node was created or deleted"""
    delta.setdefault(graph, {}).setdefault('nodes', {})[node] = bool(exists)


def setnodeval(delta, graph, node, key, value):
    """Change a delta to say that a node stat was set to a certain value"""
    if (graph in delta and 'nodes' in delta[graph]
            and node in delta[graph]['nodes']
            and not delta[graph]['nodes'][node]):
        return
    delta.setdefault(graph, {}).setdefault('node_val',
                                           {}).setdefault(node,
                                                          {})[key] = value


def setedge(delta, is_multigraph, graph, orig, dest, idx, exists):
    """Change a delta to say that an edge was created or deleted"""
    if is_multigraph(graph):
        delta.setdefault(graph, {}).setdefault('edges', {})\
            .setdefault(orig, {}).setdefault(dest, {})[idx] = bool(exists)
    else:
        delta.setdefault(graph, {}).setdefault('edges', {})\
            .setdefault(orig, {})[dest] = bool(exists)


def setedgeval(delta, is_multigraph, graph, orig, dest, idx, key, value):
    """Change a delta to say that an edge stat was set to a certain value"""
    if is_multigraph(graph):
        if (graph in delta and 'edges' in delta[graph]
                and orig in delta[graph]['edges']
                and dest in delta[graph]['edges'][orig]
                and idx in delta[graph]['edges'][orig][dest]
                and not delta[graph]['edges'][orig][dest][idx]):
            return
        delta.setdefault(graph, {}).setdefault('edge_val', {})\
            .setdefault(orig, {}).setdefault(dest, {})\
            .setdefault(idx, {})[key] = value
    else:
        if (graph in delta and 'edges' in delta[graph]
                and orig in delta[graph]['edges']
                and dest in delta[graph]['edges'][orig]
                and not delta[graph]['edges'][orig][dest]):
            return
        delta.setdefault(graph, {}).setdefault('edge_val', {})\
            .setdefault(orig, {}).setdefault(dest, {})[key] = value


class ORM(object):
    """Instantiate this with the same string argument you'd use for a
    SQLAlchemy ``create_engine`` call. This will be your interface to
    allegedb.

    """
    node_cls = Node
    edge_cls = Edge
    query_engine_cls = QueryEngine
    illegal_graph_names = ['global']
    illegal_node_names = ['nodes', 'node_val', 'edges', 'edge_val']
    time = TimeSignalDescriptor()

    def _graph_state_hash(self, nodes, edges, vals):
        from hashlib import blake2b
        qpac = self.query.pack

        if isinstance(qpac(' '), str):

            def pack(x):
                return qpac(x).encode()
        else:
            pack = qpac
        nodes_hash = 0
        for name, val in nodes.items():
            hash = blake2b(pack(name))
            hash.update(pack(val))
            nodes_hash ^= int.from_bytes(hash.digest(), 'little')
        edges_hash = 0
        for orig, dests in edges.items():
            for dest, idxs in dests.items():
                for idx, val in idxs.items():
                    hash = blake2b(pack(orig))
                    hash.update(pack(dest))
                    hash.update(pack(idx))
                    hash.update(pack(val))
                    edges_hash ^= int.from_bytes(hash.digest(), 'little')
        val_hash = 0
        for key, val in vals.items():
            hash = blake2b(pack(key))
            hash.update(pack(val))
            val_hash ^= int.from_bytes(hash.digest(), 'little')
        total_hash = blake2b(nodes_hash.to_bytes(64, 'little'))
        total_hash.update(edges_hash.to_bytes(64, 'little'))
        total_hash.update(val_hash.to_bytes(64, 'little'))
        return total_hash.digest()

    def _kfhash(self, graphn, branch, turn, tick, nodes, edges, vals):
        """Return a hash digest of a keyframe"""
        from hashlib import blake2b
        qpac = self.query.pack

        if isinstance(qpac(' '), str):

            def pack(x):
                return qpac(x).encode()
        else:
            pack = qpac
        total_hash = blake2b(pack(graphn))
        total_hash.update(pack(branch))
        total_hash.update(pack(turn))
        total_hash.update(pack(tick))
        total_hash.update(self._graph_state_hash(nodes, edges, vals))
        return total_hash.digest()

    def _make_node(self, graph, node):
        return self.node_cls(graph, node)

    def _get_node(self, graph, node):
        node_objs, node_exists, make_node = self._get_node_stuff
        if type(graph) is str:
            graphn = graph
            graph = self.graph[graphn]
        else:
            graphn = graph.name
        key = (graphn, node)
        if key in node_objs:
            ret = node_objs[key]
            if ret._validate_node_type():
                return ret
            else:
                del node_objs[key]
        if not node_exists(graphn, node):
            raise KeyError("No such node: {} in {}".format(node, graphn))
        ret = make_node(graph, node)
        node_objs[key] = ret
        return ret

    def _make_edge(self, graph, orig, dest, idx):
        return self.edge_cls(graph, orig, dest, idx)

    def _get_edge(self, graph, orig, dest, idx=0):
        edge_objs, edge_exists, make_edge = self._get_edge_stuff
        if type(graph) is str:
            graphn = graph
            graph = self.graph[graphn]
        else:
            graphn = graph.name
        key = (graphn, orig, dest, idx)
        if key in edge_objs:
            return edge_objs[key]
        if not edge_exists(graphn, orig, dest, idx):
            raise KeyError("No such edge: {}->{}[{}] in {}".format(
                orig, dest, idx, graphn))
        ret = make_edge(graph, orig, dest, idx)
        edge_objs[key] = ret
        return ret

    def plan(self):
        return PlanningContext(self)

    plan.__doc__ = PlanningContext.__doc__

    @contextmanager
    def advancing(self):
        """A context manager for when time is moving forward one turn at a time.

        When used in LiSE, this means that the game is being simulated.
        It changes how the caching works, making it more efficient.

        """
        if self._forward:
            raise ValueError("Already advancing")
        self._forward = True
        yield
        self._forward = False

    @contextmanager
    def batch(self):
        """A context manager for when you're creating lots of state.

        Reads will be much slower in a batch, but writes will be faster.

        You *can* combine this with ``advancing`` but it isn't any faster.

        """
        if self._no_kc:
            raise ValueError("Already in a batch")
        self._no_kc = True
        gc_was_active = gc.isenabled()
        if gc_was_active:
            gc.disable()
        yield
        if gc_was_active:
            gc.enable()
            gc.collect()
        self._no_kc = False

    def get_delta(self, branch, turn_from, tick_from, turn_to, tick_to):
        """Get a dictionary describing changes to all graphs.

        The keys are graph names. Their values are dictionaries of the graphs'
        attributes' new values, with ``None`` for deleted keys. Also in those graph
        dictionaries are special keys 'node_val' and 'edge_val' describing changes
        to node and edge attributes, and 'nodes' and 'edges' full of booleans
        indicating whether a node or edge exists.

        """
        from functools import partial
        if turn_from == turn_to:
            return self.get_turn_delta(branch, turn_from, tick_from, tick_to)
        delta = {}
        graph_objs = self._graph_objs
        if turn_to < turn_from:
            updater = partial(update_backward_window, turn_from, tick_from,
                              turn_to, tick_to)
            gvbranches = self._graph_val_cache.presettings
            nbranches = self._nodes_cache.presettings
            nvbranches = self._node_val_cache.presettings
            ebranches = self._edges_cache.presettings
            evbranches = self._edge_val_cache.presettings
            tick_to += 1
        else:
            updater = partial(update_window, turn_from, tick_from, turn_to,
                              tick_to)
            gvbranches = self._graph_val_cache.settings
            nbranches = self._nodes_cache.settings
            nvbranches = self._node_val_cache.settings
            ebranches = self._edges_cache.settings
            evbranches = self._edge_val_cache.settings

        if branch in gvbranches:
            updater(partial(setgraphval, delta), gvbranches[branch])

        if branch in nbranches:
            updater(partial(setnode, delta), nbranches[branch])

        if branch in nvbranches:
            updater(partial(setnodeval, delta), nvbranches[branch])

        if branch in ebranches:
            updater(
                partial(setedge, delta,
                        lambda g: graph_objs[g].is_multigraph()),
                ebranches[branch])

        if branch in evbranches:
            updater(
                partial(setedgeval, delta,
                        lambda g: graph_objs[g].is_multigraph()),
                evbranches[branch])

        return delta

    def get_turn_delta(self,
                       branch=None,
                       turn=None,
                       tick_from=0,
                       tick_to=None):
        """Get a dictionary describing changes made on a given turn.

        If ``tick_to`` is not supplied, report all changes after ``tick_from``
        (default 0).

        The keys are graph names. Their values are dictionaries of the graphs'
        attributes' new values, with ``None`` for deleted keys. Also in those graph
        dictionaries are special keys 'node_val' and 'edge_val' describing changes
        to node and edge attributes, and 'nodes' and 'edges' full of booleans
        indicating whether a node or edge exists.

        :arg branch: A branch of history; defaults to the current branch
        :arg turn: The turn in the branch; defaults to the current turn
        :arg tick_from: Starting tick; defaults to 0

        """
        branch = branch or self.branch
        turn = turn or self.turn
        tick_to = tick_to or self.tick
        delta = {}
        if tick_from < tick_to:
            gvbranches = self._graph_val_cache.settings
            nbranches = self._nodes_cache.settings
            nvbranches = self._node_val_cache.settings
            ebranches = self._edges_cache.settings
            evbranches = self._edge_val_cache.settings
            tick_to += 1
        else:
            gvbranches = self._graph_val_cache.presettings
            nbranches = self._nodes_cache.presettings
            nvbranches = self._node_val_cache.presettings
            ebranches = self._edges_cache.presettings
            evbranches = self._edge_val_cache.presettings

        if branch in gvbranches and turn in gvbranches[branch]:
            for graph, key, value in gvbranches[branch][turn][
                    tick_from:tick_to]:
                if graph in delta:
                    delta[graph][key] = value
                else:
                    delta[graph] = {key: value}

        if branch in nbranches and turn in nbranches[branch]:
            for graph, node, exists in nbranches[branch][turn][
                    tick_from:tick_to]:
                delta.setdefault(graph, {}).setdefault('nodes',
                                                       {})[node] = bool(exists)

        if branch in nvbranches and turn in nvbranches[branch]:
            for graph, node, key, value in nvbranches[branch][turn][
                    tick_from:tick_to]:
                if (graph in delta and 'nodes' in delta[graph]
                        and node in delta[graph]['nodes']
                        and not delta[graph]['nodes'][node]):
                    continue
                nodevd = delta.setdefault(graph, {}).setdefault('node_val', {})
                if node in nodevd:
                    nodevd[node][key] = value
                else:
                    nodevd[node] = {key: value}

        graph_objs = self._graph_objs
        if branch in ebranches and turn in ebranches[branch]:
            for graph, orig, dest, idx, exists in ebranches[branch][turn][
                    tick_from:tick_to]:
                if graph_objs[graph].is_multigraph():
                    if (graph in delta and 'edges' in delta[graph]
                            and orig in delta[graph]['edges']
                            and dest in delta[graph]['edges'][orig]
                            and idx in delta[graph]['edges'][orig][dest]
                            and not delta[graph]['edges'][orig][dest][idx]):
                        continue
                    delta.setdefault(graph, {}).setdefault('edges', {})\
                        .setdefault(orig, {}).setdefault(dest, {})[idx] = bool(exists)
                else:
                    if (graph in delta and 'edges' in delta[graph]
                            and orig in delta[graph]['edges']
                            and dest in delta[graph]['edges'][orig]
                            and not delta[graph]['edges'][orig][dest]):
                        continue
                    delta.setdefault(graph, {}).setdefault('edges', {})\
                        .setdefault(orig, {})[dest] = bool(exists)

        if branch in evbranches and turn in evbranches[branch]:
            for graph, orig, dest, idx, key, value in evbranches[branch][turn][
                    tick_from:tick_to]:
                edgevd = delta.setdefault(graph, {}).setdefault('edge_val', {})\
                    .setdefault(orig, {}).setdefault(dest, {})
                if graph_objs[graph].is_multigraph():
                    if idx in edgevd:
                        edgevd[idx][key] = value
                    else:
                        edgevd[idx] = {key: value}
                else:
                    edgevd[key] = value

        return delta

    def _init_caches(self):
        from collections import defaultdict
        from .cache import Cache, NodesCache, EdgesCache
        self._where_cached = defaultdict(list)
        self._node_objs = node_objs = WeakValueDictionary()
        self._get_node_stuff = (node_objs, self._node_exists, self._make_node)
        self._edge_objs = edge_objs = WeakValueDictionary()
        self._get_edge_stuff = (edge_objs, self._edge_exists, self._make_edge)
        self._childbranch = defaultdict(set)
        """Immediate children of a branch"""
        self._branches = {}
        """Start time, end time, and parent of each branch"""
        self._branch_parents = defaultdict(set)
        """Parents of a branch at any remove"""
        self._turn_end = defaultdict(lambda: 0)
        """Tick on which a (branch, turn) ends"""
        self._turn_end_plan = defaultdict(lambda: 0)
        """Tick on which a (branch, turn) ends, even if it hasn't been simulated"""
        self._graph_objs = {}
        self._plans = {}
        self._branches_plans = defaultdict(set)
        self._plan_ticks = defaultdict(lambda: defaultdict(list))
        self._time_plan = {}
        self._plans_uncommitted = []
        self._plan_ticks_uncommitted = []
        self._graph_val_cache = Cache(self)
        self._graph_val_cache.name = 'graph_val_cache'
        self._nodes_cache = NodesCache(self)
        self._nodes_cache.name = 'nodes_cache'
        self._edges_cache = EdgesCache(self)
        self._edges_cache.name = 'edges_cache'
        self._node_val_cache = Cache(self)
        self._node_val_cache.name = 'node_val_cache'
        self._edge_val_cache = Cache(self)
        self._edge_val_cache.name = 'edge_val_cache'
        self._caches = [
            self._graph_val_cache, self._nodes_cache, self._edges_cache,
            self._node_val_cache, self._edge_val_cache
        ]

    def _load_graphs(self):
        self.graph = GraphsMapping(self)
        for (graph, typ) in self.query.graphs_types():
            if typ != 'DiGraph':
                raise NotImplementedError("Only DiGraph for now")
            self._graph_objs[graph] = DiGraph(self, graph)

    def __init__(self, dbstring, alchemy=True, connect_args=None):
        """Make a SQLAlchemy engine if possible, else a sqlite3 connection. In
        either case, begin a transaction.

        :arg dbstring: rfc1738 URL for a database connection. Unless it begins with
        "sqlite:///", SQLAlchemy will be required.
        :arg alchemy: Set to ``False`` to use the precompiled SQLite queries even if
        SQLAlchemy is available.
        :arg connect_args: Dictionary of keyword arguments to be used for the database
        connection.

        """
        connect_args = connect_args or {}
        self._planning = False
        self._forward = False
        self._no_kc = False
        # in case this is the first startup
        self._obranch = 'trunk'
        self._otick = self._oturn = 0
        self._init_caches()
        if hasattr(self, '_post_init_cache_hook'):
            self._post_init_cache_hook()
        if not hasattr(self, 'query'):
            self.query = self.query_engine_cls(dbstring, connect_args, alchemy,
                                               getattr(self, 'pack', None),
                                               getattr(self, 'unpack', None))
        self._edge_val_cache.setdb = self.query.edge_val_set
        self._edge_val_cache.deldb = self.query.edge_val_del_time
        self._node_val_cache.setdb = self.query.node_val_set
        self._node_val_cache.deldb = self.query.node_val_del_time
        self._edges_cache.setdb = self.query.exist_edge
        self._edges_cache.deldb = self.query.edges_del_time
        self._nodes_cache.setdb = self.query.exist_node
        self._nodes_cache.deldb = self.query.nodes_del_time
        self._graph_val_cache.setdb = self.query.graph_val_set
        self._graph_val_cache.deldb = self.query.graph_val_del_time
        self.query.initdb()
        self._obranch = self.query.get_branch()
        self._oturn = self.query.get_turn()
        self._otick = self.query.get_tick()
        for (branch, parent, parent_turn, parent_tick, end_turn,
             end_tick) in self.query.all_branches():
            self._branches[branch] = (parent, parent_turn, parent_tick,
                                      end_turn, end_tick)
            self._upd_branch_parentage(parent, branch)
        for (branch, turn, end_tick, plan_end_tick) in self.query.turns_dump():
            self._turn_end[branch, turn] = end_tick
            self._turn_end_plan[branch, turn] = plan_end_tick
        if 'trunk' not in self._branches:
            self._branches['trunk'] = None, 0, 0, 0, 0
        self._new_keyframes = []
        self._nbtt_stuff = (self._btt, self._turn_end_plan, self._turn_end,
                            self._plan_ticks, self._plan_ticks_uncommitted,
                            self._time_plan, self._branches)
        self._node_exists_stuff = (self._nodes_cache.retrieve, self._btt)
        self._exist_node_stuff = (self._nbtt, self.query.exist_node,
                                  self._nodes_cache.store)
        self._edge_exists_stuff = (self._edges_cache.retrieve, self._btt)
        self._exist_edge_stuff = (self._nbtt, self.query.exist_edge,
                                  self._edges_cache.store)
        self._load_graphs()
        assert hasattr(self, 'graph')
        self._keyframes_list = []
        self._keyframes_dict = {}
        self._keyframes_times = set()
        self._loaded = {}  # branch: (turn_from, tick_from, turn_to, tick_to)
        self._init_load()

    def _init_load(self):
        keyframes_list = self._keyframes_list
        keyframes_dict = self._keyframes_dict
        keyframes_times = self._keyframes_times
        for graph, branch, turn, tick in self.query.keyframes_list():
            keyframes_list.append((graph, branch, turn, tick))
            if branch not in keyframes_dict:
                keyframes_dict[branch] = {turn: {tick}}
            else:
                keyframes_dict_branch = keyframes_dict[branch]
                if turn not in keyframes_dict_branch:
                    keyframes_dict_branch[turn] = {tick}
                else:
                    keyframes_dict_branch[turn].add(tick)
            keyframes_times.add((branch, turn, tick))
        self._load_at(*self._btt())

        last_plan = -1
        plans = self._plans
        branches_plans = self._branches_plans
        for plan, branch, turn, tick in self.query.plans_dump():
            plans[plan] = branch, turn, tick
            branches_plans[branch].add(plan)
            if plan > last_plan:
                last_plan = plan
        self._last_plan = last_plan
        plan_ticks = self._plan_ticks
        time_plan = self._time_plan
        for plan, turn, tick in self.query.plan_ticks_dump():
            plan_ticks[plan][turn].append(tick)
            time_plan[plans[plan][0], turn, tick] = plan

    def _upd_branch_parentage(self, parent, child):
        self._childbranch[parent].add(child)
        self._branch_parents[child].add(parent)
        while parent in self._branches:
            parent, _, _, _, _ = self._branches[parent]
            self._branch_parents[child].add(parent)

    def _snap_keyframe(self, graph, branch, turn, tick, nodes, edges,
                       graph_val):
        nodes_keyframes_branch_d = self._nodes_cache.keyframe[graph, ][branch]
        if turn in nodes_keyframes_branch_d:
            nodes_keyframes_branch_d[turn][tick] = {
                node: True
                for node in nodes
            }
        else:
            nodes_keyframes_branch_d[turn] = {
                tick: {node: True
                       for node in nodes}
            }
        nvck = self._node_val_cache.keyframe
        for node, vals in nodes.items():
            node_val_keyframe_branch_d = nvck[graph, node][branch]
            if turn in node_val_keyframe_branch_d:
                node_val_keyframe_branch_d[turn][tick] = vals
            else:
                node_val_keyframe_branch_d[turn] = {tick: vals}
        eck = self._edges_cache.keyframe
        evck = self._edge_val_cache.keyframe
        for orig, dests in edges.items():
            for dest, vals in dests.items():
                edge_val_keyframe_branch_d = evck[graph, orig, dest, 0][branch]
                edges_keyframe_branch_d = eck[graph, orig, dest][branch]
                if turn in edges_keyframe_branch_d:
                    edges_keyframe_branch_d[turn][tick] = {}
                else:
                    edges_keyframe_branch_d[turn] = {tick: {}}
                ekbdrt = edges_keyframe_branch_d[turn][tick]
                ekbdrt[0] = True
                assert edges_keyframe_branch_d[turn][tick][0]
                if turn in edge_val_keyframe_branch_d:
                    edge_val_keyframe_branch_d[turn][tick] = vals
                else:
                    edge_val_keyframe_branch_d[turn] = {tick: vals}
        gvkb = self._graph_val_cache.keyframe[graph, ][branch]
        if turn in gvkb:
            gvkb[turn][tick] = graph_val
        else:
            gvkb[turn] = {tick: graph_val}

    def snap_keyframe(self):
        branch, turn, tick = self._btt()
        snapp = self._snap_keyframe
        kfl = self._keyframes_list
        kfd = self._keyframes_dict
        kfs = self._keyframes_times
        nkfs = self._new_keyframes
        for graphn, graph in self.graph.items():
            nodes = graph._nodes_state()
            edges = graph._edges_state()
            val = graph._val_state()
            snapp(graphn, branch, turn, tick, nodes, edges, val)
            nkfs.append((graphn, branch, turn, tick, nodes, edges, val))
            kfl.append((graphn, branch, turn, tick))
            kfs.add((branch, turn, tick))
            if branch not in kfd:
                kfd[branch] = {
                    turn: {
                        tick,
                    }
                }
            elif turn not in kfd[branch]:
                kfd[branch][turn] = {
                    tick,
                }
            else:
                kfd[branch][turn].add(tick)

    def _load_at(self, branch, turn, tick):
        snap_keyframe = self._snap_keyframe
        latest_past_keyframe = None
        earliest_future_keyframe = None
        branch_now, turn_now, tick_now = branch, turn, tick
        branch_parents = self._branch_parents
        for (branch, turn, tick) in \
                    self._keyframes_times:
            # Figure out the latest keyframe that is earlier than the present moment,
            # and the earliest keyframe that is later than the present moment,
            # for each graph.
            # Can I avoid iterating over the entire keyframes table, somehow?
            if branch == branch_now:
                if turn < turn_now:
                    if latest_past_keyframe:
                        late_branch, late_turn, late_tick = latest_past_keyframe
                        if late_branch != branch or late_turn < turn or (
                                late_turn == turn and late_tick < tick):
                            latest_past_keyframe = (branch, turn, tick)
                    else:
                        latest_past_keyframe = (branch, turn, tick)
                elif turn > turn_now:
                    if earliest_future_keyframe:
                        early_branch, early_turn, early_tick = earliest_future_keyframe
                        if early_branch != branch or early_turn > turn or (
                                early_turn == turn and early_tick > tick):
                            earliest_future_keyframe = (branch, turn, tick)
                    else:
                        earliest_future_keyframe = (branch, turn, tick)
                elif tick < tick_now:
                    if latest_past_keyframe:
                        late_branch, late_turn, late_tick = latest_past_keyframe
                        if late_branch != branch or late_turn < turn or (
                                late_turn == turn and late_tick < tick):
                            latest_past_keyframe = (branch, turn, tick)
                    else:
                        latest_past_keyframe = (branch, turn, tick)
                elif tick > tick_now:
                    if earliest_future_keyframe:
                        early_branch, early_turn, early_tick = earliest_future_keyframe
                        if early_branch != branch or early_turn > turn or (
                                early_turn == turn and early_tick > tick):
                            earliest_future_keyframe = (branch, turn, tick)
                    else:
                        earliest_future_keyframe = (branch, turn, tick)
                else:
                    latest_past_keyframe = (branch, turn, tick)
            elif branch in branch_parents[branch_now]:
                if latest_past_keyframe:
                    late_branch, late_turn, late_tick = latest_past_keyframe
                    if branch == late_branch:
                        if turn > late_turn or (turn == late_turn
                                                and tick > late_tick):
                            latest_past_keyframe = (branch, turn, tick)
                    elif late_branch in branch_parents[branch]:
                        latest_past_keyframe = (branch, turn, tick)
                else:
                    latest_past_keyframe = (branch, turn, tick)
            # If branch is a descendant of branch_now, don't load the keyframe there,
            # because then we'd potentially be loading keyframes from any number of
            # possible futures, and we're trying to be conservative about what we load.
            # If neither branch is an ancestor of the other, we can't use the keyframe
            # for this load.
        loaded = self._loaded
        if earliest_future_keyframe:
            kfb, kfr, kft = earliest_future_keyframe
            if kfb in loaded:
                early_turn, early_tick, late_turn, late_tick = loaded[kfb]
                if kfr > late_turn or (kfr == late_turn and kft > late_tick):
                    loaded[kfb] = early_turn, early_tick, kfr, kft
            elif kfb == branch_now:
                if kfr > turn_now or (kfr == turn_now and kft > tick_now):
                    loaded[kfb] = (turn_now, tick_now, kfr, kft)
            else:
                loaded[kfb] = (kfr, kft, kfr, kft)
        if latest_past_keyframe:
            kfb, kfr, kft = latest_past_keyframe
            if kfb in loaded:
                early_turn, early_tick, late_turn, late_tick = loaded[kfb]
                if kfr < early_turn or (kfr == early_turn
                                        and kft < early_tick):
                    loaded[kfb] = kfr, kft, late_turn, late_tick
            elif kfb == branch_now:
                if kfr < turn_now or (kfr == turn_now and kft < tick_now):
                    loaded[kfb] = (kfr, kft, turn_now, tick_now)
            else:
                loaded[kfb] = (kfr, kft, kfr, kft)
        if branch_now in loaded:
            early_turn, early_tick, late_turn, late_tick = loaded[branch_now]
            if turn_now < early_turn or (turn_now == early_turn
                                         and tick_now < early_tick):
                early_turn, early_tick = turn_now, tick_now
            elif turn_now > late_turn or (turn_now == late_turn
                                          and tick_now > late_tick):
                late_turn, late_tick = turn_now, tick_now
            loaded[branch_now] = early_turn, early_tick, late_turn, late_tick
        else:
            loaded[branch_now] = turn_now, tick_now, turn_now, tick_now
        noderows = []
        edgerows = []
        graphvalrows = []
        nodevalrows = []
        edgevalrows = []
        load_nodes = self.query.load_nodes
        load_edges = self.query.load_edges
        load_graph_val = self.query.load_graph_val
        load_node_val = self.query.load_node_val
        load_edge_val = self.query.load_edge_val
        get_keyframe = self.query.get_keyframe
        iter_parent_btt = self._iter_parent_btt
        if latest_past_keyframe is None:  # happens in very short games

            def updload(branch, turn, tick):
                (early_turn, early_tick, late_turn, late_tick) = loaded[branch]
                if turn < early_turn or (turn == early_turn
                                         and tick < early_tick):
                    (early_turn, early_tick) = (turn, tick)
                if turn > late_turn or (turn == late_turn
                                        and tick > late_tick):
                    (late_turn, late_tick) = (turn, tick)
                loaded[branch] = (early_turn, early_tick, late_turn, late_tick)

            for (graph, node, branch, turn, tick,
                 ex) in self.query.nodes_dump():
                updload(branch, turn, tick)
                noderows.append((graph, node, branch, turn, tick, ex or None))
            for (graph, orig, dest, idx, branch, turn, tick,
                 ex) in self.query.edges_dump():
                updload(branch, turn, tick)
                edgerows.append((graph, orig, dest, idx, branch, turn, tick, ex
                                 or None))
            for row in self.query.graph_val_dump():
                updload(*row[2:5])
                graphvalrows.append(row)
            for row in self.query.node_val_dump():
                updload(*row[3:6])
                nodevalrows.append(row)
            for row in self.query.edge_val_dump():
                updload(*row[5:8])
                edgevalrows.append(row)
            with self.batch():
                self._nodes_cache.load(noderows)
                self._edges_cache.load(edgerows)
                self._graph_val_cache.load(graphvalrows)
                self._node_val_cache.load(nodevalrows)
                self._edge_val_cache.load(edgevalrows)
            return None, None, \
                    {}, noderows, edgerows, graphvalrows, \
                    nodevalrows, edgevalrows
        past_branch, past_turn, past_tick = latest_past_keyframe
        keyframed = {}
        for graph in self.graph:
            stuff = keyframed[graph] = get_keyframe(graph, past_branch,
                                                    past_turn, past_tick)
            if stuff is None:
                continue
            nodes, edges, graph_val = stuff
            snap_keyframe(graph, past_branch, past_turn, past_tick, nodes,
                          edges, graph_val)
            if earliest_future_keyframe is None:
                start_turn, start_tick, end_turn, end_tick = loaded.get(
                    branch, (turn_now, tick_now, turn_now, tick_now))
                if past_turn < start_turn or (past_turn == start_turn
                                              and past_tick < start_tick):
                    (start_turn, start_tick) = (past_turn, past_tick)
                for (graph, node, branch, turn, tick,
                     ex) in load_nodes(graph, past_branch, past_turn,
                                       past_tick):
                    noderows.append((graph, node, branch, turn, tick, ex
                                     or None))
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for (graph, orig, dest, idx, branch, turn, tick,
                     ex) in load_edges(graph, past_branch, past_turn,
                                       past_tick):
                    edgerows.append(
                        (graph, orig, dest, idx, branch, turn, tick, ex
                         or None))
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for row in load_graph_val(graph, past_branch, past_turn,
                                          past_tick):
                    graphvalrows.append(row)
                    turn = row[3]
                    tick = row[4]
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for row in load_node_val(graph, past_branch, past_turn,
                                         past_tick):
                    nodevalrows.append(row)
                    turn = row[4]
                    tick = row[5]
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for row in load_edge_val(graph, past_branch, past_turn,
                                         past_tick):
                    edgevalrows.append(row)
                    turn = row[6]
                    tick = row[7]
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                (start_turn0, start_tick0, end_turn0, end_tick0) = loaded.get(
                    branch, (turn_now, tick_now, turn_now, tick_now))
                if start_turn < start_turn0 or (start_turn == start_turn0
                                                and start_tick < start_tick0):
                    (start_turn1, start_tick1) = (start_turn, start_tick)
                else:
                    (start_turn1, start_tick1) = (start_turn0, start_tick0)
                if end_turn > end_turn0 or (end_turn == end_turn0
                                            and end_tick > end_tick0):
                    (end_turn1, end_tick1) = (end_turn, end_tick)
                else:
                    (end_turn1, end_tick1) = (end_turn0, end_tick0)
                loaded[branch] = (start_turn1, start_tick1, end_turn1,
                                  end_tick1)
                continue
            future_branch, future_turn, future_tick = earliest_future_keyframe
            if past_branch == future_branch:
                for (graph, node, branch, turn, tick,
                     ex) in load_nodes(graph, past_branch, past_turn,
                                       past_tick, future_turn, future_tick):
                    noderows.append((graph, node, branch, turn, tick, ex
                                     or None))
                for (graph, orig, dest, idx, branch, turn, tick,
                     ex) in load_edges(graph, past_branch, past_turn,
                                       past_tick, future_turn, future_tick):
                    edgerows.append(
                        (graph, orig, dest, idx, branch, turn, tick, ex
                         or None))
                graphvalrows.extend(
                    load_graph_val(graph, past_branch, past_turn, past_tick,
                                   future_turn, future_tick))
                nodevalrows.extend(
                    load_node_val(graph, past_branch, past_turn, past_tick,
                                  future_turn, future_tick))
                edgevalrows.extend(
                    load_edge_val(graph, past_branch, past_turn, past_tick,
                                  future_turn, future_tick))
                if branch in loaded:
                    early_turn, early_tick, late_turn, late_tick = loaded[
                        branch]
                    if past_turn < early_turn or (past_turn == early_turn
                                                  and past_tick < early_tick):
                        early_turn, early_tick = past_turn, past_tick
                    if future_turn > late_turn or (future_turn == late_turn and
                                                   future_tick > late_tick):
                        late_turn, late_tick = future_turn, future_tick
                    loaded[branch] = (early_turn, early_tick, late_turn,
                                      late_tick)
                else:
                    loaded[branch] = (past_turn, past_tick, future_turn,
                                      future_tick)
                continue
            parentage_iter = iter_parent_btt(future_branch, future_turn,
                                             future_tick)
            branch1, turn1, tick1 = next(parentage_iter)
            windows = []
            for branch0, turn0, tick0 in parentage_iter:
                windows.append((branch1, turn0, tick0, turn1, tick1))
                if branch0 == past_branch:
                    windows.append(
                        (branch0, past_turn, past_tick, turn0, tick0))
                    break
            else:
                assert branch0 == past_branch, "Invalid branch heredity"
            if not windows:
                continue  # I think this would happen when we are only loading an initial state
            for window in reversed(windows):  # chronological ordering
                start_turn, start_tick, end_turn, end_tick = loaded[branch]
                for (graph, node, branch, turn, tick,
                     ex) in load_nodes(graph, *window):
                    noderows.append((graph, node, branch, turn, tick, ex
                                     or None))
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for (graph, orig, dest, idx, branch, turn, tick,
                     ex) in load_edges(graph, *window):
                    edgerows.append(
                        (graph, orig, dest, idx, branch, turn, tick, ex
                         or None))
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for row in load_graph_val(graph, *window):
                    graphvalrows.append(row)
                    turn = row[3]
                    tick = row[4]
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for row in load_node_val(graph, *window):
                    nodevalrows.append(row)
                    turn = row[4]
                    tick = row[5]
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                for row in load_edge_val(graph, *window):
                    edgevalrows.append(row)
                    turn = row[6]
                    tick = row[7]
                    if turn > end_turn:
                        (end_turn, end_tick) = (turn, tick)
                    elif turn == end_turn and tick > end_tick:
                        end_tick = tick
                    if turn < start_turn:
                        (start_turn, start_tick) = (turn, tick)
                    elif turn == start_turn and tick < start_tick:
                        start_tick = tick
                loaded[branch] = (start_turn, start_tick, end_turn, end_tick)
        with self.batch():
            self._nodes_cache.load(noderows)
            self._edges_cache.load(edgerows)
            self._graph_val_cache.load(graphvalrows)
            self._node_val_cache.load(nodevalrows)
            self._edge_val_cache.load(edgevalrows)
        return latest_past_keyframe, earliest_future_keyframe, \
               keyframed, noderows, edgerows, graphvalrows, \
               nodevalrows, edgevalrows

    def unload(self):
        """Remove everything from memory we can"""
        # find the slices of time that need to stay loaded
        branch, turn, tick = self._btt()
        iter_parent_btt = self._iter_parent_btt
        kfd = self._keyframes_dict
        if not kfd:
            return
        loaded = self._loaded
        to_keep = {}
        # Find a path to the latest past keyframe we can use. Keep things
        # loaded from there to here.
        for past_branch, past_turn, past_tick in iter_parent_btt(
                branch, turn, tick):
            if past_branch not in loaded:
                continue  # nothing happened in this branch i guess
            early_turn, early_tick, late_turn, late_tick = loaded[past_branch]
            if past_branch in kfd:
                for kfturn, kfticks in kfd[past_branch].items():
                    # this can't possibly perform very well.
                    # Maybe I need another loadedness dict that gives the two
                    # keyframes I am between and gets upkept upon time travel
                    for kftick in kfticks:
                        if loaded_keep_test(kfturn, kftick, early_turn,
                                            early_tick, late_turn, late_tick):
                            if (kfturn < turn or
                                (kfturn == turn and kftick < tick)) and (
                                    kfturn > early_turn or
                                    (kfturn == early_turn
                                     and kftick > early_tick)):
                                early_turn, early_tick = kfturn, kftick
                            elif (kfturn > turn or
                                  (kfturn == turn and kftick >= tick)) and (
                                      kfturn < late_turn or
                                      (kfturn == late_turn
                                       and kftick < late_tick)):
                                late_turn, late_tick = kfturn, kftick
                assert loaded_keep_test(
                    past_turn, past_tick, early_turn, early_tick, late_turn,
                    late_tick
                ), "Unloading failed due to an invalid cache state"
                to_keep[
                    past_branch] = early_turn, early_tick, past_turn, past_tick
                break
            else:
                to_keep[
                    past_branch] = early_turn, early_tick, late_turn, late_tick
        if not to_keep:
            # unloading literally everything would make the game unplayable,
            # so don't
            if hasattr(self, 'warning'):
                self.warning("Not unloading, due to lack of keyframes")
            return
        caches = self._caches
        for past_branch, (early_turn, early_tick, late_turn,
                          late_tick) in to_keep.items():
            for cache in caches:
                cache.truncate(past_branch, early_turn, early_tick, 'backward')
                cache.truncate(past_branch, late_turn, late_tick, 'forward')
                for graph, branches in cache.keyframe.items():
                    turns = branches[past_branch]
                    turns.truncate(late_turn, 'forward')
                    try:
                        late = turns[late_turn]
                    except HistoryError:
                        pass
                    else:
                        late.truncate(late_tick, 'forward')
                    turns.truncate(early_turn, 'backward')
                    try:
                        early = turns[early_turn]
                    except HistoryError:
                        pass
                    else:
                        early.truncate(early_tick, 'backward')
        loaded.update(to_keep)
        for branch in set(loaded).difference(to_keep):
            for cache in caches:
                cache.remove_branch(branch)
            del loaded[branch]

    def _time_is_loaded(self, branch, turn=None, tick=None):
        loaded = self._loaded
        if branch not in loaded:
            return False
        if turn is None:
            return True
        if tick is not None:
            return loaded_keep_test(turn, tick, *loaded[branch])
        (past_turn, past_tick, future_turn, future_tick) = loaded[branch]
        return past_turn <= turn <= future_turn

    def __enter__(self):
        """Enable the use of the ``with`` keyword"""
        return self

    def __exit__(self, *args):
        """Alias for ``close``"""
        self.close()

    def is_parent_of(self, parent, child):
        """Return whether ``child`` is a branch descended from ``parent`` at
        any remove.

        """
        if parent == 'trunk':
            return True
        if child == 'trunk':
            return False
        if child not in self._branches:
            raise ValueError(
                "The branch {} seems not to have ever been created".format(
                    child))
        if self._branches[child][0] == parent:
            return True
        return self.is_parent_of(parent, self._branches[child][0])

    def _get_branch(self):
        return self._obranch

    def _set_branch(self, v):
        if self._planning:
            raise ValueError("Don't change branches while planning")
        curbranch, curturn, curtick = self._btt()
        if curbranch == v:
            self._otick = self._turn_end_plan[curbranch, curturn]
            return
        # make sure I'll end up within the revision range of the
        # destination branch
        if v != 'trunk' and v in self._branches:
            parturn = self._branches[v][1]
            if curturn < parturn:
                raise ValueError(
                    "Tried to jump to branch {br}, "
                    "which starts at turn {rv}. "
                    "Go to turn {rv} or later to use this branch.".format(
                        br=v, rv=parturn))
        branch_is_new = v not in self._branches
        if branch_is_new:
            # assumes the present turn in the parent branch has
            # been finalized.
            self.query.new_branch(v, curbranch, curturn, curtick)
            self._branches[v] = curbranch, curturn, curtick, curturn, curtick
            self._upd_branch_parentage(v, curbranch)
            self._turn_end_plan[v, curturn] = self._turn_end[v,
                                                             curturn] = curtick
        self._obranch = v
        self._otick = tick = self._turn_end_plan[v, curturn]
        loaded = self._loaded
        if branch_is_new:
            self._copy_plans(curbranch, curturn, curtick)
            loaded[v] = (curturn, tick, curturn, tick)
            return
        elif v not in loaded:
            self._load_at(v, curturn, tick)
            return
        (start_turn, start_tick, end_turn, end_tick) = loaded[v]
        if (curturn > end_turn or
            (curturn == end_turn and tick > end_tick)) or (
                curturn < start_turn or
                (curturn == start_turn and tick < start_tick)):
            self._load_at(v, curturn, tick)

    def _copy_plans(self, branch_from, turn_from, tick_from):
        """Collect all plans that are active at the given time and copy them to the current branch"""
        plan_ticks = self._plan_ticks
        plan_ticks_uncommitted = self._plan_ticks_uncommitted
        time_plan = self._time_plan
        plans = self._plans
        branch = self.branch
        where_cached = self._where_cached
        last_plan = self._last_plan
        turn_end_plan = self._turn_end_plan
        for plan_id in self._branches_plans[branch_from]:
            _, start_turn, start_tick = plans[plan_id]
            if start_turn > turn_from or (start_turn == turn_from
                                          and start_tick > tick_from):
                continue
            incremented = False
            for turn, ticks in list(plan_ticks[plan_id].items()):
                if turn < turn_from:
                    continue
                for tick in ticks:
                    if turn == turn_from and tick < tick_from:
                        continue
                    if not incremented:
                        self._last_plan = last_plan = last_plan + 1
                        incremented = True
                        plans[last_plan] = branch, turn, tick
                    for cache in where_cached[branch_from, turn, tick]:
                        data = cache.settings[branch_from][turn][tick]
                        value = data[-1]
                        key = data[:-1]
                        args = key + (branch, turn, tick, value)
                        if hasattr(cache, 'setdb'):
                            cache.setdb(*args)
                        cache.store(*args, planning=True)
                        plan_ticks[last_plan][turn].append(tick)
                        plan_ticks_uncommitted.append((last_plan, turn, tick))
                        time_plan[branch, turn, tick] = last_plan
                        turn_end_plan[branch, turn] = tick

    def delete_plan(self, plan):
        """Delete the portion of a plan that has yet to occur.

        :arg plan: integer ID of a plan, as given by ``with self.plan() as plan:``

        """
        branch, turn, tick = self._btt()
        to_delete = []
        plan_ticks = self._plan_ticks[plan]
        for trn, tcks in plan_ticks.items(
        ):  # might improve performance to use a WindowDict for plan_ticks
            if turn == trn:
                for tck in tcks:
                    if tck >= tick:
                        to_delete.append((trn, tck))
            elif trn > turn:
                to_delete.extend((trn, tck) for tck in tcks)
        # Delete stuff that happened at contradicted times, and then delete the times from the plan
        where_cached = self._where_cached
        time_plan = self._time_plan
        for trn, tck in to_delete:
            for cache in where_cached[branch, trn, tck]:
                cache.remove(branch, trn, tck)
                if hasattr(cache, 'deldb'):
                    cache.deldb(branch, trn, tck)
            del where_cached[branch, trn, tck]
            plan_ticks[trn].remove(tck)
            if not plan_ticks[trn]:
                del plan_ticks[trn]
            del time_plan[branch, trn, tck]

    # easier to override things this way
    @property
    def branch(self):
        return self._get_branch()

    @branch.setter
    def branch(self, v):
        self._set_branch(v)

    def _get_turn(self):
        return self._oturn

    def _set_turn(self, v):
        branch = self.branch
        loaded = self._loaded
        if v == self.turn:
            self._otick = tick = self._turn_end_plan[tuple(self.time)]
            if branch not in loaded:
                loaded[branch] = (v, tick, v, tick)
                return
            (start_turn, start_tick, end_turn, end_tick) = loaded[branch]
            if v > end_turn or (v == end_turn and tick > end_tick):
                if (branch, v, tick) in self._keyframes_times:
                    self._load_at(branch, v, tick)
                else:
                    loaded[branch] = (start_turn, start_tick, end_turn, tick)
            return
        if not isinstance(v, int):
            raise TypeError("turn must be an integer")
        # enforce the arrow of time, if it's in effect
        if self._forward and v < self._oturn:
            raise ValueError("Can't time travel backward in a forward context")

        # first make sure the cursor is not before the start of this branch
        if branch != 'trunk':
            parent, turn_start, tick_start, turn_end, tick_end = self._branches[
                branch]
            if v < turn_start:
                raise ValueError("The turn number {} "
                                 "occurs before the start of "
                                 "the branch {}".format(v, branch))
        if branch not in loaded:
            if (branch, v) in self._turn_end_plan:
                tick = self._turn_end_plan[branch, v]
            else:
                tick = 0
            self._load_at(branch, v, tick)
        else:
            (start_turn, start_tick, end_turn, end_tick) = loaded[branch]
            if (branch, v) in self._turn_end_plan:
                tick = self._turn_end_plan[(branch, v)]
            else:
                self._turn_end_plan[(branch, v)] = tick = 0
            if v > end_turn or (v == end_turn and tick > end_tick):
                if (branch, v, tick) in self._keyframes_times:
                    self._load_at(branch, v, tick)
                else:
                    loaded[branch] = (start_turn, start_tick, v, tick)
            elif v < start_turn or (v == start_turn and tick < start_tick):
                self._load_at(branch, v, tick)
        self._otick = tick
        self._oturn = v

    # easier to override things this way
    @property
    def turn(self):
        return self._get_turn()

    @turn.setter
    def turn(self, v):
        self._set_turn(v)

    def _get_tick(self):
        return self._otick

    def _set_tick(self, v):
        if not isinstance(v, int):
            raise TypeError("tick must be an integer")
        time = branch, turn = self._obranch, self._oturn
        # enforce the arrow of time, if it's in effect
        if self._forward and v < self._otick:
            raise ValueError("Can't time travel backward in a forward context")
        if v > self._turn_end_plan[time]:  # TODO: only mutate after load
            self._turn_end_plan[time] = v
        if not self._planning:
            if v > self._turn_end[time]:
                self._turn_end[time] = v
            parent, turn_start, tick_start, turn_end, tick_end = self._branches[
                branch]
            if turn == turn_end and v > tick_end:
                self._branches[
                    branch] = parent, turn_start, tick_start, turn, v
        self._otick = v
        loaded = self._loaded
        if branch not in loaded:
            self._load_at(branch, turn, v)
            return
        (start_turn, start_tick, end_turn, end_tick) = loaded[branch]
        if turn > end_turn or (turn == end_turn and v > end_tick):
            if (branch, end_turn, end_tick) in self._keyframes_times:
                self._load_at(branch, turn, v)
                return
            loaded[branch] = (start_turn, start_tick, turn, v)
        elif turn < start_turn or (turn == start_turn and v < start_tick):
            self._load_at(branch, turn, v)

    # easier to override things this way
    @property
    def tick(self):
        return self._get_tick()

    @tick.setter
    def tick(self, v):
        self._set_tick(v)

    def _btt(self):
        """Return the branch, turn, and tick."""
        return self._obranch, self._oturn, self._otick

    def _nbtt(self):
        """Increment the tick and return branch, turn, tick

        Unless we're viewing the past, in which case raise HistoryError.

        Idea is you use this when you want to advance time, which you
        can only do once per branch, turn, tick.

        """
        (btt, turn_end_plan, turn_end, plan_ticks, plan_ticks_uncommitted,
         time_plan, branches) = self._nbtt_stuff
        branch, turn, tick = btt()
        branch_turn = (branch, turn)
        tick += 1
        if branch_turn in turn_end_plan and \
                tick <= turn_end_plan[branch_turn]:
            tick = turn_end_plan[branch_turn] + 1
        if turn_end[branch_turn] > tick:
            raise HistoryError(
                "You're not at the end of turn {}. Go to tick {} to change things"
                .format(turn, turn_end[branch_turn]))
        parent, turn_start, tick_start, turn_end, tick_end = branches[branch]
        if turn < turn_end:
            # There used to be a check for turn == turn_end and tick < tick_end
            # but I couldn't come up with a situation where that would actually
            # happen
            raise HistoryError(
                "You're in the past. Go to turn {}, tick {} to change things".
                format(turn_end, tick_end))
        if self._planning:
            last_plan = self._last_plan
            if (turn, tick) in plan_ticks[last_plan]:
                raise HistoryError(
                    "Trying to make a plan at {}, but that time already happened"
                    .format((branch, turn, tick)))
            plan_ticks[last_plan][turn].append(tick)
            plan_ticks_uncommitted.append((last_plan, turn, tick))
            time_plan[branch, turn, tick] = last_plan
        turn_end_plan[branch_turn] = tick
        branches[branch] = parent, turn_start, tick_start, turn_end, tick
        loaded = self._loaded
        if branch in loaded:
            (early_turn, early_tick, late_turn, late_tick) = loaded[branch]
            if turn > late_turn:
                (late_turn, late_tick) = (turn, tick)
            elif turn == late_turn and tick > late_tick:
                late_tick = tick
            loaded[branch] = (early_turn, early_tick, late_turn, late_tick)
        else:
            loaded[branch] = (turn, tick, turn, tick)
        self._otick = tick
        return branch, turn, tick

    def commit(self):
        """Write the state of all graphs to the database and commit the transaction.

        Also saves the current branch, turn, and tick.

        """
        self.query.globl['branch'] = self._obranch
        self.query.globl['turn'] = self._oturn
        self.query.globl['tick'] = self._otick
        set_branch = self.query.set_branch
        for branch, (parent, turn_start, tick_start, turn_end,
                     tick_end) in self._branches.items():
            set_branch(branch, parent, turn_start, tick_start, turn_end,
                       tick_end)
        turn_end = self._turn_end
        set_turn = self.query.set_turn
        for (branch, turn), plan_end_tick in self._turn_end_plan.items():
            set_turn(branch, turn, turn_end[branch], plan_end_tick)
        if self._plans_uncommitted:
            self.query.plans_insert_many(self._plans_uncommitted)
        if self._plan_ticks_uncommitted:
            self.query.plan_ticks_insert_many(self._plan_ticks_uncommitted)
        if self._new_keyframes:
            self.query.keyframes_insert_many(self._new_keyframes)
            self._new_keyframes = []
        self.query.commit()
        self._plans_uncommitted = []
        self._plan_ticks_uncommitted = []

    def close(self):
        """Write changes to database and close the connection"""
        self.commit()
        self.query.close()

    def _nudge_loaded(self, branch, turn, tick):
        loaded = self._loaded
        if branch in loaded:
            past_turn, past_tick, future_turn, future_tick = loaded[branch]
            if turn < past_turn or (turn == past_turn and tick < past_tick):
                loaded[branch] = turn, tick, future_turn, future_tick
            elif turn > future_turn or (turn == future_turn
                                        and tick > future_tick):
                loaded[branch] = past_turn, past_tick, turn, tick
        else:
            loaded[branch] = turn, tick, turn, tick

    def _init_graph(self, name, type_s='DiGraph', data=None):
        if self.query.have_graph(name):
            raise GraphNameError("Already have a graph by that name")
        if name in self.illegal_graph_names:
            raise GraphNameError("Illegal name")
        self.query.new_graph(name, type_s)
        branch, turn, tick = self._btt()
        self._nudge_loaded(branch, turn, tick)
        if data:
            if isinstance(data, DiGraph):
                nodes = data._nodes_state()
                edges = data._edges_state()
                val = data._val_state()
                self._snap_keyframe(name, branch, turn, tick, nodes, edges,
                                    val)
                self._new_keyframes.append(
                    (name, branch, turn, tick, nodes, edges, val))
            elif isinstance(data, nx.Graph):
                self._snap_keyframe(name, branch, turn, tick, data._node,
                                    data._adj, data.graph)
                self._new_keyframes.append((name, branch, turn, tick,
                                            data._node, data._adj, data.graph))
            elif isinstance(data, dict):
                try:
                    data = nx.from_dict_of_dicts(data)
                except AttributeError:
                    data = nx.from_dict_of_lists(data)
                self._snap_keyframe(name, branch, turn, tick, data._node,
                                    data._adj, data.graph)
                self._new_keyframes.append((name, branch, turn, tick,
                                            data._node, data._adj, data.graph))
            else:
                if len(data) != 3 or not all(
                        isinstance(d, dict) for d in data):
                    raise ValueError("Invalid graph data")
                self._snap_keyframe(name, branch, turn, tick, *data)
                self._new_keyframes.append((name, branch, turn, tick) +
                                           tuple(data))
            graphmap = self.graph
            others = set(graphmap)
            others.discard(name)
            branch, turn, tick = self._btt()
            snapp = self._snap_keyframe
            kfl = self._keyframes_list
            kfd = self._keyframes_dict
            kfs = self._keyframes_times
            nkfs = self._new_keyframes
            already_keyframed = {nkf[:4] for nkf in self._new_keyframes}
            for graphn in others:
                if (graphn, branch, turn, tick) in already_keyframed:
                    continue
                graph = graphmap[graphn]
                nodes = graph._nodes_state()
                edges = graph._edges_state()
                val = graph._val_state()
                snapp(graphn, branch, turn, tick, nodes, edges, val)
                nkfs.append((graphn, branch, turn, tick, nodes, edges, val))
                kfl.append((graphn, branch, turn, tick))
                kfs.add((branch, turn, tick))
                if branch not in kfd:
                    kfd[branch] = {
                        turn: {
                            tick,
                        }
                    }
                elif turn not in kfd[branch]:
                    kfd[branch][turn] = {
                        tick,
                    }
                else:
                    kfd[branch][turn].add(tick)

    def new_graph(self, name, data=None, **attr):
        """Return a new instance of type Graph, initialized with the given
        data if provided.

        :arg name: a name for the graph
        :arg data: dictionary or NetworkX graph object providing initial state

        """
        raise NotImplementedError("Only DiGraph for now")

    def new_digraph(self, name, data=None, **attr):
        """Return a new instance of type DiGraph, initialized with the given
        data if provided.

        :arg name: a name for the graph
        :arg data: dictionary or NetworkX graph object providing initial state

        """
        if data and isinstance(data, nx.Graph):
            if not data.is_directed():
                data = nx.to_directed(data)
            self._init_graph(name, 'DiGraph',
                             [data._node, data._succ, data.graph])
        else:
            self._init_graph(name, 'DiGraph', data)
        return DiGraph(self, name)

    def new_multigraph(self, name, data=None, **attr):
        """Return a new instance of type MultiGraph, initialized with the given
        data if provided.

        :arg name: a name for the graph
        :arg data: dictionary or NetworkX graph object providing initial state

        """
        raise NotImplementedError("Only DiGraph for now")

    def new_multidigraph(self, name, data=None, **attr):
        """Return a new instance of type MultiDiGraph, initialized with the given
        data if provided.

        :arg name: a name for the graph
        :arg data: dictionary or NetworkX graph object providing initial state

        """
        raise NotImplementedError("Only DiGraph for now")

    def get_graph(self, name):
        """Return a graph previously created with ``new_graph``,
        ``new_digraph``, ``new_multigraph``, or
        ``new_multidigraph``

        :arg name: name of an existing graph

        """
        return self._graph_objs[name]

    def del_graph(self, name):
        """Remove all traces of a graph's existence from the database

        :arg name: name of an existing graph

        """
        # make sure the graph exists before deleting anything
        self.get_graph(name)
        self.query.del_graph(name)
        if name in self._graph_objs:
            del self._graph_objs[name]

    def _iter_parent_btt(self,
                         branch=None,
                         turn=None,
                         tick=None,
                         *,
                         stoptime=None):
        """Private use. Iterate over (branch, turn, tick), where the branch is
        a descendant of the previous (starting with whatever branch is
        presently active and ending at 'trunk'), and the turn is the
        latest revision in the branch that matters.

        :arg stoptime: This may be a branch, in which case iteration will stop
        instead of proceeding into that branch's parent; or it may be a triple,
        ``(branch, turn, tick)``, in which case iteration will stop instead of
        yielding any time before that. The tick may be ``None``, in which case
        iteration will stop instead of yielding the turn.

        """
        branch = branch or self.branch
        trn = self.turn if turn is None else turn
        tck = self.tick if tick is None else tick
        yield branch, trn, tck
        stopbranches = set()
        if stoptime:
            if type(stoptime) is tuple:
                stopbranch = stoptime[0]
                stopbranches.add(stopbranch)
                stopbranches.update(self._branch_parents[stopbranch])
            else:
                stopbranch = stoptime
                stopbranches = self._branch_parents[stopbranch]
        _branches = self._branches
        while branch in _branches:
            # ``par`` is the parent branch;
            # ``(trn, tck)`` is when ``branch`` forked off from ``par``
            (branch, trn, tck, _, _) = _branches[branch]
            if branch is None:
                return
            if branch in stopbranches and (
                    trn < stoptime[1] or
                (trn == stoptime[1] and
                 (stoptime[2] is None or tck <= stoptime[2]))):
                return
            yield branch, trn, tck

    def _branch_descendants(self, branch=None):
        """Iterate over all branches immediately descended from the current
        one (or the given one, if available).

        """
        branch = branch or self.branch
        for (parent, (child, _, _, _, _)) in self._branches.items():
            if parent == branch:
                yield child

    def _node_exists(self, character, node):
        retrieve, btt = self._node_exists_stuff
        try:
            return retrieve(character, node, *btt()) is not None
        except KeyError:
            return False

    def _exist_node(self, character, node, exist=True):
        nbtt, exist_node, store = self._exist_node_stuff
        branch, turn, tick = nbtt()
        exist_node(character, node, branch, turn, tick, exist)
        store(character, node, branch, turn, tick, exist)

    def _edge_exists(self, character, orig, dest, idx=0):
        retrieve, btt = self._edge_exists_stuff
        try:
            return retrieve(character, orig, dest, idx, *btt()) is not None
        except KeyError:
            return False

    def _exist_edge(self, character, orig, dest, idx=0, exist=True):
        nbtt, exist_edge, store = self._exist_edge_stuff
        branch, turn, tick = nbtt()
        exist_edge(character, orig, dest, idx, branch, turn, tick, exist)
        store(character, orig, dest, idx, branch, turn, tick, exist)
