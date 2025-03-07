#!/bin/env python3
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
# See LICENSE for more details.
#
# Copyright: Red Hat Inc. 2017
# Author: Cleber Rosa <crosa@redhat.com>

from setuptools import find_packages, setup

VERSION = open("VERSION", "r").read().strip()

setup(name='avocado-framework-plugin-varianter-yaml-to-mux',
      description='Avocado Varianter plugin to parse YAML file into variants',
      version=VERSION,
      author='Avocado Developers',
      author_email='avocado-devel@redhat.com',
      url='http://avocado-framework.github.io/',
      packages=find_packages(exclude=('tests*',)),
      include_package_data=True,
      install_requires=['avocado-framework==%s' % VERSION, 'PyYAML>=4.2b2'],
      test_suite='tests',
      entry_points={
          "avocado.plugins.init": [
              "yaml_to_mux = avocado_varianter_yaml_to_mux:YamlToMuxInit",
          ],
          "avocado.plugins.cli": [
              "yaml_to_mux = avocado_varianter_yaml_to_mux:YamlToMuxCLI",
          ],
          "avocado.plugins.varianter": [
              "yaml_to_mux = avocado_varianter_yaml_to_mux:YamlToMux"]})
