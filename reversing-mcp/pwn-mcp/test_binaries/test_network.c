/*
 * test_network — simple TCP echo server.
 * Binds to 127.0.0.1 on a random port, prints the port to stdout,
 * accepts one connection, echoes data back, exits.
 * Used for pwntools interaction tests.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <arpa/inet.h>
#include <sys/socket.h>

int main(void) {
    int srv = socket(AF_INET, SOCK_STREAM, 0);
    int opt = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));

    struct sockaddr_in addr = {
        .sin_family = AF_INET,
        .sin_addr.s_addr = htonl(INADDR_LOOPBACK),
        .sin_port = 0,  /* random port */
    };
    bind(srv, (struct sockaddr*)&addr, sizeof(addr));

    socklen_t len = sizeof(addr);
    getsockname(srv, (struct sockaddr*)&addr, &len);
    printf("PORT %d\n", ntohs(addr.sin_port));
    fflush(stdout);

    listen(srv, 1);
    int cli = accept(srv, NULL, NULL);

    send(cli, "HELLO\n", 6, 0);

    char buf[256];
    ssize_t n;
    while ((n = recv(cli, buf, sizeof(buf)-1, 0)) > 0) {
        buf[n] = '\0';
        if (strncmp(buf, "QUIT", 4) == 0) break;
        send(cli, buf, n, 0);
    }

    close(cli);
    close(srv);
    return 0;
}
