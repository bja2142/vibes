#include <stdio.h>
#include <unistd.h>

void win(void) {
    puts("win");
}

void lose(void) {
    puts("lose");
}

int main(void) {
    char buf[4] = {0};
    if (read(0, buf, sizeof(buf)) != sizeof(buf)) {
        lose();
        return 1;
    }
    if (buf[0] == 'C' && buf[1] == 'T' && buf[2] == 'F' && buf[3] == '!') {
        win();
        return 0;
    }
    lose();
    return 1;
}
