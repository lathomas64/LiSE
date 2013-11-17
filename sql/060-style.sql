-- This file is part of LiSE, a framework for life simulation games.
-- Copyright (c) 2013 Zachary Spector,  zacharyspector@gmail.com
INSERT INTO style
(name, fontface, fontsize, spacing, textcolor,
bg_inactive, bg_active, fg_inactive, fg_active) VALUES
    ('BigDark',
     'DroidSans', 20, 6,
     'solarized-base0',
     'solarized-base03',
     'solarized-base2',
     'solarized-base1',
     'solarized-base01'),
    ('SmallDark',
     'DroidSans', 16, 3, 
     'solarized-base0',
     'solarized-base03',
     'solarized-base2',
     'solarized-base1',
     'solarized-base01'),
    ('BigLight',
     'DroidSans', 20, 6,
     'solarized-base00',
     'solarized-base3',
     'solarized-base02',
     'solarized-base01',
     'solarized-base1'),
    ('SmallLight',
     'DroidSans', 16, 3,
     'solarized-base00',
     'solarized-base3',
     'solarized-base02',
     'solarized-base01',
     'solarized-base1'),
    ('default_style',
     'DroidSans', 20, 6,
     'solarized-base00',
     'solarized-base3',
     'solarized-base02',
     'solarized-base01',
     'solarized-base1'),
    ('solid_symbols',
     'assets/Entypo.ttf', 40, 6,
     'transparent',
     'transparent',
     'transparent',
     'transparent',
     'black');
