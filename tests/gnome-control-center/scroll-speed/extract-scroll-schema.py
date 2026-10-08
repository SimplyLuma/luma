"""Stage Mutter's exact owned schema for Settings' private package checks."""
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
target = 'data/org.projectluma.peripherals.gschema.xml'
section = text.split('diff --git a/' + target + ' b/' + target + '\n')[1].split('\ndiff --git ', 1)[0]
lines = section.splitlines()
content = ''.join(line[1:] + '\n' for line in lines if line.startswith('+') and not line.startswith('+++'))
assert content.startswith('<?xml') and content.endswith('</schemalist>\n')
Path(sys.argv[2]).write_text(content)
