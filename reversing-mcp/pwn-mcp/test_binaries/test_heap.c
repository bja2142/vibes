/*
 * test_heap — deterministic malloc/free sequence for heap analysis tests.
 *
 * After reaching the pause, the heap state is:
 *   chunk A (32 bytes): allocated, contains "AAAAAAAA..."
 *   chunk B (64 bytes): allocated, contains "BBBBBBBB..."
 *   chunk C (32 bytes): freed → in tcache/fastbin
 *   chunk D (128 bytes): allocated, contains "DDDDDDDD..."
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

int main(void) {
    char *a = malloc(32);
    char *b = malloc(64);
    char *c = malloc(32);
    char *d = malloc(128);

    memset(a, 'A', 31); a[31] = '\0';
    memset(b, 'B', 63); b[63] = '\0';
    memset(c, 'C', 31); c[31] = '\0';
    memset(d, 'D', 127); d[127] = '\0';

    free(c);  /* c → tcache */

    printf("a=%p b=%p d=%p\n", a, b, d);
    puts("heap ready — press enter");
    fflush(stdout);
    getchar();  /* pause here for heap analysis */

    free(a); free(b); free(d);
    return 0;
}
