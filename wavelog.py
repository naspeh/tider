#!/usr/bin/env python
import sqlite3
import time
from collections import namedtuple

import cairo as C
from gi.repository import Gtk, GObject

Context = namedtuple('Context', 'conf db start active target win tray menu')
Config = namedtuple('Config', 'app_dir timeout')


class Variable:
    __slots__ = ('value', )

    def __init__(self, value=None):
        self.value = value

    def __repr__(self):
        return 'Variable({!r})'.format(self.value)


def wavelog():
    conf = Config(timeout=500, app_dir='./var/')
    g = Context(
        conf=conf,
        db=connect_db(conf),
        start=Variable(),
        active=Variable(False),
        target=Variable('OFF'),
        win=create_win(),
        menu=create_menu(),
        tray=Gtk.StatusIcon(),
    )

    g.win.connect('destroy', lambda x: main_quit(g))
    g.win.connect('delete_event', lambda x, y: main_quit(g))

    g.menu.child_quit.connect('activate', lambda x: main_quit(g))
    g.menu.child_off.connect('activate', disable, g)
    g.menu.child_start.connect('activate', lambda x: toggle_active(g, True))
    g.menu.child_stop.connect('activate', lambda x: toggle_active(g, False))
    g.menu.child_target.connect('activate', change_target, g)

    g.tray.connect('activate', toggle_win, g.win)
    g.tray.connect(
        'popup-menu',
        lambda icon, button, time: (
            g.menu.popup(None, None, icon.position_menu, icon, button, time)
        )
    )
    GObject.timeout_add(
        g.conf.timeout, lambda: g.start.value is None or update_ui(g)
    )

    update_ui(g)


def main_quit(g):
    save_log(g)
    Gtk.main_quit()


def disable(widget, g):
    save_log(g)
    g.start.value = None
    update_ui(g)


def toggle_win(widget, win):
    if win.is_visible():
        win.hide()
    else:
        win.show_all()


def toggle_active(g, flag=True, target=None):
    if g.start.value:
        save_log(g)
    if target:
        g.target.value = target

    g.start.value = time.time()
    g.active.value = flag

    update_ui(g)


def change_target(widget, g):
    dialog = Gtk.Dialog('Enter target')
    box = dialog.get_content_area()
    entry = Gtk.Entry()
    entry.set_text(g.target.value)
    box.add(entry)
    dialog.add_buttons(
        Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
        Gtk.STOCK_OK, Gtk.ResponseType.OK
    )
    dialog.show_all()

    response = dialog.run()
    if response == Gtk.ResponseType.OK:
        toggle_active(g, target=entry.get_text())

    dialog.destroy()


def show_about(widget):
    about = Gtk.AboutDialog()
    about.set_destroy_with_parent(True)
    about.set_icon_name('Wavelog')
    about.set_name('Wavelog')
    about.set_version('alfa')
    about.run()
    about.destroy()


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
    return win


def create_menu():
    start = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_PLAY, None)
    start.set_label('Start working')

    stop = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_PAUSE, None)
    stop.set_label('Stop working')

    off = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_STOP, None)
    off.set_label('OFF')

    target = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_OK, None)
    target.set_label('Change target')
    target.show()

    separator = Gtk.SeparatorMenuItem()
    separator.show()

    about = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_ABOUT, None)
    about.connect('activate', show_about)
    about.show()

    quit = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
    quit.show()

    menu = Gtk.Menu()
    menu.append(target)
    menu.append(start)
    menu.append(stop)
    menu.append(off)
    menu.append(separator)
    menu.append(about)
    menu.append(quit)

    menu.child_start = start
    menu.child_stop = stop
    menu.child_off = off
    menu.child_target = target
    menu.child_quit = quit
    return menu


def update_ui(g):
    duration = {'total': 0}
    if g.start.value:
        duration['total'] = int(time.time() - g.start.value)
    duration['min'] = int(duration['total'] / 60)
    duration['sec'] = duration['total'] - duration['min'] * 60

    if g.start.value is None:
        g.menu.child_off.hide()
        g.menu.child_start.hide()
        g.menu.child_stop.hide()
        g.tray.set_from_stock(Gtk.STOCK_MEDIA_STOP)
    elif g.active.value:
        g.menu.child_off.show()
        g.menu.child_start.hide()
        g.menu.child_stop.show()
        g.tray.set_from_stock(Gtk.STOCK_MEDIA_PLAY)
    else:
        g.menu.child_off.show()
        g.menu.child_stop.hide()
        g.menu.child_start.show()
        g.tray.set_from_stock(Gtk.STOCK_MEDIA_PAUSE)

    max_w = 60
    max_h = 20
    padding = max_h / 8
    box_h = max_h - 2 * padding
    box_w = max_w - 2 * padding
    font_h = box_h - padding * 1.5
    font_rgb = (0, 0, 0)
    timer_w = box_w * 0.4 + padding
    color = (0.6, 0.9, 0.6) if g.active.value else (0.7, 0.7, 0.7)

    icon_path = g.conf.app_dir + 'wavelog.png'
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


def connect_db(conf):
    db_path = conf.app_dir + 'wavelog.db'
    db = sqlite3.connect(db_path)
    cur = db.cursor()
    cur.execute(
        'SELECT name FROM sqlite_master WHERE type="table" AND name="log"'
    )
    if not cur.fetchone():
        cur.execute(
            '''
            CREATE TABLE `log`(
                `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                `target` varchar(255) NOT NULL,
                `started` TEXT,
                `ended` TEXT,
                `duration` REAL,
                `is_active` INTEGER,
                UNIQUE (target, started)
            )
            '''
        )
        db.commit()
    return db


def save_log(g):
    if g.start.value is None:
        return

    cur = g.db.cursor()
    target = g.target.value
    duration = time.time() - g.start.value
    started = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(g.start.value))
    ended = time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())
    is_active = 1 if g.active.value else 0
    cur.execute(
        'SELECT id FROM log WHERE started = ? AND target = ?',
        [started, target]
    )
    if not cur.fetchone():
        cur.execute(
            'INSERT INTO log (target, started, ended,  duration, is_active) '
            '   VALUES (?, ?, ?, ?, ?)',
            [target, started, ended,  duration, is_active]
        )
        g.db.commit()


if __name__ == '__main__':
    wavelog()
    Gtk.main()
