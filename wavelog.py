#!/usr/bin/env python
import argparse
import os
import socket
import sqlite3
import sys
import time
from collections import namedtuple
from threading import Thread

import cairo as C
from gi.repository import Gdk, Gtk, GObject

GObject.threads_init()
Context = namedtuple('Context', 'conf db start active target win tray menu')
Config = namedtuple('Config', 'timeout sock_path db_path img_path')


def get_config():
    app_dir = os.path.join(os.path.dirname(__file__), 'var') + '/'
    return Config(
        timeout=500,
        sock_path=app_dir + 'channel.sock',
        db_path=app_dir + 'log.db',
        img_path=app_dir + 'win.png'
    )


class Variable:
    __slots__ = ('value', )

    def __init__(self, value=None):
        self.value = value

    def __repr__(self):
        return 'Variable({!r})'.format(self.value)


def wavelog():
    conf = get_config()
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

    g.tray.connect('activate', change_target, g)
    g.tray.connect('popup-menu', lambda icon, button, time: (
        g.menu.popup(None, None, icon.position_menu, icon, button, time)
    ))
    GObject.timeout_add(
        g.conf.timeout, lambda: g.start.value is None or update_ui(g)
    )

    update_ui(g)

    server = Thread(target=run_server, args=(g,))
    server.daemon = True
    server.start()

    Gtk.main()


def main_quit(g):
    save_log(g)
    Gtk.main_quit()


def disable(widget, g):
    save_log(g)
    g.start.value = None
    g.active.value = False
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
    return True


def change_target(widget, g):
    dialog = Gtk.Dialog('Enter target of activity')

    entry = Gtk.Entry()
    entry.set_text(g.target.value)
    entry.connect('key-press-event', lambda w, e: (
        e.keyval == Gdk.KEY_Return and dialog.response(Gtk.ResponseType.OK)
    ))

    label = Gtk.Label()
    label.set_markup('<b>Enter target of activity:</b>')
    label.set_justify(Gtk.Justification.LEFT)

    box = dialog.get_content_area()
    box.add(label)
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
    start.set_label('Start activity')

    stop = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_PAUSE, None)
    stop.set_label('Pause')

    off = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_STOP, None)
    off.set_label('Disable')

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


def get_tooltip(g):
    if g.start.value is None:
        return ('<b><big>Wavelog is disabled</big></b>')

    duration = int((time.time() - g.start.value) / 60)
    started = time.strftime('%H:%M:%S', time.gmtime(g.start.value))
    if g.active.value:
        return (
            '<b><big>Working</big></b>\n'
            'target: <b>{target}</b>\n'
            'started: <b>{started}</b>\n'
            'duration: <b>{duration} minutes</b>'
        ).format(
            target=g.target.value,
            started=started,
            duration=duration
        )
    else:
        return (
            '<b><big>Pause</big></b>\n'
            'started: <b>{started}</b>\n'
            'duration: <b>{duration} minutes</b>'
        ).format(
            started=started,
            duration=duration,
        )


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

    if g.start.value is None:
        duration_text = ''
        target_text = 'OFF'
    else:
        duration_text = str(duration['min'])
        target_text = g.target.value

    max_h = 20
    max_w = int(max_h * 4)
    padding = max_h * 0.125
    box_h = max_h - 2 * padding
    font_h = box_h * 0.77
    font_rgb = (0, 0, 0)
    timer_w = max_h * 1.25
    color = (0.6, 0.9, 0.6) if g.active.value else (0.7, 0.7, 0.7)

    icon_path = g.conf.img_path
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

    text_w, text_h = ctx.text_extents(duration_text)[2:4]
    ctx.move_to(timer_w - text_w - padding, font_h + padding)
    ctx.show_text(duration_text)

    ctx.move_to(timer_w + padding, font_h + padding)
    ctx.show_text(target_text)

    line_h = padding * 0.7
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
    g.tray.set_tooltip_markup(get_tooltip(g))
    return True


def connect_db(conf):
    db_path = conf.db_path
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


def run_server(g):
    sockfile = g.conf.sock_path
    if os.path.exists(sockfile):
        os.remove(sockfile)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sockfile)
    s.listen(1)

    while True:
        conn, addr = s.accept()
        while True:
            data = conn.recv(1024)
            GObject.idle_add(do_action, g, data.decode())
            if not data:
                break

    conn.close()


def send_action(action):
    conf = get_config()
    sockfile = conf.sock_path
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sockfile)
    s.send(action.encode())
    s.close()


def do_action(g, action):
    if action == 'target':
        g.menu.child_target.emit('activate')
    elif action == 'toggle-active':
        if g.start.value is None:
            return False
        toggle_active(g, False if g.active.value else True)
    elif action == 'disable':
        g.menu.child_off.emit('activate')
    elif action == 'quit':
        main_quit(g)


def parse_args(args):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        '-a', '--action', help='choice action',
        choices=['target', 'toggle-active', 'disable', 'quit']
    )
    args = parser.parse_args(args)

    if args.action:
        send_action(args.action)


if __name__ == '__main__':
    args = sys.argv[1:]
    if not args:
        wavelog()
    else:
        parse_args(args)
