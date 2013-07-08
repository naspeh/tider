import argparse
import os
import socket
import sqlite3
import subprocess
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
    g.menu.child_start.connect('activate', lambda x: set_activity(g, True))
    g.menu.child_stop.connect('activate', lambda x: set_activity(g, False))
    g.menu.child_target.connect('activate', change_target, g)

    g.tray.connect('activate', change_target, g)
    g.tray.connect('popup-menu', lambda icon, button, time: (
        g.menu.popup(None, None, icon.position_menu, icon, button, time)
    ))
    GObject.timeout_add(
        g.conf.timeout, lambda: not g.start.value or update_ui(g)
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


def set_activity(g, active, target=None):
    assert active in [False, True]

    if not target:
        target = g.target.value

    if g.start.value and target == g.target.value and active == g.active.value:
        return

    save_log(g)
    g.start.value = time.time()
    g.target.value = target
    g.active.value = active
    update_ui(g)


def change_target(widget, g):
    dialog = Gtk.Dialog('Set activity')
    press_enter = lambda w, e: (
        e.keyval == Gdk.KEY_Return and dialog.response(Gtk.ResponseType.OK)
    )

    table = Gtk.Table(4, 3)
    table.set_col_spacings(6)

    label = Gtk.Label()
    label.set_markup('<b>Name:</b>')
    name = Gtk.Entry()
    name.set_text(g.target.value)
    name.connect('key-press-event', press_enter)
    table.attach(label, 0, 1, 0, 1)
    table.attach(name, 1, 4, 0, 1)

    label = Gtk.Label()
    label.set_markup('<b>Type:</b>')
    working = Gtk.RadioButton.new_from_widget(None)
    working.set_label('working')
    working.connect('key-press-event', press_enter)
    pause = Gtk.RadioButton.new_from_widget(working)
    pause.set_label('break')
    pause.connect('key-press-event', press_enter)
    table.attach(label, 0, 1, 1, 2)
    table.attach(working, 1, 2, 1, 2)
    table.attach(pause, 2, 3, 1, 2)

    label = Gtk.Label()
    label.set_markup('<b>Action:</b>')
    start = Gtk.RadioButton.new_from_widget(None)
    start.set_label('start')
    start.connect('key-press-event', press_enter)
    replace = Gtk.RadioButton.new_from_widget(start)
    replace.set_label('replace')
    replace.connect('key-press-event', press_enter)
    reject = Gtk.RadioButton.new_from_widget(start)
    reject.set_label('reject')
    reject.connect('key-press-event', press_enter)
    if g.start.value:
        table.attach(label, 0, 1, 2, 3)
        table.attach(start, 1, 2, 2, 3)
        table.attach(replace, 2, 3, 2, 3)
        table.attach(reject, 3, 4, 2, 3)

    dialog.get_content_area().add(table)
    dialog.add_buttons(
        Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
        Gtk.STOCK_OK, Gtk.ResponseType.OK
    )
    dialog.show_all()
    response = dialog.run()

    if response == Gtk.ResponseType.OK:
        target = name.get_text().strip()
        active = working.get_active()
        if replace.get_active():
            g.target.value = target
            g.active.value = active
        elif reject.get_active():
            g.start.value = None
        set_activity(g, active, target=target)

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
    stop.set_label('Start break')

    off = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_STOP, None)
    off.set_label('Disable program')

    target = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_OK, None)
    target.set_label('Set activity')
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
    if not g.start.value:
        return ('<b>Wavelog is disabled</b>')

    duration = time.gmtime(time.time() - g.start.value)
    duration = time.strftime('%H hr %M min', duration)
    started = time.strftime('%H:%M:%S', time.localtime(g.start.value))
    if g.active.value:
        result = (
            '<b><big>Working</big></b>\n'
            'target: <b>{target}</b>\n'
            'started at: <b>{started}</b>\n'
            'duration: <b>{duration}</b>'
        ).format(
            target=g.target.value,
            started=started,
            duration=duration
        )
    else:
        result = (
            '<b><big>Pause</big></b>\n'
            'started at: <b>{started}</b>\n'
            'duration: <b>{duration}</b>'
        ).format(
            started=started,
            duration=duration,
        )
    result += '\n=================\n' + get_report(g.conf)
    return result


def update_ui(g):
    duration_sec = 0
    if g.start.value:
        duration_sec = int(time.time() - g.start.value)
    duration = time.gmtime(duration_sec)

    if not g.start.value:
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

    if not g.start.value:
        duration_text = ''
        target_text = 'OFF'
    else:
        target_text = g.target.value
        duration_text = '{}:{:02d}'.format(duration.tm_hour, duration.tm_min)

    max_h = 18
    max_w = int(max_h * 5)
    padding = max_h * 0.125
    box_h = max_h - 2 * padding
    font_h = box_h * 0.77
    font_rgb = (0, 0, 0)
    timer_w = max_h * 1.5
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
    duration_w = int(duration.tm_sec / step_sec) * step_w
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
    db = sqlite3.connect(conf.db_path)
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
                `duration` INTEGER,
                `is_active` INTEGER,
                UNIQUE (target, started)
            )
            '''
        )
        db.commit()
    return db


def save_log(g):
    if not g.start.value:
        return

    cur = g.db.cursor()
    target = g.target.value
    duration = int(time.time() - g.start.value)
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


def do_action(g, action):
    if action == 'target':
        g.menu.child_target.emit('activate')
    elif action == 'toggle-active':
        if not g.start.value:
            return False
        set_activity(g, not g.active.value)
    elif action == 'disable':
        g.menu.child_off.emit('activate')
    elif action == 'quit':
        main_quit(g)


def send_action(conf, action):
    sockfile = conf.sock_path
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sockfile)
    s.send(action.encode())
    s.close()


def get_report(conf, interval=None):
    if not interval:
        interval = [time.strftime('%Y-%m-%d', time.localtime())]
    if len(interval) == 1:
        interval = interval * 2

    db = connect_db(conf)
    cursor = db.cursor()
    duration_sql = lambda is_active: cursor.execute(
        'SELECT target, SUM(duration) FROM log'
        '   WHERE is_active=? AND date(started) BETWEEN date(?) AND date(?)'
        '   GROUP BY target'
        '   ORDER BY 2 DESC',
        [str(1 if is_active else 0)] + interval
    )
    duration_str = lambda v: '{} min {} sec'.format(int(v / 60), v % 60)

    duration_sql(False)
    pauses = cursor.fetchall()
    pauses_dict = dict(pauses)

    duration_sql(True)
    working = cursor.fetchall()
    working_dict = dict(working)

    if interval[0] == interval[1]:
        result = ['Report for {}'.format(interval[0])]
    else:
        result = ['Report from {} to {}'.format(*interval)]

    result += [
        '\n'
        'Total working: {}'.format(duration_str(sum(working_dict.values()))),
        'Total breaks: {}'.format(duration_str(sum(pauses_dict.values()))),
    ]

    if working:
        result += ['\nWorking time with breaks by target:']
        for target, dur in working:
            pause = pauses_dict.pop(target, 0)
            line = '  {}: {}'.format(target, duration_str(dur))
            if pause:
                line += ' (and breaks: {})'.format(duration_str(pause))
            result += [line]

    if pauses_dict:
        result += ['\nBreaks only by target:']
        for target, dur in pauses:
            if target not in pauses_dict:
                continue
            result += ['  {}: {}'.format(target, duration_str(dur))]
    return '\n'.join(result)


def print_report(conf, args):
    interval = None
    if args.interval:
        if len(args.interval) == 2 and args.interval[0] > args.interval[1]:
            raise SystemExit('Wrong interval: second date less than first')
        interval = [time.strftime('%Y-%m-%d', i) for i in args.interval]

    report = get_report(conf, interval)
    print(report)


def main(args):
    if not args:
        wavelog()
        return

    conf = get_config()
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers()

    sub_do = subs.add_parser('do', help='apply action')
    sub_do.add_argument(
        'action', help='choice action',
        choices=['target', 'toggle-active', 'disable', 'quit']
    )
    sub_do.set_defaults(func=lambda: send_action(conf, args.action))

    sub_db = subs.add_parser('db', help='enter to sqlite session')
    sub_db.set_defaults(func=lambda: (
        subprocess.call('sqlite3 {}'.format(conf.db_path), shell=True)
    ))

    sub_report = subs.add_parser('report', aliases=['re'], help='print report')
    sub_report.set_defaults(func=lambda: print_report(conf, args))
    sub_report.add_argument(
        '-i', '--interval',
        help='date interval as "YYYYMMDD" or "YYYYMMDD-YYYYMMDD"',
        type=lambda v: [time.strptime(i, '%Y%m%d') for i in v.split('-', 1)]
    )

    args = parser.parse_args(args)
    try:
        args.func()
    except KeyboardInterrupt:
        raise SystemExit()
