import sys
from distutils.core import setup
from distutils.command.build_py import build_py

if sys.version_info <= (3, 2):
    sys.stderr.write("Tider requires Python 3.2+\n")
    sys.exit(1)

with open('README.rst', 'br') as f:
    desc = f.read().decode()

setup(
    name='tider',
    description=desc.split('\n', 1)[0],
    long_description=desc,
    license='BSD',
    version='beta',
    author='naspeh',
    author_email='naspeh@ya.ru',
    url='http://github.com/naspeh/tider/',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Environment :: X11 Applications :: GTK',
        'Intended Audience :: End Users/Desktop',
        'License :: OSI Approved :: BSD License',
        'Operating System :: Linux',
        'Programming Language :: Python :: 3',
        'Topic :: Office/Business'
    ],
    platforms='any',
    py_modules=['tider'],
    scripts=['tider'],
    cmdclass={'build_py': build_py}
)
