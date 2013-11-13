import argparse
import json
import os
import pickle
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from configparser import ConfigParser
from contextlib import contextmanager
from threading import Thread

import cairo
from gi.repository import Gdk, Gtk, GObject

GObject.threads_init()

RELOAD = 100
SQL_DATE = '%Y-%m-%d'
APP_DIRS = [
    os.path.join(os.path.dirname(__file__), 'var'),
    os.path.join(os.path.expanduser('~'), '.config', 'tider')
]
DEFAULTS = (
    ('update_period', ('500', 'int')),  # in microseconds
    ('offline_timeout', ('300', 'int')),  # in seconds
    ('min_duration', ('60',  'int')),  # in seconds
    ('break_symbol', ('*', '')),
    ('break_period', ('600', 'int')),  # in seconds
    ('work_period', ('3000', 'int')),  # in seconds
    ('overwork_period', ('300', 'int')),  # in seconds
    ('height', ('20', 'int')),
    ('width', (None, 'int')),
    ('font_size', (None, 'int')),
    ('hide_tray', ('yes', 'boolean')),
    ('hide_win', ('no', 'boolean')),
    ('win_move_x', (None, 'int')),
    ('win_move_y', (None, 'int')),
    ('sqlite_manager', ('sqlite3', '')),
    ('xfce_enable', ('no', 'boolean')),
    ('xfce_tooltip', ('yes', 'boolean')),
    ('xfce_click', ('no', 'boolean')),
    ('i3bar_enable', ('no', 'boolean')),
    ('i3bar_tmpl', ('[{symbol} {duration} {target}]', '')),
    ('i3bar_work_rgb', ('#007700', '')),
    ('i3bar_break_rgb', ('#777777', ''))
)

strip_tags = lambda r: re.sub(r'<[^>]+>', '', r)
shell_call = lambda cmd: subprocess.call(cmd, shell=True)


def tider():
    g = get_context()
    if os.path.exists(g.path.sock):
        if send_action(g, 'ping') == 'pong':
            print('Another `tider` instance already run.')
            raise SystemExit(1)
        else:
            os.remove(g.path.sock)

    g.ui = create_ui(g)

    server = Thread(target=run_server, args=(g,))
    server.daemon = True
    server.start()

    signal.signal(signal.SIGINT, lambda s, f: main_quit(g))
    try:
        Gtk.main()
    finally:
        if g.reload:
            print('Tider reloading...')
            raise SystemExit(RELOAD)
        else:
            disable(g)
            print('Tider closed.')


def get_config(file):
    defaults = dict((k, v[0]) for k, v in DEFAULTS)

    parser = ConfigParser(defaults=defaults)
    if os.path.exists(file):
        with open(file) as f:
            src = f.read()
    else:
        src = ''
    src = '[default]\n' + src
    parser.read_string(src)
    parser = parser['default']

    conf = {}
    defaults_dict = dict(DEFAULTS)
    for k, v in parser.items():
        if k not in defaults_dict:
            raise KeyError('Wrong key: ' + k)
        if parser.get(k):
            conf[k] = getattr(parser, 'get' + defaults_dict[k][1])(k)
        else:
            conf[k] = None

    return fix_slots('Conf', conf)


def get_paths():
    app_dir = APP_DIRS[-1]
    for d in APP_DIRS:
        if os.path.exists(d):
            app_dir = d
            break

    if not os.path.exists(app_dir):
        os.mkdir(app_dir)

    app_dir = app_dir + os.path.sep
    return fix_slots('Paths', {
        'conf': app_dir + 'config.ini',
        'sock': app_dir + 'server.sock',
        'db': app_dir + 'log.db',
        'img': app_dir + 'status.png',
        'last': app_dir + 'last.txt',
        'stats': app_dir + 'stats.txt',
        'xfce': app_dir + 'xfce.txt',
        'i3bar': app_dir + 'i3bar.txt'
    })


def get_context():
    paths = get_paths()
    g = fix_slots('Context', {
        'path': paths,
        'conf': get_config(paths.conf),
        'db': connect_db(paths.db),
        'start': None,
        'last': None,
        'active': False,
        'target': None,
        'stats': None,
        'ui': None,
        'reload': False,
        'last_overwork': None,
    })
    g._update(**get_last_state(g))

    update_all(g)
    return g


class _FixedSlots:
    __slots__ = ()

    def __init__(self, **kw):
        defaults = dict((k, None) for k in self.__slots__)
        defaults.update(kw)
        self._update(**defaults)

    @property
    def _items(self):
        return [(k, getattr(self, k)) for k in self.__slots__]

    def _update(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        return self

    def __repr__(self):
        return '{}({})'.format(
            self.__class__.__name__,
            ', '.join('{}={!r}'.format(*r) for r in self._items)
        )


def fix_slots(name, fields):
    cls = type(name, (_FixedSlots, ), {})
    cls.__slots__ = fields.keys()
    return cls(**fields)


def disable(g):
    save_log(g)
    g.start = None
    g.last = None
    g.active = False
    if g.ui:
        g.ui.update()
    else:
        update_all(g)


def main_quit(g, reload=False):
    g.reload = reload
    os.remove(g.path.sock)
    Gtk.main_quit()


def set_activity(g, active, target=None, new=True):
    if not target:
        target = g.target

    if g.start and target == g.target and active == g.active:
        return

    if new:
        save_log(g)
        g.start = time.time()
        g.last = None

    g.target = target
    g.active = active
    update_all(g)


def get_completion(g):
    cursor = g.db.cursor()
    cursor.execute(
        '''
        SELECT DISTINCT target FROM log
            GROUP BY target
            ORDER BY start DESC
            LIMIT 20
        '''
    )
    names = cursor.fetchall()
    liststore = Gtk.ListStore(str)
    for n in names:
        liststore.append(n)
    completion = Gtk.EntryCompletion(model=liststore)
    completion.set_text_column(0)
    completion.set_minimum_key_length(0)
    completion.set_popup_completion(True)
    completion.set_popup_single_match(False)
    completion.set_inline_completion(True)
    completion.set_inline_selection(True)
    return completion


def change_target(g):
    dialog = Gtk.Dialog('Set activity')
    box = dialog.get_content_area()
    press_enter = lambda w, e: (
        e.keyval == Gdk.KEY_Return and dialog.response(Gtk.ResponseType.OK)
    )
    box.connect('key-press-event', press_enter)

    label = Gtk.Label(halign=Gtk.Align.START)
    label.set_markup('<b>Activity:</b>')
    box.pack_start(label, True, True, 6)

    name = Gtk.Entry(completion=get_completion(g))
    name.set_max_length(20)
    name.set_text(g.target or 'Enter name...')
    name.connect('key-press-event', press_enter)
    note = Gtk.Label(halign=Gtk.Align.END)
    note.set_markup(
        '<small>Use symbol <b>{} in the end</b> for a break</small>'
        .format(g.conf.break_symbol)
    )
    box.add(name)
    box.pack_start(note, True, True, 3)

    start = Gtk.RadioButton.new_from_widget(None)
    start.set_label('start new')
    fix = Gtk.RadioButton.new_from_widget(start)
    fix.set_label('edit current')
    reject = Gtk.RadioButton.new_from_widget(start)
    reject.set_label('reject current')
    off = Gtk.RadioButton.new_from_widget(start)
    off.set_label('turn OFF')
    if g.start:
        box.add(start)
        box.add(fix)
        box.add(reject)
        box.add(Gtk.Separator())
        box.add(off)

    dialog.add_buttons(
        Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
        Gtk.STOCK_OK, Gtk.ResponseType.OK
    )
    dialog.show_all()
    response = dialog.run()

    if response == Gtk.ResponseType.OK:
        target = name.get_text().strip()
        active = not target.endswith(g.conf.break_symbol)
        if not active:
            target = target.rstrip(' ' + g.conf.break_symbol)
        if not target:
            pass
        elif start.get_active():
            set_activity(g, active, target=target)
        elif fix.get_active():
            set_activity(g, active, target=target, new=False)
        elif reject.get_active():
            g.start = None
            disable(g)
        elif off.get_active():
            disable(g)
        else:
            raise ValueError('wrong state')

    dialog.destroy()


def show_report(g):
    dialog = Gtk.MessageDialog()
    dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
    dialog.set_markup(g.stats)

    def update():
        if not dialog.is_visible() or not g.start:
            return False

        dialog.set_markup(g.stats)
        return True

    GObject.timeout_add(g.conf.update_period, update)
    dialog.run()
    dialog.destroy()


def create_ui(g):
    menu = create_menu(g)
    win = create_win(g) if not g.conf.hide_win else None
    tray = create_tray(g, menu) if not g.conf.hide_tray else None

    def update():
        update_all(g)
        menu.update()
        if win:
            win.update()
        if tray:
            tray.update()
        return True

    update()
    GObject.timeout_add(g.conf.update_period, lambda: not g.start or update())
    return fix_slots('UI', {
        'update': update, 'popup_menu': menu.popup_default
    })


def create_tray(g, menu):
    tray = Gtk.StatusIcon()

    tray.connect('activate', lambda w: change_target(g))
    tray.connect('popup-menu', lambda icon, button, time: (
        menu.popup(None, None, icon.position_menu, icon, button, time)
    ))

    def update():
        tray.set_tooltip_markup(g.stats)
        if not g.start:
            tray.set_from_stock(Gtk.STOCK_MEDIA_STOP)
        elif g.active:
            tray.set_from_stock(Gtk.STOCK_MEDIA_PLAY)
        else:
            tray.set_from_stock(Gtk.STOCK_MEDIA_PAUSE)

    tray.update = update
    return tray


def create_win(g):
    img = Gtk.Image()
    box = Gtk.EventBox()
    box.add(img)

    win = Gtk.Window(
        title='Tider', resizable=False, decorated=False,
        skip_pager_hint=True, skip_taskbar_hint=True,
        type=Gtk.WindowType.POPUP
    )
    win.set_keep_above(True)
    win.add(box)

    if g.conf.win_move_x is not None or g.conf.win_move_y is not None:
        win.move(g.conf.win_move_x or 0, g.conf.win_move_y or 0)
    win.show_all()

    win.connect('destroy', lambda w: main_quit(g))
    win.connect('delete_event', lambda w, e: main_quit(g))
    box.connect('button-press-event', lambda w, e: g.ui.popup_menu(e))

    def update():
        img.set_from_file(g.path.img)
        img.set_tooltip_markup(g.stats)

    win.update = update
    return win


def create_menu(g):
    start = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_PLAY, None)
    start.set_label('Start working')
    start.connect('activate', lambda w: set_activity(g, True))

    stop = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_PAUSE, None)
    stop.set_label('Start break')
    stop.connect('activate', lambda w: set_activity(g, False))

    off = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_STOP, None)
    off.set_label('Switch off')
    off.connect('activate', lambda w: disable(g))

    target = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_OK, None)
    target.set_label('Set activity')
    target.connect('activate', lambda w: change_target(g))
    target.show()

    stat = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_PAGE_SETUP, None)
    stat.set_label('Show statistics')
    stat.connect('activate', lambda w: show_report(g))
    stat.show()

    separator = Gtk.SeparatorMenuItem()
    separator.show()

    quit = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
    quit.connect('activate', lambda w: main_quit(g))
    quit.show()

    menu = Gtk.Menu()
    items = [target, start, stop, off, stat, separator, quit]
    for i in items:
        menu.append(i)

    def update():
        if not g.start:
            off.hide()
            start.hide()
            stop.hide()
        elif g.active:
            off.show()
            start.hide()
            stop.show()
        else:
            off.show()
            stop.hide()
            start.show()

    def popup_default(e):
        if e:
            menu.popup(None, None, None, None, e.button, e.time)
        else:
            screen = menu.get_screen()
            x, y = int(screen.get_width() / 2), int(screen.get_height() / 2)
            menu.popup(None, None, lambda *a: (x, y, True), None, 0, 0)

    menu.update = update
    menu.popup_default = popup_default
    return menu


def get_stats(g, detailed=True):
    if not g.start:
        status = ('<b>Tider is disabled</b>')
    else:
        status = (
            '<b><big>Currently {state}</big></b>\n'
            '  <b>{target}: {duration}</b> from {started}'
            .format(
                state='working' if g.active else 'break',
                target=g.target,
                started=time.strftime('%H:%M', time.localtime(g.start)),
                duration=str_seconds(time.time() - g.start)
            )
        )
    result = [status]

    last_w = get_last_working(g)
    if last_w.period:
        last_working = (
            '<b>Last working period</b>\n'
            '  <b>{period}</b>'
            .format(period=str_seconds(last_w.period))
        )
        if g.active:
            last_working += ' from {}'.format(
                time.strftime('%H:%M', time.localtime(last_w.started))
            )
        else:
            last_working += ' till {}'.format(
                time.strftime('%H:%M', time.localtime(last_w.ended))
            )
        if g.active and last_w.need_break:
            last_working += '\n  <b>Need a break!</b>'
        elif not g.active and not last_w.need_break:
            last_working += '\n  <b>Can work again!</b>'
        result += [last_working]

    if detailed:
        result += [get_report(g)]
    result = '\n\n'.join(result)
    return result


def update_all(g):
    if g.last and time.time() - g.last > g.conf.offline_timeout:
        return disable(g)
    else:
        g.last = time.time()
    g.stats = get_stats(g)

    duration = split_seconds(int(g.last - g.start) if g.start else 0)
    if not g.start:
        duration_text = ''
        target = 'OFF'
    else:
        target = g.target if g.active else g.target + g.conf.break_symbol
        duration_text = '{}:{:02d}'.format(duration.h, duration.m)

    if g.conf.xfce_enable or not g.conf.hide_win:
        max_h = max(12, g.conf.height)
        max_w = int(max_h * 4)
        if g.conf.width:
            max_w = max(max_w, g.conf.width)
        padding = max_h * 0.125
        box_h = max_h - 2 * padding
        font_h = box_h * 0.77
        if g.conf.font_size:
            font_h = min(font_h, g.conf.font_size)
        timer_w = max_h * 1.5

        if g.start:
            color = (0.6, 0.9, 0.6) if g.active else (0.8, 0.8, 0.83)
            need_break = get_last_working(g).need_break
            text_color = (0.5, 0, 0) if need_break else (0, 0, 0.5)
        else:
            color = (0.7, 0.7, 0.7)
            text_color = (0, 0, 0)

        src = cairo.ImageSurface(cairo.FORMAT_ARGB32, max_w, max_h)
        ctx = cairo.Context(src)

        ctx.set_source_rgb(1, 1, 1)
        ctx.rectangle(0, 0, max_w, max_h)
        ctx.fill()

        ctx.set_line_width(1)
        ctx.set_source_rgb(*color)
        ctx.rectangle(0, 0, max_w, max_h)
        ctx.stroke()
        ctx.rectangle(0, 0, timer_w + padding / 2, max_h)
        ctx.fill()

        ctx.set_source_rgb(*text_color)
        ctx.set_font_size(font_h)

        text_w, text_h = ctx.text_extents(duration_text)[2:4]
        ctx.move_to(timer_w - text_w - padding, font_h + padding)
        ctx.show_text(duration_text)

        ctx.move_to(timer_w + padding, font_h + padding)
        ctx.show_text(target)

        line_h = padding * 0.7
        step_sec = 2
        step_w = timer_w * step_sec / 60
        duration_w = int(duration.s / step_sec) * step_w
        ctx.set_line_width(line_h)
        ctx.set_source_rgb(*text_color)
        ctx.move_to(timer_w, max_h - line_h / 2)
        ctx.line_to(timer_w - duration_w, max_h - line_h / 2)
        ctx.stroke()

        with tmp_file(g.path.img) as filename:
            src.write_to_png(filename)

    with tmp_file(g.path.last, mode='wb') as f:
        f.write(pickle.dumps({
            'target': g.target,
            'active': g.active,
            'start': g.start,
            'last': g.last,
            'last_overwork': g.last_overwork
        }))

    with tmp_file(g.path.stats, mode='w') as f:
        f.write(g.stats)

    if g.conf.xfce_enable:
        prepare_xfce(g)

    if g.conf.i3bar_enable:
        full_text = g.conf.i3bar_tmpl.format(
            symbol='☭' if g.active else '☯',
            duration=duration_text if g.start else 'Tider',
            target=target
        )
        color = g.conf.i3bar_work_rgb if g.active else g.conf.i3bar_break_rgb
        with tmp_file(g.path.i3bar, mode='w') as f:
            f.write(json.dumps({'full_text': full_text, 'color': color}))

    if g.conf.overwork_period and g.active:
        last_working = get_last_working(g)
        if not last_working.need_break:
            g.last_overwork = None
        else:
            overtime = int(last_working.period - g.conf.work_period)
            timeout = time.time() - g.last_overwork if g.last_overwork else 0
            if not timeout or timeout >= g.conf.overwork_period:
                g.last_overwork = time.time()
                f_seconds = lambda v: '<b>{}</b>'.format(str_seconds(v))
                message = 'Working: ' + f_seconds(last_working.period)
                if overtime:
                    message += '\nOverworking: ' + f_seconds(overtime)
                shell_call(
                    'notify-send -t {} "Take a break!" "{}"'
                    .format(int(g.conf.overwork_period * 500), message)
                )
    return True


def get_last_state(g):
    last_state = {}
    if os.path.exists(g.path.last):
        with open(g.path.last, 'rb') as f:
            state = pickle.load(f)
            last_state.update(state)

    if os.path.exists(g.path.stats):
        with open(g.path.stats, 'r') as f:
            last_state['stats'] = f.read()

    return last_state


def connect_db(db_path):
    db = sqlite3.connect(db_path)
    cur = db.cursor()
    cur.execute(
        'SELECT name FROM sqlite_master WHERE type="table" AND name="log"'
    )
    if not cur.fetchone():
        cur.execute(
            '''
            CREATE TABLE `log`(
                `id` INTEGER PRIMARY KEY,
                `target` TEXT NOT NULL,
                `start` INTEGER NOT NULL,
                `end` INTEGER,
                `work` INTEGER,
                `break` INTEGER,
                UNIQUE (target, start)
            )
            '''
        )
        cur.execute(
            '''
            CREATE VIEW `log_pretty` AS
            SELECT
                id, target, work / 60 AS work_m, break / 60 AS break_m,
                start, datetime(start, 'unixepoch', 'localtime') AS start_str,
                end, datetime(end, 'unixepoch', 'localtime') AS end_str
            FROM `log` WHERE work_m > 0 OR break_m > 0
            '''
        )
        db.commit()
    return db


def save_log(g):
    if not g.start:
        return

    duration = int(g.last - g.start)
    if duration < g.conf.min_duration:
        return

    cur = g.db.cursor()
    work_time = duration if g.active else 0
    break_time = 0 if g.active else duration
    cur.execute(
        'SELECT id FROM log WHERE start = ? AND target = ?',
        [g.start, g.target]
    )
    if not cur.fetchone():
        cur.execute(
            'INSERT INTO log (target, start, end,  work, break) '
            '   VALUES (?, ?, ?, ?, ?)',
            [g.target, g.start, g.last, work_time, break_time]
        )
        g.db.commit()


def run_server(g):
    sockfile = g.path.sock
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sockfile)
    s.listen(1)

    while True:
        conn, addr = s.accept()
        while True:
            data = conn.recv(1024)
            if not data:
                break
            action = data.decode()
            if action == 'ping':
                conn.send('pong'.encode())
            else:
                action = run_server.actions.get(data.decode())
                GObject.idle_add(action, g)
                conn.send('ok'.encode())
    conn.close()

run_server.actions = {
    'target': lambda g: change_target(g),
    'toggle-active': lambda g: g.start and set_activity(g, not g.active),
    'menu': lambda g: g.ui.popup_menu(None),
    'disable': lambda g: disable(g),
    'reload': lambda g: main_quit(g, reload=True),
    'quit': lambda g: main_quit(g),
    'report': lambda g: show_report(g),
    'ping': None
}


def send_action(g, action):
    sockfile = g.path.sock
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(sockfile)
    except socket.error:
        return None
    s.send(action.encode())
    data = s.recv(1024)
    s.close()
    if data:
        return data.decode()
    return True


@contextmanager
def tmp_file(filename, suffix='.tmp', mode=None):
    '''Manipulate the tmp file and then quickly rename to `filename`'''
    tmp = filename + suffix
    if mode:
        with open(tmp, mode) as f:
            yield f
    else:
        yield tmp
    os.rename(tmp, filename)


def split_seconds(v):
    return fix_slots('Duration', {
        'h': int(v / 60 / 60), 'm': int(v / 60 % 60), 's': int(v % 60)
    })


def str_seconds(duration):
    time = split_seconds(duration)
    result = '{}h '.format(time.h) if time.h else ''
    result += '{}m '.format(time.m) if time.h or time.m else ''
    result += '{}s'.format(time.s)
    return result


def get_last_working(g):
    cursor = g.db.cursor()
    cursor.execute(
        'SELECT start, end, work FROM log'
        '   WHERE start > (strftime("%s", "now") - 24 * 60 * 60) AND work > 0'
        '   ORDER BY start DESC'
    )
    rows = cursor.fetchall()

    now = time.time()
    if g.active:
        rows.insert(0, (g.start, now, now - g.start))

    if not rows:
        period, need_break = 0, False
        started = ended = None
    else:
        period, need_break = rows[0][2], False
        started, ended = rows[0][0], rows[0][1]
        for i in range(1, len(rows)):
            if rows[i-1][0] - rows[i][1] > g.conf.break_period:
                break
            period += rows[i][2]
            started = rows[i][0]

        if g.active or now - rows[0][1] < g.conf.break_period:
            need_break = period > g.conf.work_period
    return fix_slots('Last', {
        'period': period, 'need_break': need_break,
        'started': started, 'ended': ended
    })


def get_report(g, interval=None):
    if not interval:
        interval = [time.strftime(SQL_DATE)]

    if len(interval) == 1:
        interval = interval * 2

    cursor = g.db.cursor()
    cursor.execute(
        'SELECT target, SUM(work), SUM(break) FROM log'
        '   WHERE date(start, "unixepoch", "localtime") BETWEEN ? AND ?'
        '   GROUP BY target'
        '   ORDER BY 2 DESC',
        interval
    )
    rows = cursor.fetchall()

    if interval[0] == interval[1]:
        result = ['<b>Statistics for {}</b>'.format(interval[0])]
    else:
        result = ['<b>Statistics from {} to {}</b>'.format(*interval)]

    if not rows:
        result += ['  No activities']
    elif len(rows) == 1:
        row = rows[0]
        line = '  {}: {}'.format(row[0], str_seconds(row[1]))
        if row[2]:
            line += ' (and breaks {})'.format(str_seconds(row[2]))
        result += [line]
    elif len(rows) > 1:
        details = []
        total = lambda index: str_seconds(sum(v[index] for v in rows))
        header = ('target', 'work', 'break')
        width = max([len(header[0])] + [len(r[0]) for r in rows])
        pattern = '|{:<%s}|{:>11}|{:>11}|' % width
        separator = '|%s|' % '+'.join(['-' * width] + ['-' * 11] * 2)
        details += [pattern.format(*header), separator]
        for target, work_time, break_time in rows:
            details += [pattern.format(
                target, str_seconds(work_time), str_seconds(break_time)
            )]
        details += [separator, pattern.format('total', total(1), total(2))]
        result += ['<tt>{}</tt>'.format('\n'.join(details))]

    result = '\n'.join(result)
    return result


def prepare_xfce(g):
    result = '<img>{}</img>'.format(g.path.img)
    if g.conf.xfce_click:
        click = 'python {} do {}'.format(__file__, g.conf.xfce_click)
        result += '<click>{}</click>'.format(click)
    if g.conf.xfce_tooltip:
        tooltip = strip_tags(get_stats(g, detailed=False))
        result += '<tool>{}</tool>'.format(tooltip)

    with tmp_file(g.path.xfce, mode='w') as f:
        f.write(result)


def parse_interval(interval):
    result = None
    for prefix in ['', '%m%Y', '%Y']:
        try:
            value = [time.strftime(i + prefix) for i in interval.split('-', 1)]
            result = [time.strptime(i, '%d%m%Y') for i in value]
            break
        except ValueError:
            pass

    if not result:
        raise SystemExit('Wrong interval format')
    if len(result) == 2 and result[0] > result[1]:
        raise SystemExit('Wrong interval: second date less than first')
    return result


def process_args(args):
    g = get_context()
    parser = argparse.ArgumentParser()
    cmds = parser.add_subparsers(title='commands')

    def cmd(name, **kw):
        p = cmds.add_parser(name, **kw)
        p.set_defaults(cmd=name)
        p.arg = lambda *a, **kw: p.add_argument(*a, **kw) and p
        p.exe = lambda f: p.set_defaults(exe=f) and p
        return p

    cmd('call', help='call a specific action')\
        .arg('name', choices=run_server.actions.keys(), help='choice action')\
        .exe(lambda a: print(send_action(g, a.name)))

    cmd('report', aliases=['re'], help='print report')\
        .arg('-i', '--interval', help='DD, DDMM, DDMMYYYY or pair via "-"')\
        .arg('-d', '--daily', action='store_true', help='daily report')\
        .arg('-w', '--weekly', action='store_true', help='weekly report')\
        .arg('-m', '--monthly', action='store_true', help='monthly report')

    cmd('db', help='enter to sqlite session')\
        .arg('--cmd', default=g.conf.sqlite_manager, help='sqlite manager')\
        .exe(lambda a: shell_call('{} {}'.format(a.cmd, g.path.db)))

    cmd('get', help='print examples')\
        .arg('name', help='choice name', choices=['conf', 'xfce', 'i3bar'])

    args = parser.parse_args(args)
    if hasattr(args, 'exe'):
        args.exe(args)

    elif args.cmd == 'report':
        interval = result = []
        if args.interval:
            interval_ = parse_interval(args.interval)
            interval = [time.strftime(SQL_DATE, i) for i in interval_]
        if len(interval) == 2 and (args.daily or args.weekly or args.monthly):
            day = 60 * 60 * 24
            begin, end = [time.mktime(i) for i in interval_]
            begin_ = begin
            for i in range(int((end - begin) / day) + 1):
                cur = begin + i * day
                if args.monthly or args.weekly:
                    next_ = begin + day * (i + 1)
                    check = lambda: (
                        int(time.strftime('%d', time.localtime(next_))) == 1
                        if args.monthly else
                        int(time.strftime('%w', time.localtime(next_))) == 1
                    )
                    if check() or next_ > end:
                        interval_ = [
                            time.strftime(SQL_DATE, time.localtime(begin_)),
                            time.strftime(SQL_DATE, time.localtime(cur))
                        ]
                        begin_ = next_
                        result += [get_report(g, interval_)]
                else:
                    result += [get_report(g, [time.strftime(SQL_DATE, cur)])]

        result += [get_report(g, interval)]
        result = '\n\n'.join(result)
        result = re.sub(r'<[^>]+>', '', result)
        print(result)

    elif args.cmd == 'get':
        if args.name == 'xfce':
            print(
                'Add "xfce_enable=yes" to config.\n'
                'Command for xfce4-genmon-plugin:\n'
                '$ cat {}'.format(g.path.xfce)
            )
        elif args.name == 'i3bar':
            print(
                'Add "i3bar_enable=yes" to config.\n'
                'Command for i3bar:\n'
                '$ cat {}'.format(g.path.i3bar)
            )
        elif args.name == 'conf':
            result = []
            for k, v in DEFAULTS:
                line = '{}={}'.format(k, v[0] if v[0] else '')
                result.append(line)
            print('\n'.join(result))
    else:
        raise ValueError('Wrong subcommand')


def main(args=None):
    if args is None:
        args = sys.argv[1:]

    if not args:
        return tider()

    try:
        process_args(args)
    except KeyboardInterrupt:
        raise SystemExit()


if __name__ == '__main__':
    main()
