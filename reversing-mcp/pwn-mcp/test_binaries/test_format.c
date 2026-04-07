/*
 * test_format — classic format string vulnerability via printf(buf).
 * For format string detection and exploitation tests.
 */
#include <stdio.h>
#include <string.h>

int secret = 0xdeadbeef;

int main(void) {
    char buf[256];
    puts("Enter format string:");
    fflush(stdout);
    if (fgets(buf, sizeof(buf), stdin) == NULL)
        return 1;
    /* Vulnerable: user controls format string */
    printf(buf);
    fflush(stdout);
    return 0;
}
