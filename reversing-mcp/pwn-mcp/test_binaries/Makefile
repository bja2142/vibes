CC_X86_64  = gcc
CC_AARCH64 = aarch64-linux-gnu-gcc
CC_MIPSEL  = mipsel-linux-gnu-gcc
CC_RISCV64 = riscv64-linux-gnu-gcc

CFLAGS = -O0 -g -fno-stack-protector -no-pie
BINS_X86_64  = test_hello_x86_64 test_crash_offset_x86_64 test_heap_x86_64 \
               test_format_x86_64 test_seccomp_x86_64 test_network_x86_64
BINS_AARCH64 = test_hello_aarch64 test_crash_offset_aarch64 test_heap_aarch64
BINS_MIPSEL  = test_hello_mipsel test_crash_offset_mipsel test_heap_mipsel
BINS_RISCV64 = test_hello_riscv64 test_crash_offset_riscv64 test_heap_riscv64

all: $(BINS_X86_64) $(BINS_AARCH64) $(BINS_MIPSEL) $(BINS_RISCV64)

x86_64: $(BINS_X86_64)
aarch64: $(BINS_AARCH64)
mipsel: $(BINS_MIPSEL)
riscv64: $(BINS_RISCV64)

# x86_64 targets
test_hello_x86_64: test_hello.c
	$(CC_X86_64) $(CFLAGS) -o $@ $<

test_crash_offset_x86_64: test_crash_offset.c
	$(CC_X86_64) $(CFLAGS) -o $@ $<

test_heap_x86_64: test_heap.c
	$(CC_X86_64) $(CFLAGS) -o $@ $<

test_format_x86_64: test_format.c
	$(CC_X86_64) $(CFLAGS) -o $@ $<

test_seccomp_x86_64: test_seccomp.c
	$(CC_X86_64) $(CFLAGS) -o $@ $< -lseccomp

test_network_x86_64: test_network.c
	$(CC_X86_64) $(CFLAGS) -o $@ $<

# aarch64 targets
test_hello_aarch64: test_hello.c
	$(CC_AARCH64) $(CFLAGS) -o $@ $<

test_crash_offset_aarch64: test_crash_offset.c
	$(CC_AARCH64) $(CFLAGS) -o $@ $<

test_heap_aarch64: test_heap.c
	$(CC_AARCH64) $(CFLAGS) -o $@ $<

# mipsel targets
test_hello_mipsel: test_hello.c
	$(CC_MIPSEL) $(CFLAGS) -static -o $@ $<

test_crash_offset_mipsel: test_crash_offset.c
	$(CC_MIPSEL) $(CFLAGS) -static -o $@ $<

test_heap_mipsel: test_heap.c
	$(CC_MIPSEL) $(CFLAGS) -static -o $@ $<

# riscv64 targets
test_hello_riscv64: test_hello.c
	$(CC_RISCV64) $(CFLAGS) -static -o $@ $<

test_crash_offset_riscv64: test_crash_offset.c
	$(CC_RISCV64) $(CFLAGS) -static -o $@ $<

test_heap_riscv64: test_heap.c
	$(CC_RISCV64) $(CFLAGS) -static -o $@ $<

clean:
	rm -f $(BINS_X86_64) $(BINS_AARCH64) $(BINS_MIPSEL) $(BINS_RISCV64)

.PHONY: all x86_64 aarch64 mipsel riscv64 clean
