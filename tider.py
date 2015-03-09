import argparse
import datetime as dt
import hashlib
import math
import os
import pickle
import re
import socket
import sqlite3
import subprocess as sp
import sys
import time
from collections import namedtuple
from contextlib import contextmanager
from threading import Thread

from gi.repository import Gdk, Gtk, GObject

GObject.threads_init()

OK = 'OK'
RELOAD = 100
SQL_DATE = '%Y-%m-%d'
DEFAULT_CONFIG = '''
update_period = 1000  # in microseconds
offline_timeout = 300  # in seconds
min_duration = 60  # in seconds
break_symbol = '*'
break_period = 600  # in seconds
work_period = 3000  # in seconds
overwork_period = 300  # in seconds
hide_tray = True
hide_win = False
sqlite_manager = 'sqlite3'


# Update window after creation
def win_hook(win):
    win.move(500, 2)


# Update window text
def text_hook(ctx):
    target = ctx.target if ctx.active else 'OFF'
    label = '{0.duration.h}:{0.duration.m:02d} {1}'.format(ctx, target)

    text = '[{} {}]'.format('☭' if ctx.active else '☯', label)
    color = '#007700' if ctx.active else '#777777'
    markup = '<span color="{}" font="11">{}</span>'.format(color, text)
    return markup
'''.strip()


class Gui:
    def __init__(self, conf):
        if os.path.exists(conf.socket):
            if send_action(conf.socket, 'ping') == OK:
                print('Another `tider` instance already run.')
                raise SystemExit(1)
            else:
                os.remove(conf.socket)

        self.reload = False
        self.conf = conf
        self.state = State(conf)

        self.menu = menu = self.create_menu()
        win = self.create_win(menu) if not conf.hide_win else None
        tray = self.create_tray(menu) if not conf.hide_tray else None

        def update():
            self.state.refresh()
            menu.update()
            if win:
                win.update()
            if tray:
                tray.update()
            return True

        update()
        GObject.timeout_add(conf.update_period, update)

        # Start GTK loop
        server = Thread(target=self.serve, args=(conf.socket,))
        server.daemon = True
        server.start()

        try:
            Gtk.main()
        finally:
            if self.reload:
                print('Tider reloading...')
                raise SystemExit(RELOAD)
            else:
                self.state.disable()
                print('Tider closed.')

    def serve(self, address):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.bind(address)
        s.listen(1)

        while True:
            conn, addr = s.accept()
            while True:
                data = conn.recv(1024)
                if not data:
                    break
                action = data.decode()
                action = getattr(self, 'pub_' + action)
                GObject.idle_add(action)
                conn.send(OK.encode())
        conn.close()

    def create_tray(self, menu):
        tray = Gtk.StatusIcon()

        tray.connect('activate', lambda w: self.pub_target())
        tray.connect('popup-menu', lambda icon, button, time: (
            menu.popup(None, None, icon.position_menu, icon, button, time)
        ))

        def update():
            tray.set_tooltip_markup(self.state.stats)
            if not self.state.start:
                tray.set_from_stock(Gtk.STOCK_MEDIA_STOP)
            elif self.state.active:
                tray.set_from_stock(Gtk.STOCK_MEDIA_PLAY)
            else:
                tray.set_from_stock(Gtk.STOCK_MEDIA_PAUSE)

        tray.update = update
        return tray

    def create_win(self, menu):
        label = Gtk.Label()
        box = Gtk.EventBox()
        box.add(label)

        win = Gtk.Window(title='Tider', type=Gtk.WindowType.POPUP)
        win.set_keep_above(True)
        win.add(box)

        self.conf.win_hook(win)
        win.show_all()

        win.connect('destroy', lambda w: self.pub_quit())
        win.connect('delete_event', lambda w, e: self.pub_quit())
        box.connect('button-press-event', lambda w, e: menu.popup_default(e))

        def update():
            label.set_markup(self.state.text)
            label.set_tooltip_markup(self.state.stats)

        win.update = update
        return win

    def create_menu(self):
        off = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_MEDIA_STOP, None)
        off.set_label('Switch off')
        off.connect('activate', lambda w: self.state.disable())

        target = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_OK, None)
        target.set_label('Set activity')
        target.connect('activate', lambda w: self.pub_target())
        target.show()

        stat = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_PAGE_SETUP, None)
        stat.set_label('Show statistics')
        stat.connect('activate', lambda w: self.pub_report())
        stat.show()

        separator = Gtk.SeparatorMenuItem()
        separator.show()

        quit = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
        quit.connect('activate', lambda w: self.pub_quit())
        quit.show()

        menu = Gtk.Menu()
        items = [target, off, stat, separator, quit]
        for i in items:
            menu.append(i)

        def update():
            if not self.state.start:
                off.hide()
            else:
                off.show()

        def popup_default(e=None):
            if e:
                menu.popup(None, None, None, None, e.button, e.time)
            else:
                scr = menu.get_screen()
                x, y = int(scr.get_width() / 2), int(scr.get_height() / 2)
                menu.popup(None, None, lambda *a: (x, y, True), None, 0, 0)

        menu.update = update
        menu.popup_default = popup_default
        return menu

    def pub_report(self):
        dialog = Gtk.MessageDialog()
        dialog.add_button(Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dialog.set_markup(self.state.stats)

        def update():
            if not dialog.is_visible() or not self.state.start:
                return False

            dialog.set_markup(self.state.stats)
            return True

        GObject.timeout_add(1000, update)
        dialog.run()
        dialog.destroy()

    def get_completion(self):
        db, cursor = self.conf.db()
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

    def pub_target(self):
        dialog = Gtk.Dialog()
        box = dialog.get_content_area()
        press_enter = lambda w, e: (
            e.keyval == Gdk.KEY_Return and dialog.response(Gtk.ResponseType.OK)
        )
        box.connect('key-press-event', press_enter)

        label = Gtk.Label(halign=Gtk.Align.START)
        label.set_markup('<b>Activity:</b>')
        box.pack_start(label, True, True, 6)

        name = Gtk.Entry(completion=self.get_completion())
        name.set_max_length(20)
        name.set_text(self.state.target or 'Enter name...')
        name.connect('key-press-event', press_enter)
        box.add(name)

        start = Gtk.RadioButton.new_from_widget(None)
        start.set_label('start new')
        fix = Gtk.RadioButton.new_from_widget(start)
        fix.set_label('edit current')
        reject = Gtk.RadioButton.new_from_widget(start)
        reject.set_label('reject current')
        off = Gtk.RadioButton.new_from_widget(start)
        off.set_label('turn OFF')
        if self.state.start:
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
            active = not target.endswith(self.conf.break_symbol)
            if not active:
                target = target.rstrip(' ' + self.conf.break_symbol)
            if not target:
                pass
            elif start.get_active():
                self.state.set_activity(active, target=target)
            elif fix.get_active():
                self.state.set_activity(active, target=target, new=False)
            elif reject.get_active():
                self.state.reset()
            elif off.get_active():
                self.state.disable()
            else:
                raise ValueError('wrong state')

        dialog.destroy()

    def pub_menu(self):
        self.menu.popup_default()

    def pub_disable(self):
        self.state.disable()

    def pub_quit(self):
        os.remove(self.conf.socket)
        Gtk.main_quit()

    def pub_reload(self):
        self.reload = True
        self.pub_quit()

    def pub_ping(self):
        pass


class State:
    __slots__ = '_path _data _last_overwork conf text stats'.split()

    def __init__(self, conf):
        self._path = os.path.join(conf.conf_dir, 'last.txt')
        self._data = {
            'target': None,
            'active': False,
            'start': None,
            'last': None
        }
        self._last_overwork = None
        self.conf = conf
        self.text = None
        self.stats = None

        self.load()

    def __getattr__(self, name):
        return self._data[name]

    def update(self, **kwargs):
        # self.load()
        self._data.update(**kwargs)
        with open_via_tmpfile(self._path, mode='wb') as f:
            f.write(pickle.dumps(self._data))

    def load(self):
        state = {}
        if os.path.exists(self._path):
            with open(self._path, 'rb') as f:
                try:
                    state = pickle.load(f)
                except Exception:
                    state = {}
                self._data.update(**state)

    def set_activity(self, active, target=None, new=True):
        if not target:
            target = self.target

        if self.start and target == self.target and active == self.active:
            return

        if new:
            self.save_log()
            self.update(start=time.time(), last=None)

        self.update(target=target, active=active)
        self.refresh()

    def reset(self):
        self.update(start=None, last=None, active=False)
        self.refresh()

    def disable(self):
        self.save_log()
        self.reset()

    def save_log(self):
        if not self.start:
            return

        duration = int(self.last - self.start)
        if duration < self.conf.min_duration:
            return

        db, cur = self.conf.db()
        work_time = duration if self.active else 0
        break_time = 0 if self.active else duration
        cur.execute(
            'SELECT id FROM log WHERE start = ? AND target = ?',
            [self.start, self.target]
        )
        if not cur.fetchone():
            cur.execute(
                'INSERT INTO log (target, start, end,  work, break) '
                '   VALUES (?, ?, ?, ?, ?)',
                [self.target, self.start, self.last, work_time, break_time]
            )
            db.commit()

    def refresh(self):
        if self.last and time.time() - self.last > self.conf.offline_timeout:
            return self.disable()

        self.update(last=time.time())

        # Fill `stats` and `text` fields
        self.stats = self.get_stats()

        last_working = self.get_last_working()
        if self.start:
            duration = self.last - self.start
        elif last_working.ended:
            duration = time.time() - last_working.ended
        else:
            duration = 0
        ctx = dict(self._data, **{
            'duration': split_seconds(duration),
            'stats': self.stats,
            'active': self.active,

            # useful stuff
            'conf': self.conf,
            'open': open_via_tmpfile,
        })
        ctx = namedtuple('Ctx', ctx.keys())(**ctx)
        self.text = self.conf.text_hook(ctx)

        # Handle overwork
        if self.conf.overwork_period and self.active:
            if not last_working.need_break:
                self._last_overwork = None
            else:
                overtime = int(last_working.period - self.conf.work_period)

                timeout = 0
                if self._last_overwork:
                    timeout = time.time() - self._last_overwork

                if timeout and timeout <= self.conf.overwork_period:
                    return

                self._last_overwork = time.time()

                f_seconds = lambda v: '<b>%s</b>' % str_seconds(v)
                message = 'Working: ' + f_seconds(last_working.period)
                if overtime:
                    message += '\nOverworking: ' + f_seconds(overtime)

                cmd = 'notify-send -t %s %s "Take a break!" "%s"' % (
                    int(self.conf.overwork_period * 500),
                    '-u critical' if overtime > self.conf.work_period else '',
                    message
                )
                sp.call(cmd, shell=True)

    def get_stats(self):
        if not self.start:
            status = ('<b>Tider is disabled</b>')
        else:
            status = (
                '<b><big>Currently {state}</big></b>\n'
                '  <b>{target}: {duration}</b> from {started}'
                .format(
                    state='working' if self.active else 'break',
                    target=self.target,
                    started=str_time(self.start),
                    duration=str_seconds(time.time() - self.start)
                )
            )
        result = [status]

        last_w = self.get_last_working()
        if last_w.period:
            last_working = (
                '<b>Last working period</b>\n'
                '  <b>{period}</b>'
                .format(period=str_seconds(last_w.period))
            )
            if self.active:
                last_working += ' from {}'.format(last_w.started_str)
            else:
                last_working += ' till {}'.format(last_w.ended_str)
            if self.active and last_w.need_break:
                last_working += '\n  <b>Need a break!</b>'
            elif not self.active and not last_w.need_break:
                last_working += '\n  <b>Can work again!</b>'
            result += [last_working]

        result += [get_report(self.conf)]
        result = '\n\n'.join(result)
        return result

    def get_last_working(self):
        db, cursor = self.conf.db()
        cursor.execute(
            'SELECT start, end, work FROM log '
            'WHERE'
            '   start > (strftime("%s", "now") - 24 * 60 * 60) '
            '   AND work > 0 '
            'ORDER BY start DESC'
        )
        rows = cursor.fetchall()

        now = time.time()
        if self.active:
            rows.insert(0, (self.start, now, now - self.start))

        if not rows:
            period, need_break = 0, False
            started = ended = None
        else:
            period, need_break = rows[0][2], False
            started, ended = rows[0][0], rows[0][1]
            for i in range(1, len(rows)):
                if rows[i - 1][0] - rows[i][1] > self.conf.break_period:
                    break
                period += rows[i][2]
                started = rows[i][0]

            if self.active or now - rows[0][1] < self.conf.break_period:
                need_break = period > self.conf.work_period
        last = {
            'period': period, 'need_break': need_break,
            'started': started, 'started_str': str_time(started),
            'ended': ended, 'ended_str': str_time(ended)
        }
        return namedtuple('Last', last.keys())(**last)


def get_config():
    conf_dirs = [
        os.path.join(os.path.dirname(__file__), 'var'),
        os.path.join(os.path.expanduser('~'), '.config', 'tider')
    ]
    conf_dir = [p for p in conf_dirs if os.path.exists(p)]
    if conf_dir:
        conf_dir = conf_dir[0]
    else:
        conf_dir = conf_dirs[-1]
        os.mkdir(conf_dir)

    conf_path = os.path.join(conf_dir, 'config.py')
    conf = {}
    exec(DEFAULT_CONFIG, None, conf)
    if os.path.exists(conf_path):
        with open(conf_path, 'rb') as f:
            source = f.read()
        exec(source, None, conf)

    sid = '='.join([conf_dir, os.environ.get('XDG_SESSION_ID')])
    sid = hashlib.md5(sid.encode()).hexdigest()
    conf['socket'] = '/tmp/perevod-%s' % sid
    conf['conf_dir'] = conf_dir
    conf['db_path'] = os.path.join(conf_dir, 'log.db')
    conf['db'] = lambda: connect_db(conf['db_path'])
    return namedtuple('Conf', conf.keys())(**conf)


def connect_db(db_path):
    db = sqlite3.connect(db_path)
    if hasattr(connect_db, 'checked'):
        return db, db.cursor()

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
    connect_db.checked = True
    return db, db.cursor()


@contextmanager
def open_via_tmpfile(filename, suffix='.tmp', mode=None):
    '''Manipulate the tmp file and then quickly rename to `filename`'''
    tmp = filename + suffix
    with open(tmp, mode) as f:
        yield f
    os.rename(tmp, filename)


def split_seconds(v):
    d = {'h': int(v / 60 / 60), 'm': int(v / 60 % 60), 's': int(v % 60)}
    return namedtuple('Duration', d.keys())(**d)


def str_seconds(duration):
    time = split_seconds(duration)
    result = '{}h '.format(time.h) if time.h else ''
    result += '{}m '.format(time.m) if time.h or time.m else ''
    result += '{}s'.format(time.s)
    return result


def str_time(v):
    return time.strftime('%H:%M', time.localtime(v))


def get_report(conf, interval=None, like=None, label=None, one=0, quiet=1):
    if not interval:
        interval = [time.strftime(SQL_DATE)]

    if len(interval) == 1:
        interval = interval * 2

    like = like if like else '%%'

    db, cursor = conf.db()
    cursor.execute(
        'SELECT target, SUM(work) FROM log'
        '   WHERE date(start, "unixepoch", "localtime") BETWEEN ? AND ?' +
        ('  AND target like ?' if like else '') +
        '   GROUP BY target'
        '   ORDER BY 2 DESC',
        interval + [like] if like else []
    )
    rows = cursor.fetchall()

    if not rows and quiet:
        result = []
    elif label:
        result = ['<b>Statistics {}</b>'.format(label)]
    elif interval[0] == interval[1]:
        result = ['<b>Statistics for {}</b>'.format(interval[0])]
    else:
        result = ['<b>Statistics from {} to {}</b>'.format(*interval)]

    get_rest = lambda v: '%s' % str_seconds(v + math.ceil(v / 5))
    if one:
        get_work_n_rest = lambda v: get_rest(v)
    else:
        get_work_n_rest = lambda v: (
            '{} (with rest ~{})'.format(str_seconds(v), get_rest(v))
        )
    if not rows:
        result += [] if quiet else ['  No activities']
    elif len(rows) == 1:
        row = rows[0]
        result += ['  {}: {}'.format(row[0], get_work_n_rest(row[1]))]
    elif len(rows) > 1:
        total = sum(v[1] for v in rows)
        rows += [('total', total)]

        header = ('target', 'work', 'with rest')
        width = max([len(header[0])] + [len(r[0]) for r in rows])

        if one:
            pattern_ = '|{:<%s}|{:>11}|' % width
            line = lambda *a: pattern_.format(a[0], a[2])
        else:
            pattern_ = '|{:<%s}|{:>11}|{:>11}|' % width
            line = lambda *a: pattern_.format(*a)
        sep = line('-' * width, *(['-' * 11] * 2))

        details = [line(*header), sep]
        for target, work_time in rows:
            details += [
                line(target, str_seconds(work_time), get_rest(work_time))
            ]
        details.insert(-1, sep)
        result += ['<tt>{}</tt>'.format('\n'.join(details))]

    result = '\n'.join(result)
    return result


def send_action(address, action):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(address)
    except socket.error:
        return 'Error. No answer'
    s.send(action.encode())
    data = s.recv(1024)
    s.close()
    if data:
        return data.decode()
    return 'Error. Empty answer'


def get_actions():
    return [m[4:] for m in dir(Gui) if m.startswith('pub_')]


def parse_interval(interval):
    def get_named(name):
        named = re.match(r'^(\d*)(w|week|m|month|y|year)$', name.lower())
        if not named:
            return

        count = int(named.group(1) or 1) - 1
        name = named.group(2)

        now = dt.datetime.now()
        if name in ('w', 'week'):
            start = now - dt.timedelta(days=now.weekday())
            if count:
                start -= dt.timedelta(days=count * 7)
        elif name in ('m', 'month'):
            start = now.replace(day=1)
            for i in range(count):
                start = (start - dt.timedelta(days=1)).replace(day=1)
        elif name in ('y', 'year'):
            start = now.replace(day=1, month=1)
            for i in range(count):
                start = (start - dt.timedelta(days=1)).replace(day=1, month=1)
        return [i.timetuple() for i in (start, now)]

    result = get_named(interval)
    if result:
        return result

    formats = {
        '%d': lambda t: '{:02d}%m%Y'.format(t.tm_mday),
        '%d%m': lambda t: '{:02d}{:02d}%Y'.format(t.tm_mday, t.tm_mon),
        '%d%m%Y': None
    }
    for fmt, fix in formats.items():
        try:
            result = [time.strptime(i, fmt) for i in interval.split('-', 1)]
            if fix:
                value = [time.strftime(fix(i)) if fix else i for i in result]
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
    conf = get_config()
    parser = argparse.ArgumentParser(prog='tider')
    cmds = parser.add_subparsers(title='commands')

    def cmd(name, **kw):
        p = cmds.add_parser(name, **kw)
        p.set_defaults(cmd=name)
        p.arg = lambda *a, **kw: p.add_argument(*a, **kw) and p
        p.exe = lambda f: p.set_defaults(exe=f) and p
        return p

    cmd('call', help='call a specific action')\
        .arg('name', choices=get_actions(), help='choice action')\
        .exe(lambda a: print(send_action(conf.socket, a.name)))

    cmd('report', aliases=['re'], help='print report')\
        .arg('-i', '--interval', help=(
            'date in format DD, DDMM, DDMMYYYY or pair via "-" '
            'or /(\d*)(w|week|m|month|y|year)/'
        ))\
        .arg('-d', '--daily', action='store_true', help='daily report')\
        .arg('-w', '--weekly', action='store_true', help='weekly report')\
        .arg('-m', '--monthly', action='store_true', help='monthly report')\
        .arg('-t', '--target', help='filter targets (sqlite like syntax)')\
        .arg('-o', '--one', action='store_true', help='one column')\
        .arg('-q', '--quiet', action='store_true', help='less output')

    cmd('db', help='enter to sqlite session')\
        .arg('--cmd', default=conf.sqlite_manager, help='sqlite manager')\
        .exe(lambda a: sp.call('%s %s' % (a.cmd, conf.db_path), shell=True))

    cmd('conf', help='print default config')\
        .exe(lambda a: print(DEFAULT_CONFIG))

    args = parser.parse_args(args)
    if not hasattr(args, 'cmd'):
        Gui(conf)

    elif hasattr(args, 'exe'):
        args.exe(args)

    elif args.cmd == 'report':
        interval = result = []
        get_report_ = lambda interval, **kw: (get_report(
            conf, interval, args.target, one=args.one, quiet=args.quiet
        ))
        if args.interval:
            interval_ = parse_interval(args.interval)
            interval = [time.strftime(SQL_DATE, i) for i in interval_]
        if len(interval) == 2 and (args.daily or args.weekly or args.monthly):
            strftime = lambda t, f=SQL_DATE: (
                time.strftime(f, time.localtime(t))
            )
            # Is first day in month or week
            is_firstday = lambda t: (
                int(time.strftime('%d', time.localtime(t))) == 1
                if args.monthly else
                int(time.strftime('%w', time.localtime(t))) == 1
            )

            day = 60 * 60 * 24
            begin, end = [time.mktime(i) for i in interval_]
            begin_ = begin
            for i in range(math.ceil((end - begin) / day) + 1):
                cur = begin + i * day
                if args.daily:
                    result += [get_report_([strftime(cur)])]
                else:
                    next_ = begin + day * (i + 1)
                    if is_firstday(next_) or cur >= end:
                        label = None
                        if args.monthly:
                            label = (
                                strftime(cur, 'for %B %Y')
                                if is_firstday(begin_) and is_firstday(next_)
                                else None
                            )
                        int_ = [strftime(begin_), strftime(cur)]
                        result += [get_report_(int_, label=label)]
                        begin_ = next_

        result += [get_report_(interval)]
        result = '\n\n'.join(r for r in result if r)
        result = re.sub(r'<[^>]+>', '', result)
        print(result)

    else:
        raise ValueError('Wrong subcommand')


def tider(args=None):
    if args is None:
        args = sys.argv[1:]

    process_args(args)


if __name__ == '__main__':
    tider()
