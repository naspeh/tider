#!/usr/bin/env python
import os.path
import time

import cairo as C
from gi.repository import Gtk, GObject

DIR = os.path.abspath(os.path.dirname(__file__) + '/var') + '/'


class Wavelog(Gtk.StatusIcon):
    def __init__(self):
        super().__init__()
        self.start = None
        self.timeout = 1
        self.width = 20
        self.height = 20

        #self.connect('activate', lambda x: Gtk.main_quit())
        self.connect('popup-menu', self.make_menu)
        GObject.timeout_add_seconds(self.timeout, self.update_icon)
        self.create_icon()

    def make_menu(self, icon, e_button, e_time):
        about = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_ABOUT, None)
        about.connect('activate', self.show_about)

        quit = Gtk.ImageMenuItem.new_from_stock(Gtk.STOCK_QUIT, None)
        quit.connect('activate', Gtk.main_quit)

        menu = Gtk.Menu()
        menu.append(about)
        menu.append(Gtk.SeparatorMenuItem())
        menu.append(quit)
        menu.show_all()
        menu.popup(None, None, self.position_menu, icon, e_button, e_time)

    def show_about(self, widget):
        about = Gtk.AboutDialog()
        about.set_destroy_with_parent(True)
        about.set_icon_name('Wavelog')
        about.set_name('Wavelog')
        about.set_version('alfa')
        about.run()
        about.destroy()

    def update_icon(self):
        self.create_icon()
        return True

    def create_icon(self):
        if self.start:
            duration = int(time.time() - self.start)
        else:
            self.start = time.time()
            duration = 0

        if duration > 10:
            Gtk.main_quit()
            return

        icon_path = DIR + 'example.png'
        img = C.ImageSurface(C.FORMAT_ARGB32, self.width, self.height)
        ctx = C.Context(img)

        padding = 2
        height = self.width - padding
        width = self.height - padding

        ctx.set_line_width(0.5)
        ctx.set_source_rgb(0.3, 0.3, 0.3)
        ctx.rectangle(0, 0, self.width, self.height)
        ctx.stroke()

        ctx.set_source_rgb(0, 0, 0)
        ctx.select_font_face('Sans', C.FONT_SLANT_NORMAL, C.FONT_WEIGHT_BOLD)
        ctx.set_font_size(8)

        text = str(duration)
        text_w, text_h = ctx.text_extents(text)[2:4]
        ctx.move_to(width - text_w - padding, text_h + padding)
        ctx.show_text(text)

        text = 'prog'
        text_w, text_h = ctx.text_extents(text)[2:4]
        ctx.move_to(max(width - text_w - padding, padding), height)
        ctx.show_text(text)

        #ctx.set_line_width(12)
        #ctx.set_source_rgb(0, 0, 0.7)
        #w = width / 10
        #ctx.rectangle(padding / 2, 10, padding / 2 + duration * w, self.height)
        ##ctx.move_to(padding / 2, height)
        ##ctx.line_to(padding / 2 + w * duration, height)
        ##ctx.stroke()
        #ctx.fill()

        img.write_to_png(icon_path)
        self.set_from_file(icon_path)


if __name__ == '__main__':
    Wavelog()
    Gtk.main()
