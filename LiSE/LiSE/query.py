# This file is part of LiSE, a framework for life simulation games.
# Copyright (c) Zachary Spector,  zacharyspector@gmail.com
"""The query engine provides Pythonic methods to access the database."""
from inspect import getsource
from types import FunctionType
from marshal import loads as unmarshalled
from marshal import dumps as marshalled
from operator import gt, lt, eq, ne, le, ge

import allegedb.query

from .exc import (
    IntegrityError,
    OperationalError,
    RedundantRuleError,
    UserFunctionError
)
from .util import EntityStatAccessor
import LiSE

string_defaults = {
    'strings': {'eng': [('README', 'Write release notes for your game here.')]}
}


def windows_union(windows):
    def fix_overlap(left, right):
        if left == right:
            return [left]
        assert left[0] < right[0]
        if left[1] >= right[0]:
            if right[1] > left[1]:
                return [(left[0], right[1])]
            else:
                return [left]
        return [left, right]

    if len(windows) == 1:
        yield windows[0]
        return
    none_left = []
    otherwise = []
    for window in windows:
        if window[0] is None:
            none_left.append(window)
        else:
            otherwise.append(window)

    res = []
    otherwise.sort()
    for window in none_left:
        if not res:
            res.append(window)
            continue
        res.extend(fix_overlap(res.pop(), window))
    while otherwise:
        window = otherwise.pop(0)
        if not res:
            res.append(window)
            continue
        res.extend(fix_overlap(res.pop(), window))
    return res


def windows_intersection(windows):
    """

    :rtype: list
    """

    def intersect2(left, right):
        if left == right:
            return left
        elif left is (None, None):
            return right
        elif right is (None, None):
            return left
        elif left[0] is None:
            if right[0] is None:
                return None, min((left[1], right[1]))
            elif right[1] is None:
                if left[1] <= right[0]:
                    return left[1], right[0]
                else:
                    return None
            elif right[0] <= left[1]:
                return right[0], left[1]
            else:
                return None
        elif left[1] is None:
            if right[0] is None:
                return left[0], right[1]
            else:
                return right  # assumes left[0] <= right[0]
        # None not in left
        elif right[0] is None:
            return left[0], min((left[1], right[1]))
        elif right[1] is None:
            if left[1] >= right[0]:
                return right[0], left[1]
            else:
                return None
        assert None not in left and None not in right and left[0] < right[1]
        if left[1] >= right[0]:
            if right[1] > left[1]:
                return right[0], left[1]
            else:
                return right
        return None

    if len(windows) == 1:
        return windows
    left_none = []
    otherwise = []
    for window in windows:
        if window[0] is None:
            left_none.append(window)
        else:
            otherwise.append(window)

    done = []
    todo = left_none + sorted(otherwise)
    for window in todo:
        if not done:
            done.append(window)
            continue
        res = intersect2(done.pop(), window)
        if res:
            done.append(res)
    return done


class Query(object):
    def __new__(cls, engine, leftside, rightside=None, **kwargs):
        if rightside is None:
            if not isinstance(leftside, cls):
                raise TypeError("You can't make a query with only one side")
            me = leftside
        else:
            me = super().__new__(cls)
            me.leftside = leftside
            me.rightside = rightside
        me.engine = engine
        me.windows = kwargs.get('windows', [])
        return me

    def __call__(self):
        raise NotImplementedError("Query is abstract")

    def __eq__(self, other):
        return EqQuery(self.engine, self, self.engine.entityfy(other))

    def __gt__(self, other):
        return GtQuery(self.engine, self, self.engine.entityfy(other))

    def __ge__(self, other):
        return GeQuery(self.engine, self, self.engine.entityfy(other))

    def __lt__(self, other):
        return LtQuery(self.engine, self, self.engine.entityfy(other))

    def __le__(self, other):
        return LeQuery(self.engine, self, self.engine.entityfy(other))

    def __ne__(self, other):
        return NeQuery(self.engine, self, self.engine.entityfy(other))

    def and_before(self, end):
        if self.windows:
            new_windows = windows_intersection(
                sorted(self.windows + [(None, end)])
            )
        else:
            new_windows = [(0, end)]
        return type(self)(self.leftside, self.rightside, windows=new_windows)
    before = and_before

    def or_before(self, end):
        if self.windows:
            new_windows = windows_union(self.windows + [(None, end)])
        else:
            new_windows = [(None, end)]
        return type(self)(self.leftside, self.rightside, windows=new_windows)

    def and_after(self, start):
        if self.windows:
            new_windows = windows_intersection(self.windows + [(start, None)])
        else:
            new_windows = [(start, None)]
        return type(self)(self.leftside, self.rightside, windows=new_windows)
    after = and_after

    def or_between(self, start, end):
        if self.windows:
            new_windows = windows_union(self.windows + [(start, end)])
        else:
            new_windows = [(start, end)]
        return type(self)(self.leftside, self.rightside, windows=new_windows)

    def and_between(self, start, end):
        if self.windows:
            new_windows = windows_intersection(self.windows + [(start, end)])
        else:
            new_windows = [(start, end)]
        return type(self)(self.leftside, self.rightside, windows=new_windows)
    between = and_between

    def or_during(self, tick):
        return self.or_between(tick, tick)

    def and_during(self, tick):
        return self.and_between(tick, tick)
    during = and_during


class Union(Query):
    pass


class ComparisonQuery(Query):
    oper = lambda x, y: NotImplemented

    def __call__(self):
        return QueryResults(iter_eval_cmp(self, self.oper, engine=self.engine))


class EqQuery(ComparisonQuery):
    oper = eq


class NeQuery(ComparisonQuery):
    oper = ne


class GtQuery(ComparisonQuery):
    oper = gt


class LtQuery(ComparisonQuery):
    oper = lt


class GeQuery(ComparisonQuery):
    oper = ge


class LeQuery(ComparisonQuery):
    oper = le


comparisons = {
    'eq': EqQuery,
    'ne': NeQuery,
    'gt': GtQuery,
    'lt': LtQuery,
    'ge': GeQuery,
    'le': LeQuery
}


class StatusAlias(EntityStatAccessor):
    def __eq__(self, other):
        return EqQuery(self.engine, self, other)

    def __ne__(self, other):
        return NeQuery(self.engine, self, other)

    def __gt__(self, other):
        return GtQuery(self.engine, self, other)

    def __lt__(self, other):
        return LtQuery(self.engine, self, other)

    def __ge__(self, other):
        return GeQuery(self.engine, self, other)

    def __le__(self, other):
        return LeQuery(self.engine, self, other)


def intersect_qry(qry):
    windows = []
    windowses = 0
    if hasattr(qry.leftside, 'windows'):
        windows.extend(qry.leftside.windows)
        windowses += 1
    if hasattr(qry.rightside, 'windows'):
        windows.extend(qry.rightside.windows)
        windowses += 1
    if windowses > 1:
        windows = windows_intersection(windowses)
    return windows


def iter_intersection_ticks2check(ticks, windows):
    windows = windows_intersection(windows)
    if not windows:
        yield from ticks
        return
    for tick in sorted(ticks):
        (left, right) = windows.pop(0)
        if left is None:
            if tick <= right:
                yield tick
                windows.insert(0, (left, right))
        elif right is None:
            if tick >= left:
                yield from ticks
                return
            windows.insert(0, (left, right))
        elif left <= tick <= right:
            yield tick
            windows.insert(0, (left, right))
        elif tick < left:
            windows.insert(0, (left, right))


class QueryResults(object):
    def __init__(self, iter):
        self.iter = iter
        try:
            self.next = next(self.iter)
        except StopIteration:
            return

    def __iter__(self):
        return self

    def __next__(self):
        try:
            r = self.next
        except AttributeError:
            raise StopIteration
        try:
            self.next = next(self.iter)
        except StopIteration:
            del self.next
        return r

    def __bool__(self):
        return hasattr(self, 'next')


def iter_eval_cmp(qry, oper, start_branch=None, engine=None):
    def mungeside(side):
        if isinstance(side, Query):
            return side()
        elif isinstance(side, StatusAlias):
            return EntityStatAccessor(
                side.entity, side.stat, side.engine,
                side.branch, side.tick, side.current, side.mungers
            )
        elif isinstance(side, EntityStatAccessor):
            return side
        else:
            return lambda b, t: side

    def getcache(side):
        if hasattr(side, 'cache'):
            return side.cache
        if hasattr(side, 'entity'):
            if side.stat in (
                    'location', 'next_location', 'locations',
                    'arrival_time', 'next_arrival_time'
            ):
                return engine._things_cache.branches[
                    (side.entity.character.name, side.entity.name)]
            if side.stat in side.entity._cache:
                return side.entity._cache[side.stat]

    leftside = mungeside(qry.leftside)
    rightside = mungeside(qry.rightside)
    windows = qry.windows or [(0, None)]
    engine = engine or leftside.engine or rightside.engine
    for (branch, _) in engine._active_branches(start_branch):
        try:
            lkeys = frozenset(getcache(leftside)[branch].keys())
        except AttributeError:
            lkeys = frozenset()
        try:
            rkeys = getcache(rightside)[branch].keys()
        except AttributeError:
            rkeys = frozenset()
        ticks = lkeys.union(rkeys)
        if ticks:
            yield from (
                (branch, tick) for tick in
                iter_intersection_ticks2check(ticks, windows)
                if oper(leftside(branch, tick), rightside(branch, tick))
            )
        else:
            yield from (
                (branch, tick) for tick in
                range(engine._branch_start.get(branch, 0), engine.tick+1)
                if oper(leftside(branch, tick), rightside(branch, tick))
            )


class QueryEngine(allegedb.query.QueryEngine):
    json_path = LiSE.__path__[0]
    IntegrityError = IntegrityError
    OperationalError = OperationalError

    def universal_get(self, key, branch, tick):
        return self.json_load(self.sql('universal_get', self.json_dump(key), branch, tick))

    def universal_set(self, key, branch, tick, val):
        key, val = map(self.json_dump, (key, val))
        try:
            self.sql('universal_ins', key, branch, tick, val)
        except IntegrityError:
            self.sql('universal_upd', val, key, branch, tick)

    def universal_del(self, key, branch, tick):
        key = self.json_dump(key)
        try:
            self.sql('universal_ins', key, branch, tick, None)
        except IntegrityError:
            self.sql('universal_upd', None, key, branch, tick)

    def comparison(
            self, entity0, stat0, entity1,
            stat1=None, oper='eq', windows=[]
    ):
        stat1 = stat1 or stat0
        return comparisons[oper](
            leftside=entity0.status(stat0),
            rightside=entity1.status(stat1),
            windows=windows
        )

    def count_all_table(self, tbl):
        return self.sql('{}_count'.format(tbl)).fetchone()[0]

    def init_table(self, tbl):
        try:
            return self.sql('create_{}'.format(tbl))
        except OperationalError:
            pass

    def index_table(self, tbl):
        try:
            return self.sql('index_{}'.format(tbl))
        except OperationalError:
            pass

    def rule_triggers(self, rule):
        rule = self.json_dump(rule)
        for row in self.sql('rule_triggers', rule):
            yield row[0]

    def rule_prereqs(self, rule):
        rule = self.json_dump(rule)
        for row in self.sql('rule_prereqs', rule):
            yield row[0]

    def rule_actions(self, rule):
        rule = self.json_dump(rule)
        for row in self.sql('rule_actions', rule):
            yield row[0]

    def string_table_lang_items(self, tbl, lang):
        return self.sql('{}_lang_items'.format(tbl), lang)

    def string_table_get(self, tbl, lang, key):
        for row in self.sql('{}_get'.format(tbl), lang, key):
            return row[0]

    def string_table_set(self, tbl, lang, key, value):
        try:
            self.sql('{}_ins'.format(tbl), key, lang, value)
        except IntegrityError:
            self.sql('{}_upd'.format(tbl), value, lang, key)

    def string_table_del(self, tbl, lang, key):
        self.sql('{}_del'.format(tbl), lang, key)

    def dump_universal(self):
        for key, branch, tick, date, creator, description, value in self.sql('universal_dump'):
            yield self.json_load(key), branch, tick, self.json_load(value)

    def characters(self):
        for (ch,) in self.sql('characters'):
            yield self.json_load(ch)

    def characters_rulebooks(self):
        for row in self.sql('characters_rulebooks'):
            yield map(self.json_load, row)

    def del_character(self, name):
        name = self.json_dump(name)
        self.sql('del_char_things', name)
        self.sql('del_char_avatars', name)
        for tbl in (
                "node_val",
                "edge_val",
                "edges",
                "nodes",
                "graph_val",
                "characters",
                "graph"
        ):
            self.sql('char_del_fmt', name, tbl=tbl)

    def rulebooks(self):
        for book in self.sql('rulebooks'):
            yield self.json_load(book)

    def node_rulebook(self, character, node):
        (character, node) = map(self.json_dump, (character, node))
        r = self.sql('node_rulebook', character, node).fetchone()
        if r is None:
            raise KeyError(
                'No rulebook for node {} in character {}'.format(
                    node, character
                )
            )
        return self.json_load(r[0])

    def nodes_rulebooks(self):
        for row in self.sql('nodes_rulebooks'):
            yield map(self.json_load, row)

    def set_node_rulebook(self, character, node, rulebook):
        (character, node, rulebook) = map(
            self.json_dump, (character, node, rulebook)
        )
        try:
            return self.sql('ins_node_rulebook', character, node, rulebook)
        except IntegrityError:
            return self.sql('upd_node_rulebook', rulebook, character, node)

    def portal_rulebook(self, character, orig, dest):
        (character, orig, dest) = map(
            self.json_dump, (character, orig, dest)
        )
        r = self.sql(
            'portal_rulebook',
            character,
            orig,
            dest,
            0
        ).fetchone()
        if r is None:
            raise KeyError(
                "No rulebook for portal {}->{} in character {}".format(
                    orig, dest, character
                )
            )
        return self.json_load(r[0])

    def portals_rulebooks(self):
        for row in self.sql('portals_rulebooks'):
            yield map(self.json_load, row)

    def set_portal_rulebook(self, character, orig, dest, rulebook):
        (character, orig, dest, rulebook) = map(
            self.json_dump, (character, orig, dest, rulebook)
        )
        try:
            return self.sql(
                'ins_portal_rulebook',
                character,
                orig,
                dest,
                0,
                rulebook
            )
        except IntegrityError:
            return self.sql(
                'upd_portal_rulebook',
                rulebook,
                character,
                orig,
                dest,
                0
            )

    def character_rulebook(self, character):
        character = self.json_dump(character)
        for (rb,) in self.sql('character_rulebook', character):
            return self.json_load(rb)

    def handled_rules_on_characters(self, typ):
        for (
                character,
                rulebook,
                rule,
                branch,
                tick
        ) in self.sql('handled_{}_rules'.format(typ)):
            yield (
                self.json_load(character),
                typ,
                self.json_load(rulebook),
                self.json_load(rule),
                branch,
                tick
            )

    def handled_character_rules(self):
        return self.handled_rules_on_characters('character')

    def handled_avatar_rules(self):
        return self.handled_rules_on_characters('avatar')

    def handled_character_thing_rules(self):
        return self.handled_rules_on_characters('character_thing')

    def handled_character_place_rules(self):
        return self.handled_rules_on_characters('character_place')

    def handled_character_node_rules(self):
        return self.handled_rules_on_characters('character_node')

    def handled_character_portal_rules(self):
        return self.handled_rules_on_characters('character_portal')

    def dump_node_rules_handled(self):
        for (
                character,
                node,
                rulebook,
                rule,
                branch,
                tick
        ) in self.sql("dump_node_rules_handled"):
            yield (
                self.json_load(character),
                self.json_load(node),
                self.json_load(rulebook),
                self.json_load(rule),
                branch,
                tick
            )

    def dump_portal_rules_handled(self):
        for (
                character,
                orig,
                dest,
                idx,
                rulebook,
                rule,
                branch,
                tick
        ) in self.sql('dump_portal_rules_handled'):
            yield (
                self.json_load(character),
                self.json_load(orig),
                self.json_load(dest),
                idx,
                self.json_load(rulebook),
                self.json_load(rule),
                branch,
                tick
            )

    def handled_character_rule(
            self, character, rulebook, rule, branch, tick
    ):
        (character, rulebook, rule) = map(
            self.json_dump, (character, rulebook, rule)
        )
        try:
            return self.sql(
                'handled_character_rule',
                character,
                rulebook,
                rule,
                branch,
                tick,
            )
        except IntegrityError:
            raise RedundantRuleError(
                "Already handled rule {rule} in rulebook {book} "
                "for character {ch} at tick {t} of branch {b}".format(
                    ch=character,
                    book=rulebook,
                    rule=rule,
                    b=branch,
                    t=tick
                )
            )

    def handled_thing_rule(
            self, character, thing, rulebook, rule, branch, tick
    ):
        (character, thing, rulebook, rule) = map(
            self.json_dump, (character, thing, rulebook, rule)
        )
        try:
            return self.sql(
                'handled_thing_rule',
                character,
                thing,
                rulebook,
                rule,
                branch,
                tick
            )
        except IntegrityError:
            raise RedundantRuleError(
                "Already handled rule {r} in rulebook {book} "
                "for thing {th} "
                "at tick {t} of branch {b}".format(
                    r=rule,
                    book=rulebook,
                    th=thing,
                    b=branch,
                    t=tick
                )
            )

    def handled_place_rule(
            self, character, place, rulebook, rule, branch, tick
    ):
        (character, place, rulebook, rule) = map(
            self.json_dump, (character, place, rulebook, rule)
        )
        try:
            return self.sql(
                'handled_place_rule',
                character,
                place,
                rulebook,
                rule,
                branch,
                tick
            )
        except IntegrityError:
            raise RedundantRuleError(
                "Already handled rule {rule} in rulebook {book} "
                "for place {place} at tick {tick} of branch {branch}".format(
                    place=place,
                    rulebook=rulebook,
                    rule=rule,
                    branch=branch,
                    tick=tick
                )
            )

    def handled_portal_rule(
            self, character, orig, dest, rulebook, rule, branch, tick
    ):
        (character, orig, dest, rulebook, rule) = map(
            self.json_dump, (character, orig, dest, rulebook, rule)
        )
        try:
            return self.sql(
                'handled_portal_rule',
                character,
                orig,
                dest,
                0,
                rulebook,
                rule,
                branch,
                tick
            )
        except IntegrityError:
            raise RedundantRuleError(
                "Already handled rule {rule} in rulebook {book} "
                "for portal from {orig} to {dest} "
                "at tick {tick} of branch {branch}".format(
                    orig=orig,
                    dest=dest,
                    book=rulebook,
                    rule=rule,
                    branch=branch,
                    tick=tick
                )
            )

    def get_rulebook_char(self, rulemap, character):
        character = self.json_dump(character)
        for (book,) in self.sql(
                'rulebook_get_{}'.format(rulemap), character
        ):
            return self.json_load(book)
        raise KeyError("No rulebook")

    def upd_rulebook_char(self, rulemap, character):
        return self.sql('upd_rulebook_char_fmt', character, rulemap=rulemap)

    def things_dump(self):
        for (
                character, thing, branch, tick, loc, nextloc
        ) in self.sql('things_dump'):
            yield (
                self.json_load(character),
                self.json_load(thing),
                branch,
                tick,
                self.json_load(loc),
                self.json_load(nextloc) if nextloc else None
            )

    def thing_loc_and_next_set(
            self, character, thing, branch, tick, loc, nextloc
    ):
        (character, thing) = map(
            self.json_dump,
            (character, thing)
        )
        loc = self.json_dump(loc) if loc else None
        nextloc = self.json_dump(nextloc) if nextloc else None
        try:
            return self.sql(
                'thing_loc_and_next_ins',
                character,
                thing,
                branch,
                tick,
                loc,
                nextloc
            )
        except IntegrityError:
            return self.sql(
                'thing_loc_and_next_upd',
                loc,
                nextloc,
                character,
                thing,
                branch,
                tick
            )

    def sense_fun_set(self, character, sense, branch, tick, funn, active):
        character = self.json_dump(character)
        try:
            self.sql(
                'sense_fun_ins', character, sense, branch, tick, funn, active
            )
        except IntegrityError:
            self.sql(
                'sense_fun_upd', funn, active, character, sense, branch, tick
            )

    def sense_set(self, character, sense, branch, tick, active):
        character = self.json_dump(character)
        try:
            self.sql('sense_ins', character, sense, branch, tick, active)
        except IntegrityError:
            self.sql('sense_upd', active, character, sense, branch, tick)

    def init_character(
            self, character, character_rulebook=None, avatar_rulebook=None,
            thing_rulebook=None, place_rulebook=None, node_rulebook=None,
            portal_rulebook=None
    ):
        character_rulebook = character_rulebook or (character, 'character')
        avatar_rulebook = avatar_rulebook or (character, 'avatar')
        thing_rulebook = thing_rulebook or (character, 'character_thing')
        place_rulebook = place_rulebook or (character, 'character_place')
        node_rulebook = node_rulebook or (character, 'character_node')
        portal_rulebook = portal_rulebook or (character, 'character_portal')
        (character, character_rulebook, avatar_rulebook, thing_rulebook,
         place_rulebook, node_rulebook, portal_rulebook) = map(
            self.json_dump,
            (character, character_rulebook, avatar_rulebook, thing_rulebook,
             place_rulebook, node_rulebook, portal_rulebook)
        )
        try:
            return self.sql(
                'character_ins',
                character,
                character_rulebook,
                avatar_rulebook,
                thing_rulebook,
                place_rulebook,
                node_rulebook,
                portal_rulebook
            )
        except IntegrityError:
            pass

    def avatarness_dump(self):
        for (
                character,
                graph,
                node,
                branch,
                tick,
                is_avatar
        ) in self.sql('avatarness_dump'):
            yield (
                self.json_load(character),
                self.json_load(graph),
                self.json_load(node),
                branch,
                tick,
                bool(is_avatar)
            )

    def avatar_set(self, character, graph, node, branch, tick, isav):
        (character, graph, node) = map(
            self.json_dump, (character, graph, node)
        )
        try:
            return self.sql(
                'avatar_ins', character, graph, node, branch, tick, isav
            )
        except IntegrityError:
            return self.sql(
                'avatar_upd', isav, character, graph, node, branch, tick
            )

    def rulebook_ins(self, rulebook, idx, rule):
        (rulebook, rule) = map(self.json_dump, (rulebook, rule))
        self.sql('rulebook_inc', rulebook, idx)
        try:
            return self.sql('rulebook_ins', rulebook, idx, rule)
        except IntegrityError:
            return self.sql('rulebook_upd', rule, rulebook, idx)

    def rulebook_set(self, rulebook, idx, rule):
        (rulebook, rule) = map(self.json_dump, (rulebook, rule))
        try:
            return self.sql('rulebook_ins', rulebook, idx, rule)
        except IntegrityError:
            return self.sql('rulebook_upd', rule, rulebook, idx)

    def rulebook_decr(self, rulebook, idx):
        self.sql('rulebook_dec', self.json_dump(rulebook), idx)

    def rulebook_del(self, rulebook, idx):
        rulebook = self.json_dump(rulebook)
        self.sql('rulebook_del', rulebook, idx)
        self.sql('rulebook_dec', rulebook, idx)

    def rulebook_rules(self, rulebook):
        pass
        # TODO

    def rulebooks_rules(self):
        for (rulebook, rule) in self.sql('rulebooks_rules'):
            yield map(self.json_load, (rulebook, rule))

    def rulebook_get(self, rulebook, idx):
        return self.json_load(
            self.sql(
                'rulebook_get', self.json_dump(rulebook), idx
            ).fetchone()[0]
        )

    def branch_descendants(self, branch):
        for child in self.sql('branch_children', branch):
            yield child
            yield from self.branch_descendants(child)

    def initdb(self):
        """Set up the database schema, both for allegedb and the special
        extensions for LiSE

        """
        super().initdb()
        for table in (
            'universals',
            'rulebooks',
            'characters',
            'senses',
            'things',
            'node_rulebook',
            'portal_rulebook',
            'avatars',
            'character_rules_handled',
            'avatar_rules_handled',
            'character_thing_rules_handled',
            'character_place_rules_handled',
            'character_node_rules_handled',
            'character_portal_rules_handled',
            'thing_rules_handled',
            'place_rules_handled',
            'portal_rules_handled',
            'rule_triggers',
            'rule_prereqs',
            'rule_actions'
        ):
            self.init_table(table)
        try:
            self.sql('view_node_rules_handled')
        except OperationalError:
            pass
        for idx in (
            'senses',
            'things',
            'avatars',
            'character_rules_handled',
            'avatar_rules_handled',
            'character_thing_rules_handled',
            'character_place_rules_handled',
            'character_node_rules_handled',
            'character_portal_rules_handled',
            'thing_rules_handled',
            'place_rules_handled',
            'portal_rules_handled'
        ):
            self.index_table(idx)
