# SPDX-License-Identifier: Apache-2.0
custom_target('charlie-icon-' + size,
  input: icon_svg,
  output: 'org.projectluma.Charlie.png',
  command: [rsvg_converter, '--width', size.split('x')[0], '--height', size.split('x')[1],
            '--output', '@OUTPUT@', '@INPUT@'],
  install: true,
  install_dir: get_option('datadir') / 'icons/hicolor' / size / 'apps',
)
