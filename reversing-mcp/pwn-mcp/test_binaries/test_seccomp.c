/*
 * test_seccomp — installs a seccomp filter then waits.
 * Filter: allow read, write, exit, exit_group, brk, mmap, munmap.
 *         kill on execve.
 * Used for seccomp-tools analysis tests.
 */
#include <stdio.h>
#include <seccomp.h>
#include <unistd.h>

int main(void) {
    scmp_filter_ctx ctx = seccomp_init(SCMP_ACT_KILL);

    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(read),    0);
    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(write),   0);
    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(exit),    0);
    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(exit_group), 0);
    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(brk),     0);
    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(mmap),    0);
    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(munmap),  0);
    seccomp_rule_add(ctx, SCMP_ACT_ALLOW, SCMP_SYS(fstat),   0);

    seccomp_load(ctx);
    seccomp_release(ctx);

    write(1, "seccomp active\n", 15);
    /* Block here so seccomp-tools can dump the filter */
    char c;
    read(0, &c, 1);
    return 0;
}
