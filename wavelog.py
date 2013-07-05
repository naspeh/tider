#!/usr/bin/env python
import time
from collections import namedtuple

import cairo as C
from gi.repository import Gtk, GObject

Context = namedtuple('Context', 'conf var win tray menu')
Config = namedtuple('Config', 'app_dir timeout')


def wavelog():
    g = Context(
        conf=Config(timeout=1000, app_dir='./var/'),
        var={'start': None},
        win=create_win(),
        menu=create_menu(),
        tray=create_icon(),
    )

    update_icon(g)

    g.tray.connect('activate', toggle_win, g.win)
    g.tray.connect('popup-menu', show_menu, g.menu)
    GObject.timeout_add(g.conf.timeout, update_icon, g)


def toggle_win(widget, win):
    if win.is_visible():
        win.hide()
    else:
        win.show_all()


def create_win():
    img = Gtk.Image()
    vbox = Gtk.VBox()
    vbox.pack_start(img, False, True, 1)

    win = Gtk.Window()
    win.add(vbox)
    win.show_all()
    win.img = img

    win.connect('destroy', lambda wid: Gtk.main_quit())
    return win


def create_menu():
    about = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_ABOUT, None)
    about.connect('activate', show_about)

    quit = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
    quit.connect('activate', Gtk.main_quit)

    menu = Gtk.Menu()
    menu.append(about)
    menu.append(Gtk.SeparatorMenuItem())
    menu.append(quit)
    return menu


def show_menu(icon, e_button, e_time, menu):
    menu.show_all()
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
    tray.set_from_stock(Gtk.STOCK_YES)
    return tray


def update_icon(g):
    if g.var['start']:
        duration = int(time.time() - g.var['start'])
    else:
        g.var['start']=time.time()
        duration = 0

    if duration > 10:
        Gtk.main_quit()
        return

    max_w = 90
    max_h = 30
    padding = max_h / 5
    box_h = max_h - padding
    box_w = max_w - padding
    font_h = box_h - padding
    font_rgb = (0, 0, 0)
    timer_w = box_w / 3 + padding
    work_rgb = (0.25, 0.7, 0.4)

    icon_path = g.conf.app_dir + 'example.png'
    src = C.ImageSurface(C.FORMAT_ARGB32, max_w, max_h)
    ctx = C.Context(src)

    ctx.set_line_width(0.5)
    ctx.set_source_rgb(*work_rgb)

    ctx.rectangle(0, 0, max_w, max_h)
    ctx.stroke()

    ctx.rectangle(0, 0, timer_w + padding / 2, max_h)
    ctx.fill()

    ctx.set_source_rgb(*font_rgb)
    #ctx.select_font_face('Sans', C.FONT_SLANT_NORMAL, C.FONT_WEIGHT_BOLD)
    ctx.set_font_size(font_h)

    text = str(duration)
    text_w, text_h = ctx.text_extents(text)[2:4]
    ctx.move_to(timer_w - text_w - padding, text_h + padding)
    ctx.show_text(text)

    text = 'prog'
    ctx.move_to(timer_w + padding, text_h + padding)
    ctx.show_text(text)

    #ctx.set_line_width(12)
    #ctx.set_source_rgb(0, 0, 0.7)
    #w = box_w / 10
    #ctx.move_to(padding / 2, box_h)
    #ctx.line_to(padding / 2 + w * duration, box_h)
    #ctx.stroke()

    src.write_to_png(icon_path)
    #tray.set_from_file(icon_path)
    g.win.img.set_from_file(icon_path)
    return True


if __name__ == '__main__':
    wavelog()
    Gtk.main()
