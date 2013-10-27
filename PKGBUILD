# Maintainer: naspeh <naspeh@ya.ru>

pkgname=tider-git
pkgver=alfa
pkgrel=2
pkgdesc='Lightweight GTK+ time tracker'
arch=('any')
url='https://github.com/naspeh/tider'
license=('BSD')
depends=('python-cairo' 'python-gobject' 'gtk3')
makedepends=('git')
provides=('tider')
replace=('tider')
source=('git+https://github.com/naspeh/tider.git')
sha256sums=('SKIP')

build() {
    cd "$srcdir/tider"
    python setup.py build || return 1
    python setup.py install --root=$pkgdir --optimize=1 || return 1
}
