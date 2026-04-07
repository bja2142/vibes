/*
 * test_crash_offset — buffer overflow at a known offset.
 *
 * Stack layout (x86_64, no canary, no PIE):
 *   [buf: 64 bytes][saved_rbp: 8 bytes][return_addr: 8 bytes]
 *
 * Sending 72+ bytes overwrites return address.
 * Sending a cyclic pattern lets pwntools find the offset automatically.
 */
#include <stdio.h>
#include <string.h>

void win(void) {
    puts("win!");
}

void vuln(void) {
    char buf[64];
    /* Deliberately unsafe read — for testing only */
    read(0, buf, 256);
}

int main(void) {
    puts("Send your input:");
    fflush(stdout);
    vuln();
    puts("bye");
    return 0;
}
