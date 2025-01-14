LiSE is an application for developing life simulation games.

[Documentation](https://logicaldash.github.io/LiSE/manual.html)

[Development
blog](http://forums.tigsource.com/index.php?topic=35227.0)

# What is a life simulation game?

Life simulation games simulate the world in relatively high detail,
but not in the sense that physics engines are concerned with --
rather, each game in the genre has a different take on the mundane
events that constitute everyday life for its purpose. Logistics and
RPG elements tend to feature heavily in these games, but a lot of the
appeal is in the parts that are not directly under player control.
The game world feels like it continues to exist when you're not playing
it, because so much of it seems to operate independently of you.

Existing games that LiSE seeks to imitate include:

* The Sims
* Redshirt
* Rimworld
* Princess Maker
* Monster Rancher
* Dwarf Fortress
* Democracy
* Crusader Kings
* The King of Dragon Pass
* [Galimulator](https://snoddasmannen.itch.io/galimulator)
* [Vilmonic](https://bludgeonsoft.itch.io/)

# Why should I use LiSE for this purpose?

LiSE is a game engine in the sense of RPG Maker or Ren'Py. It assumes
that there are certain problems any designer of life simulators will
have, and provides powerful tools specialized to those
problems. Though you will still need to write some Python code for
your game, it should only be the code that describes how your game's
world works. If you don't want to worry about the data structure that
represents the world, LiSE gives you one that will work. And if you
don't want to write a user interface, you can play the game in the
IDE.

The LiSE data model has been designed from the ground up to support
debugging of complex simulations. It remembers everything that ever
happened in the simulation, so that when something strange happens
in a playtester's game, and they send you their save file, you can
track down the cause, even if it happened long before the
tester knew to look for it.

# Features

## Core

* *Infinite time travel*, rendering traditional save files obsolete.
* Integration with [NetworkX](http://networkx.github.io) for
  convenient access to various *graph algorithms*, particularly
  pathfinding.
* *Rules engine* for game logic. Rules are written in plain
  Python. They are composable, and can be disabled or reassigned to
  different entities mid-game.
* Can be run as a *web server*, so that you can control LiSE and query
  its world state from any other game engine you please.

## IDE

* *Instant replay*: go back to previous world states whenever you feel
  like it, with minimal loading.
* *Rule constructor*: Build rules out of short functions representing
  triggers and actions.
* *Rule stepper*: view the world state in the middle of a turn, just
  after one rule's run and before another.
* Edit state in graph or grid view
* Edit rule functions with syntax highlighting

# Getting started

## Windows
```
# install the Kivy app framework
python3 -m pip install kivy.deps.sdl2 kivy.deps.glew
# install LiSE and the ELiDE frontend
git clone https://github.com/LogicalDash/LiSE.git
cd LiSE
pip3 install --user LiSE\ ELiDE\
```

## Linux
```
# install the Kivy app framework
sudo apt-get install cython3 python3-dev python3-pip \
libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \
libsdl2-ttf-dev
# ELiDE doesn't play movies, so disable gstreamer
USE_GSTREAMER=0 pip3 install --user kivy
# install LiSE and the ELiDE frontend
git clone https://github.com/LogicalDash/LiSE.git
cd LiSE
pip3 install --user LiSE/ ELiDE/
```

## Mac
```
# if you don't have https://brew.sh installed:
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
# if you don't have python 3 installed: 
brew install python pip3
# install the Kivy app framework
brew install cython
# ELiDE doesn't play movies, so disable gstreamer
USE_GSTREAMER=0 pip3 install --user kivy
# install LiSE and the ELiDE frontend
git clone https://github.com/LogicalDash/LiSE.git
cd LiSE
pip3 install --user LiSE/ ELiDE/
```

You could now start the graphical frontend with ``python3 -mELiDE``,
but this might not be very useful, as you don't have any world state
to edit yet. You could laboriously assemble a gameworld by hand, but
instead let's generate one, Parable of the Polygons by Nicky Case.

Make a new Python script, let's say 'polygons.py', and write the
following in it (or use the [example
version](https://github.com/LogicalDash/LiSE/blob/master/LiSE/LiSE/examples/polygons.py)):

```python
from LiSE import Engine
import networkx as nx

with Engine(clear=True) as eng:
    phys = eng.new_character('physical', nx.grid_2d_graph(20, 20))
    tri = eng.new_character('triangle')
    sq = eng.new_character('square')
```

This starts a new game with its world state stored in the file
'world.db'. Because of ``clear`` being ``True``, it will delete any
existing world state and game code each time it's run, which is often
useful when you're getting started. It creates three characters, one
of which, named 'physical', has a 20x20 grid in it.  The others are
empty, and in fact we don't intend to put any graph in them; they're
just for keeping track of things in ``physical``. Add the following
inside the `with` block of `polygons.py`:

```python
empty = list(phys.place.values())
eng.shuffle(empty)
# distribute 30 of each shape randomly among the empty places
for i in range(1, 31):
    place = empty.pop()
    square = place.new_thing('square%i' % i, _image_paths=['atlas://polygons/meh_square'])
    sq.add_unit(square)
for i in range(1, 31):
    place = empty.pop()
    triangle = place.new_thing('triangle%i' % i, _image_paths=['atlas://polygons/meh_triangle'])
    tri.add_unit(triangle)
```

Now there are thirty each of squares and triangles in the world. They
are things, rather than places, which just means they have locations
-- each square and triangle is located in a place in the graph.

The new_thing method of a place object creates a new thing and puts it
there. You have to give the thing a name as its first argument. You
can supply further keyword arguments to customize the thing's stats;
in this case, I've given the things graphics representing what shape
they are. If you wanted, you could set the `_image_paths` to a list of
paths to whatever graphics. The 'atlas://' in the front is only
necessary if you're using graphics packed in the way that the default
ones are; [read about
atlases](https://kivy.org/doc/stable/api-kivy.atlas.html) if you like,
or just use some .png files you have lying around.

The add_avatar method of a character object marks a thing or place so
that it's considered part of a character whose graph it is not
in. This doesn't do anything yet, but we'll be using it to write our
rules in a little while.

Now we have our world, but nothing ever happens in it. Let's add the
rules of the simulation:

```python
    @eng.function
    def cmp_neighbor_shapes(poly, cmp, stat):
        """Compare the proportion of neighboring polys with the same shape as this one

        Count the neighboring polys that are the same shape as this one, and return how that compares with
        some stat on the poly's user.

        """
        home = poly.location
        similar = 0
        n = 0
        # iterate over portals leading outward from home
        for neighbor_portal in home.portal.values():
            n += 1
            neighbor_home = neighbor_portal.destination
            # there's really only 1 polygon per home right now, but this will still work if there are more
            for neighbor in neighbor_home.contents():
                if neighbor.user is poly.user:
                    similar += 1
        return cmp(poly.user.stat[stat], similar / n)

    @phys.thing.rule
    def relocate(poly):
        """Move to a random unoccupied place"""
        unoccupied = [place for place in poly.character.place.values() if not place.content]
        poly.location = poly.engine.choice(unoccupied)

    @relocate.trigger
    def similar_neighbors(poly):
        """Trigger when my neighborhood fails to be enough like me"""
        from operator import ge
        return poly.engine.function.cmp_neighbor_shapes(poly, ge, 'min_sameness')

    @relocate.trigger
    def dissimilar_neighbors(poly):
        """Trigger when my neighborhood gets too much like me"""
        from operator import lt
        return poly.engine.function.cmp_neighbor_shapes(poly, lt, 'max_sameness')
```

The core of this ruleset is the ``cmp_neighbor_shapes`` function,
which is a plain Python function that I've chosen to store in the
engine because that makes it easier for the rules to get at.
Functions decorated with ``@engine.function`` become accessible as
attributes of ``engine.function``.  Every LiSE entity has an attribute
``engine`` that you can use to get at that function store and lots of
other utilities.

If you didn't want to use the function store, you could just import
``cmp_neighbor_shapes`` in every rule that uses it, like I've done
with the operators ``ge`` and ``lt`` here.

``cmp_neighbor_shapes`` looks over the places that are directly
connected to the one a given shape is in, counts the number that
contain the same shape, and compares the result to a stat of the
``user``--the character of which this thing is an avatar. When called
in ``similar_neighbors`` and ``dissimilar_neighbors``, the stats in
question are 'min_sameness' and 'max_sameness' respectively, so let's
set those:

```python
    sq.stat['min_sameness'] = 0.1
    sq.stat['max_sameness'] = 0.9
    tri.stat['min_sameness'] = 0.2
    tri.stat['max_sameness'] = 0.8
```

Here we diverge from the original simulation a bit by setting these
values differently for the different shapes, demonstrating an
advantage of avatars.

Run ``python3 polygons.py`` to generate the simulation. To view it,
run ``python3 -m ELiDE`` in the same directory.  Just click the big
&gt; button and watch it for a little while. There's a control panel
on the bottom of the screen that lets you go back in time, if you
wish, and you can use that to browse different runs of the simulation
with different starting conditions, or even stats and rules
arbitrarily changing in the middle of a run.

If you'd prefer to run the simulation without ELiDE, though, you can add this to your script:
```python
    for i in range(10):
        eng.next_turn()
```

Every change to the world will be saved in the database so that you
can browse it in ELiDE at your leisure.  If you want to travel through
time programmatically, set the properties ``eng.branch`` (to a
string), ``eng.turn``, and ``eng.tick`` (to integers).

What next? If you wanted, you could set rules to be followed by only
some of the shapes, like so:

```python
    # this needs to replace any existing rule code you've written,
# it won't work so well together with eg. @phys.thing.rule
@tri.unit.rule
def tri_relocate(poly):
    """Move to a random unoccupied place"""
    unoccupied = [place for place in poly.character.place.values() if not place.content]
    poly.location = poly.engine.choice(unoccupied)


@tri_relocate.trigger
def similar_neighbors(poly):
    """Trigger when my neighborhood fails to be enough like me"""
    from operator import ge
    return poly.engine.function.cmp_neighbor_shapes(poly, ge, 'min_sameness')


@sq.unit.rule
def sq_relocate(poly):
    """Move to a random unoccupied place"""
    unoccupied = [place for place in poly.character.place.values() if not place.content]
    poly.location = poly.engine.choice(unoccupied)


@sq_relocate.trigger
def dissimilar_neighbors(poly):
    """Trigger when my neighborhood gets too much like me"""
    from operator import lt
    return poly.engine.function.cmp_neighbor_shapes(poly, lt, 'max_sameness')
```

Now the triangles only relocate whenever their neighborhood looks too
much like them, whereas squares only relocate when they have too many
triangle neighbors.

You should make sure your rules have unique names. This requirement is
necessary for assigning rules by name rather than decorator; you could
make triangles move in response to dissimilar neighbors like so:

```python
    tri.unit.rulebook.append('sq_relocate')
```

In this case you didn't really *have* to use the name of the rule,
since you still have the rule object in scope, but maybe you won't
always. And, anyway, rules are evaluated in an order similar to
alphabetical order, so having two rules with the same name would be
unacceptably ambiguous.

When you have a set of rules that needs to apply to many different
entities, and you can't just make them all avatars, you can have the
entities share a rulebook. This works:

```python
    sq.unit.rulebook = tri.unit.rulebook
```

And would result in pretty much the same simulation as in the first
place, with all the shapes following the same rules, but now you could
have other things in ``phys``, and they wouldn't necessarily follow
those rules.

Or you could build a rulebook ahead-of-time and assign it to many
entities:

```python
    # this needs to replace any existing rule code you've written,
    # it won't work so well together with eg. @phys.thing.rule
    @eng.rule
    def relocate(poly):
        """Move to a random unoccupied place"""
        unoccupied = [place for place in poly.character.place.values() if not place.content]
        poly.location = poly.engine.choice(unoccupied)

    @relocate.trigger
    def similar_neighbors(poly):
        """Trigger when my neighborhood fails to be enough like me"""
        from operator import ge
        return poly.engine.function.cmp_neighbor_shapes(poly, ge, 'min_sameness')

    @relocate.trigger
    def dissimilar_neighbors(poly):
        """Trigger when my neighborhood gets too much like me"""
        from operator import lt
        return poly.engine.function.cmp_neighbor_shapes(poly, lt, 'max_sameness')

    # rulebooks need names too, so you have to make it like this
    eng.rulebook['parable'] = [relocate]
    sq.rulebook = tri.rulebook = 'parable'
```

There are a variety of graph generators accessible on character
objects, and convenience methods for common game actions like
travelling along a path (``Thing.travel_to``) accessible on thing and
place objects.  The API for these isn't really solid yet, but tell me
how you like them.

# License Information

ELiDE uses third-party graphics sets:

* The [RLTiles](http://rltiles.sourceforge.net/), available under
  [CC0](http://creativecommons.org/publicdomain/zero/1.0/), being in
  the public domain where it exists.
* "Crypt" and "Island" from the [PROCJAM 2015 Art
  Pack](http://www.procjam.com/2015/09/procjam-art-pack-now-available/),
  by Marsh Davies, available under the terms of [Creative Commons
  BY-NC](http://creativecommons.org/licenses/by-nc/4.0/)
* The default wallpaper, wallpape.jpg, is copyright [Fantastic
  Maps](http://www.fantasticmaps.com/free-stuff/), freely available
  under the terms of [Creative Commons
  BY-NC-SA](https://creativecommons.org/licenses/by-nc-sa/3.0/).
* The ELiDE icon is by Robin Hill, used with permission.

``reify.py`` is derived from the Pyramid project and carries its
BSD-like license.

``collide.py`` is ported from Kivy's ``garden.collider`` module and
carries the MIT license.

The allegedb, LiSE, and ELiDE source files are licensed under the
terms of the GNU Affero Public License version 3 (and no later).
If you make a game with it, you have to release any modifications you
make to LiSE itself under the AGPL, but this doesn't apply to your 
game code. Game code is that which is loaded into the engine at
launch time from modules specified by the following parameters
to the LiSE engine:
* ``trigger``
* ``prereq``
* ``action``
* ``function``
* ``method``

If you write another application (not using any allegedb, LiSE, or
ELiDE code) that accesses a LiSE server via HTTP(S), it is separate
from LiSE and not subject to its license.

In case of my death, I, Zachary Spector, wish for every allegedb,
LiSE, texturestack, and ELiDE source file to be relicensed under
[CC0](https://creativecommons.org/choose/zero/).
