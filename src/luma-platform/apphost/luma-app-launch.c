// SPDX-License-Identifier: Apache-2.0
// A native, unprivileged client for the per-user Python/GTK app host.
#define _GNU_SOURCE
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <poll.h>
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>
#include <linux/limits.h>
#ifndef LUMA_LAUNCHER_DIR
#define LUMA_LAUNCHER_DIR "/usr/libexec/luma-app-launchers"
#endif
extern char **environ;
static int application_fd = -1;
static void forward(int number) {
    if (application_fd >= 0)
        syscall(SYS_pidfd_send_signal, application_fd, number, NULL, 0);
}
static void quoted(FILE *stream, const char *value) {
    fputc('"', stream);
    for (const unsigned char *p = (const unsigned char *)value; *p; p++) {
        if (*p == '"' || *p == '\\') { fputc('\\', stream); fputc(*p, stream); }
        else if (*p < 32) fprintf(stream, "\\u%04x", *p);
        else fputc(*p, stream);
    }
    fputc('"', stream);
}
static int run_here(const char *program, int count, char **args) {
    char **python = calloc((size_t)count + 4, sizeof(char *));
    if (!python) return 127;
    python[0] = "/usr/bin/python3"; python[1] = "-sP"; python[2] = (char *)program;
    for (int i = 0; i < count; i++) python[i + 3] = args[i];
    // The fallback must not attempt a second handoff through the Python shim.
    setenv("LUMA_APP_HOST", "0", 1);
    execv(python[0], python);
    perror(program); free(python); return 127;
}
int main(int argc, char **argv) {
    const char *base = strrchr(argv[0], '/'); base = base ? base + 1 : argv[0];
    char target[PATH_MAX]; const char *program; int first;
    if (!strcmp(base, "luma-app-launch")) {
        if (argc < 2) { fputs("usage: luma-app-launch PROGRAM [ARGUMENTS]\n", stderr); return 2; }
        program = argv[1]; first = 2;
    } else {
        if (snprintf(target, sizeof target, "%s/%s", LUMA_LAUNCHER_DIR, base) >= (int)sizeof target) return 127;
        program = target; first = 1;
    }
    char absolute[PATH_MAX];
    if (!realpath(program, absolute)) return run_here(program, argc - first, argv + first);
    program = absolute;
    for (int i = first; i < argc; i++)
        if (!strcmp(argv[i], "--agent")) return run_here(program, argc - first, argv + first);
    // A sandbox keeps its own interpreter and permissions, even if it happens
    // to expose the session's acceleration socket.
    if (access("/.flatpak-info", F_OK) == 0) return run_here(program, argc - first, argv + first);
    const char *runtime = getenv("XDG_RUNTIME_DIR"), *disabled = getenv("LUMA_APP_HOST");
    if (!runtime || (disabled && !strcmp(disabled, "0"))) return run_here(program, argc - first, argv + first);
    struct sockaddr_un address = {.sun_family = AF_UNIX};
    if (snprintf(address.sun_path, sizeof address.sun_path, "%s/luma/app-host.sock", runtime) >= (int)sizeof address.sun_path)
        return run_here(program, argc - first, argv + first);
    int connection = socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0), cwd = -1;
    if (connection < 0) return run_here(program, argc - first, argv + first);
    // A wedged accelerator must never add seconds to an ordinary launch.
    struct timeval timeout = {.tv_usec = 150000};
    setsockopt(connection, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof timeout);
    setsockopt(connection, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof timeout);
    if (connect(connection, (struct sockaddr *)&address, sizeof address) < 0) goto fallback;
    cwd = open(".", O_PATH | O_DIRECTORY | O_CLOEXEC);
    if (cwd < 0) goto fallback;
    char *json = NULL; size_t length = 0;
    FILE *stream = open_memstream(&json, &length);
    if (!stream) goto fallback;
    fputs("{\"protocol\":2,\"native\":true,\"safe_path\":true,\"python\":\"/usr/bin/python3\",\"argv\":[", stream);
    quoted(stream, program);
    for (int i = first; i < argc; i++) { fputc(',', stream); quoted(stream, argv[i]); }
    fputs("],\"env\":{", stream);
    int entries = 0;
    for (char **item = environ; *item; item++) {
        char *equal = strchr(*item, '=');
        if (!equal) continue;
        if (entries++) fputc(',', stream);
        char *name = strndup(*item, (size_t)(equal - *item));
        if (!name) { fclose(stream); free(json); goto fallback; }
        quoted(stream, name); free(name); fputc(':', stream); quoted(stream, equal + 1);
    }
    fputs("},\"cgroup\":", stream);
    FILE *groups = fopen("/proc/self/cgroup", "re"); char line[PATH_MAX + 8]; int found = 0;
    if (groups) {
        while (fgets(line, sizeof line, groups)) if (!strncmp(line, "0::", 3)) {
            line[strcspn(line, "\n")] = 0; quoted(stream, line + 3); found = 1; break;
        }
        fclose(groups);
    }
    if (!found) fputs("null", stream);
    fputc('}', stream); fclose(stream);
    if (length > (1 << 20)) { free(json); goto fallback; }
    int fds[] = {STDIN_FILENO, STDOUT_FILENO, STDERR_FILENO, cwd};
    union { struct cmsghdr alignment; char data[CMSG_SPACE(sizeof fds)]; } ancillary = {0};
    struct iovec vector = {.iov_base = json, .iov_len = length};
    struct msghdr message = {.msg_iov = &vector, .msg_iovlen = 1, .msg_control = ancillary.data, .msg_controllen = sizeof ancillary.data};
    struct cmsghdr *control = CMSG_FIRSTHDR(&message);
    control->cmsg_level = SOL_SOCKET; control->cmsg_type = SCM_RIGHTS; control->cmsg_len = CMSG_LEN(sizeof fds);
    memcpy(CMSG_DATA(control), fds, sizeof fds);
    ssize_t sent = sendmsg(connection, &message, MSG_NOSIGNAL); free(json);
    if (sent != (ssize_t)length) goto fallback;
    close(cwd); cwd = -1;
    char reply[256] = {0}; vector.iov_base = reply; vector.iov_len = sizeof reply - 1;
    memset(ancillary.data, 0, sizeof ancillary.data); message.msg_controllen = sizeof ancillary.data;
    ssize_t received = recvmsg(connection, &message, MSG_CMSG_CLOEXEC);
    if (received <= 0) goto fallback;
    for (control = CMSG_FIRSTHDR(&message); control; control = CMSG_NXTHDR(&message, control))
        if (control->cmsg_level == SOL_SOCKET && control->cmsg_type == SCM_RIGHTS) {
            size_t n = (control->cmsg_len - CMSG_LEN(0)) / sizeof(int);
            int *passed = (int *)CMSG_DATA(control);
            for (size_t i = 0; i < n; i++) { if (application_fd < 0) application_fd = passed[i]; else close(passed[i]); }
        }
    if (application_fd < 0 || !strstr(reply, "\"pid\"") || (message.msg_flags & (MSG_TRUNC | MSG_CTRUNC))) goto fallback;
    const char commit[] = "{\"start\":true}";
    if (send(connection, commit, sizeof commit - 1, MSG_NOSIGNAL) != sizeof commit - 1) goto fallback;
    const int signals[] = {SIGTERM, SIGINT, SIGHUP, SIGQUIT, SIGUSR1, SIGUSR2};
    struct sigaction action = {.sa_handler = forward}; sigemptyset(&action.sa_mask);
    for (size_t i = 0; i < sizeof signals / sizeof signals[0]; i++) sigaction(signals[i], &action, NULL);
    struct pollfd waiting[] = {{connection, POLLIN, 0}, {application_fd, POLLIN, 0}};
    int running = 1;
    while (1) {
        int ready = poll(waiting, 2, running ? -1 : 1000);
        if (ready < 0 && errno == EINTR) continue;
        if (ready <= 0) break;
        if (waiting[1].revents) { running = 0; waiting[1].fd = -1; }
        if (waiting[0].revents) {
            memset(reply, 0, sizeof reply); received = recv(connection, reply, sizeof reply - 1, 0);
            char *exit_value = strstr(reply, "\"exit\":");
            if (received > 0 && exit_value) { int status = atoi(exit_value + 7); close(application_fd); close(connection); return status; }
            if (received <= 0) waiting[0].fd = -1;
        }
        if (!running && waiting[0].fd < 0) break;
    }
    close(application_fd); close(connection); return 0;
fallback:
    if (cwd >= 0) close(cwd);
    if (application_fd >= 0) close(application_fd);
    close(connection);
    return run_here(program, argc - first, argv + first);
}
