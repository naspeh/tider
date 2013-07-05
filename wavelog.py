#!/usr/bin/env python
import os.path
import time

import cairo as C
from gi.repository import Gtk, GObject

DIR = os.path.abspath(os.path.dirname(__file__) + '/var') + '/'


class Wavelog():
    def __init__(self):
        self.start = None
        self.timeout = 1000

        win, img = self.create_win()
        menu = self.create_menu()
        tray = self.create_icon()

        self.update_icon(tray, img)

        tray.connect('activate', self.toggle_win, win)
        tray.connect('popup-menu', self.show_menu, menu)
        GObject.timeout_add(self.timeout, self.update_icon, tray, img)

    def toggle_win(self, widget, win):
        if win.is_visible():
            win.hide()
        else:
            win.show_all()

    def create_win(self):
        img = Gtk.Image()
        vbox = Gtk.VBox()
        vbox.pack_start(img, False, True, 1)

        window = Gtk.Window()
        window.add(vbox)
        window.show_all()

        window.connect('destroy', lambda wid: Gtk.main_quit())
        return window, img

    def create_menu(self):
        about = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_ABOUT, None)
        about.connect('activate', self.show_about)

        quit = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
        quit.connect('activate', Gtk.main_quit)

        menu = Gtk.Menu()
        menu.append(about)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(quit)
        return menu

    def show_menu(self, icon, e_button, e_time, menu):
        menu.show_all()
        menu.popup(None, None, icon.position_menu, icon, e_button, e_time)

    def show_about(self, widget):
        about = Gtk.AboutDialog()
        about.set_destroy_with_parent(True)
        about.set_icon_name('Wavelog')
        about.set_name('Wavelog')
        about.set_version('alfa')
        about.run()
        about.destroy()

    def create_icon(self):
        tray = Gtk.StatusIcon()
        tray.set_from_stock(Gtk.STOCK_YES)
        return tray

    def update_icon(self, tray, img):
        if self.start:
            duration = int(time.time() - self.start)
        else:
            self.start = time.time()
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

        icon_path = DIR + 'example.png'
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
        img.set_from_file(icon_path)
        return True


if __name__ == '__main__':
    Wavelog()
    Gtk.main()
