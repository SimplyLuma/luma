// SPDX-License-Identifier: GPL-3.0-or-later
#include <stdio.h>
#include <assert.h>
static int result;
static int attribute(unsigned slot) { (void)slot; return result; }
static const char *suffix(unsigned slot) { (void)slot; return "_a"; }
struct api { int (*isSlotBootable)(unsigned); int (*isSlotMarkedSuccessful)(unsigned); const char *(*getSuffix)(unsigned); };
static struct api mock = { attribute, attribute, suffix };
static int query(char optflag) { struct api *impl=&mock; unsigned slot=0; int rc; (void)rc; switch(optflag) {
	case 'b':
		rc = impl->isSlotBootable(slot);
		if (rc != 0 && rc != 1) {
			fprintf(stderr, "SLOT %s: Failed to read bootable state\n", impl->getSuffix(slot));
			return 1;
		}
		printf("SLOT %s: is %smarked bootable\n", impl->getSuffix(slot), rc == 1 ? "" : "not ");
		return 0;
	case 'n':
		rc = impl->isSlotMarkedSuccessful(slot);
		if (rc != 0 && rc != 1) {
			fprintf(stderr, "SLOT %s: Failed to read successful state\n", impl->getSuffix(slot));
			return 1;
		}
		printf("SLOT %s: is %smarked successful\n", impl->getSuffix(slot), rc == 1 ? "" : "not ");
		return 0;
default: return 2; } }
int main(void) { for (int i=0;i<2;i++) { char opt=i ? 'n':'b'; result=0; assert(query(opt)==0); result=1; assert(query(opt)==0); result=-1; assert(query(opt)==1); result=2; assert(query(opt)==1); } return 0; }
