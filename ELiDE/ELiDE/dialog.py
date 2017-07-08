"""Generic dialog boxes and menus, for in front of a Board

Mostly these will be added as children of KvLayoutFront but you
could use them independently if you wanted.

"""
from functools import partial
from kivy.properties import DictProperty, ListProperty, StringProperty, NumericProperty, VariableListProperty
from kivy.core.text import DEFAULT_FONT
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.button import Button
from kivy.uix.widget import Widget
from kivy.uix.scrollview import ScrollView
from kivy.lang import Builder


class Box(Widget):
    padding = VariableListProperty([6, 6, 6, 6])
    border = ListProperty([4, 4, 4, 4])
    font_size = StringProperty('15sp')
    font_name = StringProperty(DEFAULT_FONT)
    background = StringProperty(
        'atlas://data/images/defaulttheme/textinput')
    background_color = ListProperty([1, 1, 1, 1])
    foreground_color = ListProperty([0, 0, 0, 1])


class ScrollableLabel(ScrollView):
    font_size = StringProperty('15sp')
    font_name = StringProperty(DEFAULT_FONT)
    color = ListProperty([0, 0, 0, 1])
    line_spacing = NumericProperty(0)
    text = StringProperty()


class MessageBox(Box):
    """Looks like a TextInput but doesn't accept any input.

    Does support styled text with BBcode.

    """
    line_spacing = NumericProperty(0)
    text = StringProperty()


class DialogMenu(Box):
    """Some buttons that make the game do things."""
    options = ListProperty()
    """List of pairs of (button_text, partial)"""
    funcs = DictProperty({})
    """Dict of functions to be used in place of string partials in the options"""

    def on_options(self, *args):
        self.clear_widgets()
        if not hasattr(self, '_sv'):
            self._sv = ScrollView(size=self.size, pos=self.pos)
            self.bind(size=self._sv.setter('size'), pos=self._sv.setter('pos'))
            self._sv.add_widget(BoxLayout(orientation='vertical'))
        layout = self._sv.children[0]
        for txt, part in self.options:
            if not callable(part):
                if isinstance(part, tuple):
                    fun = part[0]
                    args = part[1]
                    if len(part) == 3:
                        kwargs = part[2]
                        part = partial(fun, *args, **kwargs)
                    else:
                        part = partial(fun, *args)
                else:
                    part = self.funcs[part]
            layout.add_widget(Button(text=txt, on_press=part, font_name=self.font_name, font_size=self.font_size))
        self.add_widget(self._sv)


class Dialog(BoxLayout):
    """MessageBox with a DialogMenu beneath it"""
    message_kwargs = DictProperty({})
    menu_kwargs = DictProperty({})

    def on_message_kwargs(self, *args):
        for k, v in self.message_kwargs.items():
            setattr(self.ids.msg, k, v)

    def on_menu_kwargs(self, *args):
        for k, v in self.menu_kwargs.items():
            setattr(self.ids.menu, k, v)


Builder.load_string("""
<Box>:
    canvas.before:
        Color:
            rgba: self.background_color
        BorderImage:
            border: self.border
            pos: self.pos
            size: self.size
            source: self.background
        Color:
            rgba: 1, 1, 1, 1
<ScrollableLabel>:
    Label:
        size_hint_y: None
        height: self.texture_size[1]
        text_size: self.width, None
        text: root.text
        color: root.color
<MessageBox>:
    ScrollableLabel:
        x: root.x + root.padding[0]
        y: root.y + root.padding[3]
        width: root.width - root.padding[2]
        height: root.height - root.padding[1]
        text: root.text
        color: root.foreground_color
<Dialog>:
    orientation: 'vertical'
    pos_hint: {'x': 0, 'y': 0}
    size_hint: 1, 0.3
    MessageBox:
        id: msg
    DialogMenu:
        id: menu
""")


if __name__ == "__main__":
    from kivy.base import runTouchApp
    dia = Dialog(
        message_kwargs={'text': 'I am a dialog'},
        menu_kwargs={'options': [('one', lambda: None), ('two', lambda: None)]}
    )