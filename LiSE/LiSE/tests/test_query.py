# This file is part of LiSE, a framework for life simulation games.
# Copyright (c) Zachary Spector, public@zacharyspector.com
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
import re
from functools import reduce
from collections import defaultdict
from LiSE.engine import Engine
import pytest
import os
import shutil
import tempfile


@pytest.fixture(scope='module')
def college24_premade():
    directory = tempfile.mkdtemp('.')
    shutil.unpack_archive(
        os.path.join(os.path.abspath(os.path.dirname(__file__)),
                     'college24_premade.tar.xz'), directory)
    with Engine(directory) as eng:
        yield eng
    shutil.rmtree(directory)


def roommate_collisions(college24_premade):
    """Test queries' ability to tell that all of the students that share
    rooms have been in the same place.

    """
    engine = college24_premade
    done = set()
    for chara in engine.character.values():
        if chara.name in done:
            continue
        match = re.match(r'dorm(\d)room(\d)student(\d)', chara.name)
        if not match:
            continue
        dorm, room, student = match.groups()
        other_student = '1' if student == '0' else '0'
        student = chara
        other_student = engine.character['dorm{}room{}student{}'.format(
            dorm, room, other_student)]

        same_loc_turns = list(
            engine.turns_when(
                student.unit.only.historical('location') ==
                other_student.unit.only.historical('location')))
        assert same_loc_turns, "{} and {} don't seem to share a room".format(
            student.name, other_student.name)
        assert len(
            same_loc_turns
        ) >= 6, "{} and {} did not share their room for at least 6 turns".format(
            student.name, other_student.name)

        done.add(student.name)
        done.add(other_student.name)


def test_roomie_collisions_premade(college24_premade):
    roommate_collisions(college24_premade)


def sober_collisions(college24_premade):
    """Students that are neither lazy nor drunkards should all have been
    in class together at least once.

    """
    engine = college24_premade
    students = [
        stu for stu in engine.character['student_body'].stat['characters']
        if not (stu.stat['drunkard'] or stu.stat['lazy'])
    ]

    assert students

    def sameClasstime(stu0, stu1):
        assert list(
            engine.turns_when(
                stu0.unit.only.historical('location') == stu1.unit.only.
                historical('location') == engine.alias('classroom'))
        ), """{stu0} seems not to have been in the classroom 
                at the same time as {stu1}.
                {stu0} was there at turns {turns0}
                {stu1} was there at turns {turns1}""".format(
            stu0=stu0.name,
            stu1=stu1.name,
            turns0=list(
                engine.turns_when(
                    stu0.unit.only.historical('location') == engine.alias(
                        'classroom'))),
            turns1=list(
                engine.turns_when(
                    stu1.unit.only.historical('location') == engine.alias(
                        'classroom'))))
        return stu1

    reduce(sameClasstime, students)


def test_sober_collisions_premade(college24_premade):
    sober_collisions(college24_premade)


def noncollision(college24_premade):
    """Make sure students *not* from the same room never go there together"""
    engine = college24_premade
    dorm = defaultdict(lambda: defaultdict(dict))
    for character in engine.character.values():
        match = re.match(r'dorm(\d)room(\d)student(\d)', character.name)
        if not match:
            continue
        d, r, s = match.groups()
        dorm[d][r][s] = character
    for d in dorm:
        other_dorms = [dd for dd in dorm if dd != d]
        for r in dorm[d]:
            other_rooms = [rr for rr in dorm[d] if rr != r]
            for stu0 in dorm[d][r].values():
                for rr in other_rooms:
                    for stu1 in dorm[d][rr].values():
                        assert not list(
                            engine.turns_when(
                                stu0.unit.only.historical('location') ==
                                stu1.unit.only.historical('location') ==
                                engine.alias('dorm{}room{}'.format(d, r)))
                        ), "{} seems to share a room with {}".format(
                            stu0.name, stu1.name)
                common = 'common{}'.format(d)
                for dd in other_dorms:
                    for rr in dorm[dd]:
                        for stu1 in dorm[dd][rr].values():
                            assert not list(
                                engine.turns_when(
                                    stu0.unit.only.historical('location') ==
                                    stu1.unit.only.historical(
                                        'location') == engine.alias(common))
                            ), "{} seems to have been in the same common room  as {}".format(
                                stu0.name, stu1.name)


def test_noncollision_premade(college24_premade):
    noncollision(college24_premade)
