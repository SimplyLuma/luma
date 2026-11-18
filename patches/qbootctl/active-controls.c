// SPDX-License-Identifier: GPL-3.0-or-later
#include <stdio.h>
#include <stdint.h>
#include <assert.h>
struct gpt_disk { int dummy; };
#define ATTR_SLOT_ACTIVE 1
static unsigned slots=2;static int values[2];
static unsigned get_number_slots(void){return slots;}
static int get_boot_attr(struct gpt_disk *d,unsigned i,int attr){(void)d;(void)attr;return values[i];}
static void gpt_disk_free(struct gpt_disk *d){(void)d;}
unsigned get_active_boot_slot()
{
	struct gpt_disk disk = { 0 };
	uint32_t num_slots = get_number_slots();

	if (num_slots == 0) {
        fprintf(stderr, "%s: No boot slots available\n", __func__);
        return (unsigned)-1;
    }
    if (num_slots == 1) {
		// Slot 0 is the only slot around.
		return 0;
	}

	for (uint32_t i = 0; i < num_slots; i++) {
		int active = get_boot_attr(&disk, i, ATTR_SLOT_ACTIVE);
        if (active != 0 && active != 1) {
            fprintf(stderr, "%s: Failed to read active boot attribute\n", __func__);
            gpt_disk_free(&disk);
            return (unsigned)-1;
        }
        if (active == 1) {
			gpt_disk_free(&disk);
			return i;
		}
	}

	fprintf(stderr, "%s: Failed to find the active boot slot\n", __func__);
	gpt_disk_free(&disk);
	return (unsigned)-1;
}

int main(void){
values[0]=-1;values[1]=1;assert(get_active_boot_slot()==(unsigned)-1);
values[0]=0;values[1]=-1;assert(get_active_boot_slot()==(unsigned)-1);
values[0]=2;values[1]=1;assert(get_active_boot_slot()==(unsigned)-1);
values[0]=0;values[1]=0;assert(get_active_boot_slot()==(unsigned)-1);
values[0]=1;values[1]=0;assert(get_active_boot_slot()==0);
values[0]=0;values[1]=1;assert(get_active_boot_slot()==1);
slots=0;assert(get_active_boot_slot()==(unsigned)-1);
slots=1;assert(get_active_boot_slot()==0);
return 0;}
