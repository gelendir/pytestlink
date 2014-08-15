#!/usr/bin/env python
from setuptools import setup

with open('requirements.txt') as f:
    requirements = [l.strip() for l in f]

setup(
    name='PyTestlink',
    version='0.1',
    url='http://github.com/gelendir/pytestlink',
    license='GPL3',
    author='Gregory Eric Sanderson',
    author_email='gregory.eric.sanderson@gmail.com',
    description='Minimal library for accessing a testlink database',
    packages=['testlink'],
    package_dir={'testlink': 'testlink'},
    zip_safe=False,
    include_package_data=True,
    platforms='any',
    install_requires=requirements,
    classifiers=[
        # see http://pypi.python.org/pypi?:action=list_classifiers
        # -*- Classifiers -*-
        'License :: OSI Approved',
        'License :: OSI Approved :: GNU General Public License (GPL)',
        "Programming Language :: Python",
    ]
)