/* SPDX-License-Identifier: MPL-2.0 */
/* Link-time stand-in carrying Debian's soname, so the test program records
 * DT_NEEDED libbz2.so.1.0. It is never loaded. */
const char *BZ2_bzlibVersion(void) { return 0; }
int BZ2_bzBuffToBuffCompress(void) { return -1; }
int BZ2_bzBuffToBuffDecompress(void) { return -1; }
