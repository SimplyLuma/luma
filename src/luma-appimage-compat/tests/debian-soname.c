/* SPDX-License-Identifier: MPL-2.0 */
/* A program linked the way Debian and Ubuntu link against bzip2: its DT_NEEDED
 * entry is libbz2.so.1.0. It must load through Luma's name for Fedora's
 * libbz2 and compress and decompress through it. */
#include <stdio.h>
#include <string.h>

const char *BZ2_bzlibVersion(void);
int BZ2_bzBuffToBuffCompress(char *dest, unsigned int *destLen, char *source, unsigned int sourceLen,
                             int blockSize100k, int verbosity, int workFactor);
int BZ2_bzBuffToBuffDecompress(char *dest, unsigned int *destLen, char *source, unsigned int sourceLen,
                               int small, int verbosity);

int main(void)
{
    char text[] = "Luma runs AppImages built on Debian and Ubuntu.";
    char packed[256], unpacked[256];
    unsigned int packed_length = sizeof packed, unpacked_length = sizeof unpacked;

    if (BZ2_bzBuffToBuffCompress(packed, &packed_length, text, sizeof text, 9, 0, 0) != 0)
        return 2;
    if (BZ2_bzBuffToBuffDecompress(unpacked, &unpacked_length, packed, packed_length, 0, 0) != 0)
        return 3;
    if (unpacked_length != sizeof text || memcmp(unpacked, text, sizeof text) != 0)
        return 4;
    printf("%s\n", BZ2_bzlibVersion());
    return 0;
}
