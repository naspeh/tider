#!/usr/bin/env python
import time
from collections import namedtuple

import cairo as C
from gi.repository import Gtk, GObject

Context = namedtuple('Context', 'conf start active target win tray menu')
Config = namedtuple('Config', 'app_dir timeout')


class Variable:
    __slots__ = ('value', )

    def __init__(self, value=None):
        self.value = value

    def __repr__(self):
        return 'Variable({!r})'.format(self.value)


def wavelog():
    g = Context(
        conf=Config(timeout=500, app_dir='./var/'),
        start=Variable(),
        active=Variable(False),
        target=Variable(''),
        win=create_win(),
        menu=create_menu(),
        tray=create_icon(),
    )

    g.menu.start.connect('activate', toggle_target, True, g)
    g.menu.stop.connect('activate', toggle_target, False, g)
    g.tray.connect('activate', toggle_win, g.win)
    g.tray.connect('popup-menu', show_menu, g.menu)
    GObject.timeout_add(g.conf.timeout, update_img, g)

    toggle_target(g.menu.stop, False, g)
    update_img(g)


def toggle_win(widget, win):
    if win.is_visible():
        win.hide()
    else:
        win.show_all()


def create_win():
    img = Gtk.Image()
    vbox = Gtk.VBox()
    vbox.pack_start(img, False, True, 1)

    win = Gtk.Window(
        title='Wavelog', resizable=False, decorated=False,
        skip_pager_hint=True, skip_taskbar_hint=True
    )
    win.set_keep_above(True)
    win.move(960, 0)
    win.add(vbox)
    win.show_all()
    win.img = img

    win.connect('destroy', lambda wid: Gtk.main_quit())
    return win


def create_menu():
    start = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_YES, None)
    start.set_label('Start working')

    stop = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_NO, None)
    stop.set_label('Stop working')

    about = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_ABOUT, None)
    about.connect('activate', show_about)
    about.show()

    quit = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
    quit.connect('activate', Gtk.main_quit)
    quit.show()

    menu = Gtk.Menu()
    menu.append(start)
    menu.append(stop)
    menu.append(about)
    menu.append(Gtk.SeparatorMenuItem())
    menu.append(quit)
    menu.start = start
    menu.stop = stop
    return menu


def toggle_target(widget, flag, g):
    if flag:
        g.menu.start.hide()
        g.menu.stop.show()
        g.tray.set_from_stock(Gtk.STOCK_YES)
        g.active.value = True
        g.target.value = 'work'
    else:
        g.menu.stop.hide()
        g.menu.start.show()
        g.tray.set_from_stock(Gtk.STOCK_NO)
        g.active.value = False
        g.target.value = 'break'


def show_menu(icon, e_button, e_time, menu):
    menu.popup(None, None, icon.position_menu, icon, e_button, e_time)


def show_about(widget):
    about = Gtk.AboutDialog()
    about.set_destroy_with_parent(True)
    about.set_icon_name('Wavelog')
    about.set_name('Wavelog')
    about.set_version('alfa')
    about.run()
    about.destroy()


def create_icon():
    tray = Gtk.StatusIcon()
    return tray


def update_img(g):
    duration = {'total': 0}
    if g.start.value:
        duration['total'] = int(time.time() - g.start.value)
    else:
        g.start.value = time.time()

    duration['min'] = int(duration['total'] / 60)
    duration['sec'] = duration['total'] - duration['min'] * 60

    max_w = 60
    max_h = 20
    padding = max_h / 8
    box_h = max_h - 2 * padding
    box_w = max_w - 2 * padding
    font_h = box_h - padding * 1.5
    font_rgb = (0, 0, 0)
    timer_w = box_w * 0.4 + padding
    color = (0.6, 0.9, 0.6) if g.active.value else (0.7, 0.7, 0.7)

    icon_path = g.conf.app_dir + 'example.png'
    src = C.ImageSurface(C.FORMAT_ARGB32, max_w, max_h)
    ctx = C.Context(src)

    ctx.set_line_width(0.5)
    ctx.set_source_rgb(*color)

    ctx.rectangle(0, 0, max_w, max_h)
    ctx.stroke()

    ctx.rectangle(0, 0, timer_w + padding / 2, max_h)
    ctx.fill()

    ctx.set_source_rgb(*font_rgb)
    #ctx.select_font_face('Mono', C.FONT_SLANT_NORMAL, C.FONT_WEIGHT_BOLD)
    ctx.set_font_size(font_h)

    text = str(duration['min'])
    text_w, text_h = ctx.text_extents(text)[2:4]
    ctx.move_to(timer_w - text_w - padding, text_h + 2 * padding)
    ctx.show_text(text)

    ctx.move_to(timer_w + padding, text_h + 2 * padding)
    ctx.show_text(g.target.value)

    line_h = 3
    step_sec = 2
    step_w = timer_w * step_sec / 60
    duration_w = int(duration['sec'] / step_sec) * step_w
    ctx.set_line_width(line_h)
    ctx.set_source_rgb(0, 0, 0.7)
    ctx.move_to(timer_w, max_h - line_h / 2)
    ctx.line_to(timer_w - duration_w, max_h - line_h / 2)
    ctx.stroke()

    src.write_to_png(icon_path)
    #tray.set_from_file(icon_path)
    g.win.img.set_from_file(icon_path)
    return True


if __name__ == '__main__':
    wavelog()
    Gtk.main()
