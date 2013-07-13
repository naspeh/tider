import argparse
import calendar
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
from threading import Thread

import cairo as C
from gi.repository import Gdk, Gtk, GObject

GObject.threads_init()

SQL_DATE = '%Y-%m-%d'
SQL_DATETIME = SQL_DATE + ' %H:%M:%S'
APP_DIRS = [
    os.path.join(os.path.dirname(__file__), 'var'),
    os.path.join(os.path.expanduser('~'), '.config', 'wavelog')
]
DEFAULTS = (
    ('upd_period', ('500', 'int', 'in microseconds')),
    ('off_timeout', ('60', 'int', 'in seconds')),
    ('min_duration', ('20',  'int', 'in seconds')),
    ('break_symbol', ('*', '', '')),
    ('height', ('20', 'int', '')),
    ('width', (None, 'int', '')),
    ('win_hide', ('no', 'boolean', '')),
    ('win_move_x', (None, 'int', '')),
    ('win_move_y', (None, 'int', '')),
    ('tray_hide', ('yes', 'boolean', '')),
)


def get_config(file):
    defaults = dict((k, v[0]) for k, v in DEFAULTS)

    parser = ConfigParser(defaults=defaults)
    if os.path.exists(file):
        parser.read(file)
    else:
        parser.read_dict({'default': {}})

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

    return new_ctx('Conf', **conf)


def get_paths():
    app_dir = APP_DIRS[-1]
    for d in APP_DIRS:
        if os.path.exists(d):
            app_dir = d
            break

    if not os.path.exists(app_dir):
        os.mkdir(app_dir)

    app_dir = app_dir + os.path.sep
    return new_ctx(
        'Paths',
        root=app_dir,
        conf=app_dir + 'config.ini',
        sock=app_dir + 'channel.sock',
        db=app_dir + 'log.db',
        img=app_dir + 'status.png',
        img_tmp=app_dir + 'status-tmp.png',
        stat=app_dir + 'stat.txt',
        last=app_dir + 'last.txt',
    )


def get_context():
    paths = get_paths()
    return new_ctx(
        'Context',
        path=paths,
        conf=get_config(paths.conf),
        db=connect_db(paths.db),
        start=None,
        active=False,
        target=None,
        ui=None,
        tooltip=None
    )


def wavelog():
    g = get_context()

    g.ui = create_ui(g)
    g, last = get_last_state(g)
    if last and time.time() - last > g.conf.off_timeout:
        disable(g, last=last)
    update_ui(g)

    GObject.timeout_add(g.conf.upd_period, lambda: not g.start or update_ui(g))

    server = Thread(target=run_server, args=(g,))
    server.daemon = True
    server.start()

    signal.signal(signal.SIGINT, lambda s, f: Gtk.main_quit())
    try:
        Gtk.main()
    finally:
        disable(g)
        print('Wavelog closed.')


class _Context:
    __slots__ = ()

    def __init__(self, **kw):
        defaults = dict((k, None) for k in self.__slots__)
        defaults.update(kw)
        self._replace(**defaults)

    @property
    def _items(self):
        return [(k, getattr(self, k)) for k in self.__slots__]

    def _replace(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        return self

    def __repr__(self):
        return '{}({})'.format(
            self.__class__.__name__,
            ', '.join('{}={!r}'.format(*r) for r in self._items)
        )


def new_ctx(name, **fields):
    cls = type(name, (_Context, ), {})
    cls.__slots__ = fields.keys()
    return cls(**fields)


def disable(g, last=None):
    save_log(g, last=last)
    g.start = None
    g.active = False
    update_ui(g)


def set_activity(g, active, target=None, new=True):
    assert active in [False, True]

    if not target:
        target = g.target

    if g.start and target == g.target and active == g.active:
        return

    if new:
        save_log(g)
        g.start = time.time()

    g.target = target
    g.active = active
    update_ui(g)


def get_completion(g):
    cursor = g.db.cursor()
    cursor.execute(
        '''
        SELECT DISTINCT target FROM log
            GROUP BY target
            ORDER BY datetime(started) DESC
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

    target = g.target if g.active else g.target + g.conf.break_symbol
    name = Gtk.Entry(completion=get_completion(g))
    name.set_max_length(20)
    name.set_text(target or 'Enter name...')
    name.connect('key-press-event', press_enter)
    note = Gtk.Label(halign=Gtk.Align.END)
    note.set_markup(
        '<small>Use symbol <b>* in the end</b> for a break</small>'
    )
    box.add(name)
    box.pack_start(note, True, True, 3)

    start = Gtk.RadioButton.new_from_widget(None)
    start.set_label('start new')
    fix = Gtk.RadioButton.new_from_widget(start)
    fix.set_label('edit current')
    if g.start:
        box.add(start)
        box.add(fix)

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
            target = target.rstrip(' *')
        if start.get_active():
            set_activity(g, active, target=target)
        elif fix.get_active():
            set_activity(g, active, target=target, new=False)
        else:
            raise ValueError('wrong state')

    dialog.destroy()


def show_report(g):
    dialog = Gtk.MessageDialog()
    dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
    dialog.set_markup(g.tooltip)

    def update():
        if not dialog.is_visible() or not g.start:
            return False

        dialog.set_markup(g.tooltip)
        return True

    GObject.timeout_add(g.conf.upd_period, update)
    dialog.run()
    dialog.destroy()


def create_ui(g):
    menu = create_menu(g)
    win = create_win(g) if not g.conf.win_hide else None
    tray = create_tray(g, menu) if not g.conf.tray_hide else None

    def update():
        menu.update()
        if win:
            win.update()
        if tray:
            tray.update()

    return new_ctx('UI', update=update, popup_menu=menu.popup_default)


def create_tray(g, menu):
    tray = Gtk.StatusIcon()

    tray.connect('activate', lambda w: change_target(g))
    tray.connect('popup-menu', lambda icon, button, time: (
        menu.popup(None, None, icon.position_menu, icon, button, time)
    ))

    def update():
        tray.set_tooltip_markup(g.tooltip)
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
        title='Wavelog', resizable=False, decorated=False,
        skip_pager_hint=True, skip_taskbar_hint=True
    )
    win.set_keep_above(True)
    win.add(box)

    if g.conf.win_move_x is not None or g.conf.win_move_y is not None:
        win.move(g.conf.win_move_x or 0, g.conf.win_move_y or 0)
    win.show_all()

    win.connect('destroy', lambda w: Gtk.main_quit())
    win.connect('delete_event', lambda w, e: Gtk.main_quit())
    box.connect('button-press-event', lambda w, e: g.ui.popup_menu(e))

    def update():
        img.set_from_file(g.path.img)

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
    quit.connect('activate', lambda w: Gtk.main_quit())
    quit.show()

    menu = Gtk.Menu()
    menu.append(target)
    menu.append(start)
    menu.append(stop)
    menu.append(off)
    menu.append(stat)
    menu.append(separator)
    menu.append(quit)

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

    menu.update = update
    menu.popup_default = lambda e: (
        menu.popup(None, None, None, None, e.button, e.time)
        if e else menu.popup(None, None, None, None, 0, 0)
    )
    return menu


def _get_tooltip(g):
    if not g.start:
        result = ('<b>Wavelog is disabled</b>')
    else:
        duration = str_secs(time.time() - g.start)
        started = time.strftime('%H:%M:%S', time.localtime(g.start))
        if g.active:
            result = (
                '<b><big>Currently working</big></b>\n'
                '  target: <b>{target}</b>\n'
                '  started at: <b>{started}</b>\n'
                '  duration: <b>{duration}</b>'
            ).format(
                target=g.target,
                started=started,
                duration=duration
            )
        else:
            result = (
                '<b><big>Currently break</big></b>\n'
                '  started at: <b>{started}</b>\n'
                '  duration: <b>{duration}</b>'
            ).format(
                started=started,
                duration=duration,
            )
    last_working = (
        '<b>Last working period: {}</b>'
        .format(str_secs(get_last_working(g)))
    )
    result = '\n\n'.join([result, last_working, get_report(g)])

    with open(g.path.stat, 'w') as f:
        f.write(result)
    return result


def get_tooltip(g, as_pango=True, load_last=False):
    if load_last:
        g = get_last_state(g)[0]

        if os.path.exists(g.path.stat):
            with open(g.path.stat, 'r') as f:
                result = f.read()
    else:
        result = _get_tooltip(g)

    if not as_pango:
        result = re.sub(r'<[^>]+>', '', result)
    return result


def update_ui(g):
    duration_sec = 0
    if g.start:
        duration_sec = int(time.time() - g.start)
    duration = time.gmtime(duration_sec)

    if not g.start:
        duration_text = ''
        target_text = 'OFF'
    else:
        target_text = g.target
        duration_text = '{}:{:02d}'.format(duration.tm_hour, duration.tm_min)

    max_h = max(12, g.conf.height)
    max_w = int(max_h * 4)
    if g.conf.width:
        max_w = max(max_w, g.conf.width)
    padding = max_h * 0.125
    box_h = max_h - 2 * padding
    font_h = box_h * 0.77
    font_rgb = (0, 0, 0)
    timer_w = max_h * 1.5
    color = (0.6, 0.9, 0.6) if g.active else (0.7, 0.7, 0.7)

    src = C.ImageSurface(C.FORMAT_ARGB32, max_w, max_h)
    ctx = C.Context(src)

    ctx.set_source_rgb(1, 1, 1)
    ctx.rectangle(0, 0, max_w, max_h)
    ctx.fill()

    ctx.set_line_width(1)
    ctx.set_source_rgb(*color)
    ctx.rectangle(0, 0, max_w, max_h)
    ctx.stroke()
    ctx.rectangle(0, 0, timer_w + padding / 2, max_h)
    ctx.fill()

    ctx.set_source_rgb(*font_rgb)
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

    src.write_to_png(g.path.img_tmp)
    os.rename(g.path.img_tmp, g.path.img)
    g.tooltip = get_tooltip(g)
    g.ui.update()

    with open(g.path.last, 'wb') as f:
        f.write(pickle.dumps([g.target, g.active, g.start, time.time()]))
    return True


def get_last_state(g):
    target, active, start, last = None, False, None, None
    if os.path.exists(g.path.last):
        with open(g.path.last, 'rb') as f:
            target, active, start, last = pickle.load(f)

    g.target = target
    g.active = active
    g.start = start
    return g, last


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


def save_log(g, last=None):
    if not g.start:
        return

    if not last:
        last = time.time()

    duration = int(last - g.start)
    if duration < g.conf.min_duration:
        return

    cur = g.db.cursor()
    target = g.target
    started = time.strftime(SQL_DATETIME, time.gmtime(g.start))
    ended = time.strftime(SQL_DATETIME, time.gmtime(last))
    is_active = 1 if g.active else 0
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

    os.remove(g.path.last)


def run_server(g):
    sockfile = g.path.sock
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
        change_target(g)
    elif action == 'toggle-active':
        if not g.start:
            return False
        set_activity(g, not g.active)
    elif action == 'disable':
        disable(g)
    elif action == 'quit':
        Gtk.main_quit()
    elif action == 'menu':
        g.ui.popup_menu(None)


def send_action(g, action):
    sockfile = g.path.sock
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.connect(sockfile)
    s.send(action.encode())
    s.close()


def str_secs(duration):
    hours = int(duration / 60 / 60)
    minutes = int(duration / 60 % 60)
    seconds = int(duration % 60)
    result = '{}h '.format(hours) if hours else ''
    result += '{:02d}m '.format(minutes) if hours or minutes else ''
    result += '{:02d}s'.format(seconds)
    return result


def get_last_working(g):
    cursor = g.db.cursor()
    cursor.execute(
        'SELECT started, ended, duration FROM log'
        '   WHERE date(started)=date(?) AND is_active'
        '   ORDER BY datetime(started) DESC',
        [time.strftime(SQL_DATE, time.gmtime())]
    )

    to_dt = lambda v: calendar.timegm(time.strptime(v, SQL_DATETIME))
    rows = cursor.fetchall()
    if g.active:
        period = time.time() - g.start
    else:
        period = 0

    if not rows:
        return period

    if period and g.start - to_dt(rows[0][1]) > g.conf.off_timeout:
        return period

    period += rows[0][2]
    for i in range(1, len(rows)):
        if to_dt(rows[i-1][0]) - to_dt(rows[i][1]) > g.conf.off_timeout:
            break
        period += rows[i][2]
    return period


def get_report(g, interval=None):
    if not interval:
        interval = [time.localtime()]
    if len(interval) == 1:
        interval = interval * 2

    interval_str = [time.strftime('%x', i) for i in interval]

    interval_utc = [
        time.strftime(SQL_DATE, time.gmtime(time.mktime(i)))
        for i in interval
    ]

    cursor = g.db.cursor()
    duration_sql = lambda is_active: cursor.execute(
        'SELECT target, SUM(duration) FROM log'
        '   WHERE is_active=? AND date(started) BETWEEN date(?) AND date(?)'
        '   GROUP BY target'
        '   ORDER BY 2 DESC',
        [str(1 if is_active else 0)] + interval_utc
    )

    duration_sql(False)
    pauses = cursor.fetchall()
    pauses_dict = dict(pauses)

    duration_sql(True)
    working = cursor.fetchall()
    working_dict = dict(working)

    if interval[0] == interval[1]:
        result = ['<b>Statistics for {}</b>'.format(interval_str[0])]
    else:
        result = ['<b>Statistics from {} to {}</b>'.format(*interval_str)]

    result += [
        '  Total working: {}'.format(str_secs(sum(working_dict.values()))),
        '  Total breaks: {}'.format(str_secs(sum(pauses_dict.values()))),
    ]

    if working:
        result += ['\n  Working time with breaks:']
        for target, dur in working:
            pause = pauses_dict.pop(target, 0)
            line = '    {}: {}'.format(target, str_secs(dur))
            if pause:
                line += ' (and breaks: {})'.format(str_secs(pause))
            result += [line]

    if pauses_dict:
        result += ['\n  Breaks only:']
        for target, dur in pauses:
            if target not in pauses_dict:
                continue
            result += ['    {}: {}'.format(target, str_secs(dur))]

    result = '\n'.join(result)
    return result


def print_report(g, args):
    interval = None
    if args.interval:
        if len(args.interval) == 2 and args.interval[0] > args.interval[1]:
            raise SystemExit('Wrong interval: second date less than first')
        interval = args.interval

    result = get_report(g, interval)
    result = re.sub(r'<[^>]+>', '', result)
    print(result)


def print_xfce4(g, args):
    result = '<img>{}</img>'.format(g.path.img)
    if args.click:
        click = 'python {} do {}'.format(__file__, args.click)
        result += '<click>{}</click>'.format(click)
    if args.tooltip and not args.echo:
        tooltip = get_tooltip(g, as_pango=False, load_last=True)
        result += '<tool>{}</tool>'.format(tooltip)
    if args.echo:
        result = 'echo "{}"'.format(result)
    print(result)


def print_conf():
    result = ['[default]\n']
    for k, v in DEFAULTS:
        line = '{}={}'.format(k, v[0])
        if v[2]:
            line = '# {}\n{}'.format(v[2], line)
        line += '\n\n'
        result.append(line)
    print(''.join(result))


def main(args=None):
    if args is None:
        args = sys.argv[1:]

    if not args:
        wavelog()
        return

    g = get_context()
    parser = argparse.ArgumentParser()
    subs = parser.add_subparsers()

    sub_do = subs.add_parser('do', help='apply action')
    sub_do.add_argument(
        'action', help='choice action',
        choices=['target', 'menu', 'toggle-active', 'disable', 'quit']
    )
    sub_do.set_defaults(func=lambda: send_action(g, args.action))

    sub_db = subs.add_parser('db', help='enter to sqlite session')
    sub_db.set_defaults(func=lambda: (
        subprocess.call('sqlite3 {}'.format(g.path.db), shell=True)
    ))

    sub_report = subs.add_parser('report', aliases=['re'], help='print report')
    sub_report.set_defaults(func=lambda: print_report(g, args))
    sub_report.add_argument(
        '-i', '--interval',
        help='date interval as "YYYYMMDD" or "YYYYMMDD-YYYYMMDD"',
        type=lambda v: [time.strptime(i, '%Y%m%d') for i in v.split('-', 1)]
    )

    sub_xfce4 = subs.add_parser(
        'xfce4', help='command for xfce4-genmon-plugin'
    )
    sub_xfce4.set_defaults(func=lambda: print_xfce4(g, args))
    sub_xfce4.add_argument(
        '-e', '--echo', action='store_true',
        help='simple echo image'
    )
    sub_xfce4.add_argument(
        '-c', '--click', choices=['menu', 'target'],
        help='show (menu|targer dialog) on click'
    )
    sub_xfce4.add_argument(
        '-t', '--tooltip', action='store_true',
        help='show tooltip'
    )

    sub_conf = subs.add_parser('conf', help='print example conf')
    sub_conf.set_defaults(func=print_conf)

    args = parser.parse_args(args)
    try:
        args.func()
    except KeyboardInterrupt:
        raise SystemExit()


if __name__ == '__main__':
    main()
