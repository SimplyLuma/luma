# SPDX-License-Identifier: GPL-3.0-or-later
"""Compile the actual native GUID/attribute transition block against GPT entry fixtures.
No block devices are opened. The same upstream macro, constants and transition
statements run here; package and physical switching remain separate gates.
"""
import pathlib, re, subprocess, sys, tempfile
root = pathlib.Path(sys.argv[1])
s = (root / 'bootctrl_impl.c').read_text()
h = (root / 'gpt-utils.h').read_text()
macro = s[s.index('#define UPDATE_SLOT('):s.index('const char *slot_suffix_arr')]
block = s[s.index("\t\tmemset(active_guid, '\\0'"):s.index('\n\t}', s.index('UPDATE_SLOT(pentryB_bak,', s.index('static int boot_ctl_set_active_slot_for_partitions')))]
constants = '\n'.join(line for line in h.splitlines() if re.match(r'#define\s+(TYPE_GUID_SIZE|ATTRIBUTE_FLAG_OFFSET|AB_FLAG_OFFSET|AB_PARTITION_ATTR_SLOT_ACTIVE|AB_PARTITION_ATTR_BOOT_SUCCESSFUL|AB_PARTITION_ATTR_UNBOOTABLE|AB_SLOT_ACTIVE_VAL)\b', line))
c = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <assert.h>
#define SLOT_ACTIVE 1
#define SLOT_INACTIVE 2
#define ATTR_SLOT_ACTIVE 0
''' + constants + '\n' + macro + r'''
static int current;
static int get_partition_attribute(void *disk, const char *name, int attr) {
 (void)disk; (void)attr; return current >= 0 && name[0] == (current ? 'b':'a');
}
static int transition(unsigned slot, uint8_t *pentryA, uint8_t *pentryA_bak, uint8_t *pentryB, uint8_t *pentryB_bak) {
 void *disk = NULL; const char *slotA="a", *slotB="b";
 char active_guid[17], inactive_guid[17];
''' + block + r'''
 return 0;
}
int main(void) {
 for (current=0; current<2; current++) for(unsigned target=0;target<2;target++) {
  uint8_t e[4][128], before[4][128];
  for(int i=0;i<4;i++) {
   memset(e[i], 0x90+i, 128);
   memset(e[i], ((i/2)==current) ? 0xAA:0xBB, 16);
   e[i][AB_FLAG_OFFSET] = ((i/2)==current) ? 0x7F:0x82;
  }
  memcpy(before,e,sizeof e);
  assert(transition(target,e[0],e[1],e[2],e[3])==0);
  for(int i=0;i<4;i++) {
   int selected=(i/2)==(int)target;
   for(int j=0;j<16;j++) assert(e[i][j]==(selected?0xAA:0xBB));
   assert(e[i][AB_FLAG_OFFSET]==(selected?0x3F:(before[i][AB_FLAG_OFFSET]&~4)));
   if(selected) { assert(((e[i][54]>>3)&7)==7); assert(!(e[i][54]&0xC0)); }
   for(int j=16;j<128;j++) if(j!=AB_FLAG_OFFSET) assert(e[i][j]==before[i][j]);
  }
 }
 current=-1; uint8_t e[4][128], before[4][128]; memset(e,0x91,sizeof e); memcpy(before,e,sizeof e);
 assert(transition(0,e[0],e[1],e[2],e[3])==-1); assert(!memcmp(e,before,sizeof e));
 puts("PASS: actual native transition A-to-B, B-to-A, A-to-A, B-to-B; both GPT copies; seven retries; successful/unbootable cleared only target; unrelated bytes unchanged; no-active refusal");
}
'''
with tempfile.TemporaryDirectory(prefix='qbootctl-slot-test-') as tmp:
 p=pathlib.Path(tmp); (p/'test.c').write_text(c)
 subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True)
 subprocess.run([str(p/'test')],check=True)
