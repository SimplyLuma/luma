// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Privacy-safe Qualcomm Voice all-call-status probe for the Fairphone 6.
 *
 * Registers only for call-notification events and decodes only TLV 0x01's
 * fixed call-state records. Remote-party-number and every other TLV are
 * ignored, so no phone number or SIP/subscriber identity can reach stdout.
 */

#define _GNU_SOURCE

#include <errno.h>
#include <linux/qrtr.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#ifndef AF_QIPCRTR
#define AF_QIPCRTR 42
#endif

#define QMI_REQUEST 0U
#define QMI_RESPONSE 2U
#define QMI_INDICATION 4U
#define VOICE_INDICATION_REGISTER 0x0003U
#define VOICE_ALL_CALL_STATUS 0x002eU

struct qmi_header {
    uint8_t type;
    uint16_t transaction;
    uint16_t message;
    uint16_t length;
} __attribute__((packed));

static volatile sig_atomic_t stopping;

static uint16_t read_le16(const void *source)
{
    const uint8_t *p = source;
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static void write_le16(void *destination, uint16_t value)
{
    uint8_t *p = destination;
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void stop_handler(int signal_number)
{
    (void)signal_number;
    stopping = 1;
}

static bool response_succeeded(const uint8_t *data, size_t size)
{
    const struct qmi_header *header = (const struct qmi_header *)data;
    const uint8_t *cursor;
    size_t remaining;

    if (size < sizeof(*header) || header->type != QMI_RESPONSE ||
        read_le16(&header->message) != VOICE_INDICATION_REGISTER)
        return false;
    cursor = data + sizeof(*header);
    remaining = size - sizeof(*header);
    while (remaining >= 3) {
        uint16_t length = read_le16(cursor + 1);
        if ((size_t)length + 3U > remaining)
            return false;
        if (cursor[0] == 0x02 && length == 4)
            return read_le16(cursor + 3) == 0 &&
                   read_le16(cursor + 5) == 0;
        cursor += 3U + length;
        remaining -= 3U + length;
    }
    return false;
}

static void inspect_all_call_status(const uint8_t *data, size_t size)
{
    const struct qmi_header *header = (const struct qmi_header *)data;
    const uint8_t *cursor;
    size_t remaining;

    if (size < sizeof(*header) || header->type != QMI_INDICATION ||
        read_le16(&header->message) != VOICE_ALL_CALL_STATUS)
        return;
    cursor = data + sizeof(*header);
    remaining = size - sizeof(*header);
    while (remaining >= 3) {
        uint16_t length = read_le16(cursor + 1);
        const uint8_t *value = cursor + 3;
        if ((size_t)length + 3U > remaining)
            return;
        if (cursor[0] == 0x01 && length >= 1) {
            uint8_t count = value[0];
            if ((size_t)length != 1U + (size_t)count * 7U)
                return;
            for (uint8_t i = 0; i < count; ++i) {
                const uint8_t *call = value + 1U + (size_t)i * 7U;
                printf("event=call-state state=%u type=%u direction=%u mode=%u identifiers_logged=0\n",
                       call[1], call[2], call[3], call[4]);
            }
            fflush(stdout);
            return;
        }
        cursor += 3U + length;
        remaining -= 3U + length;
    }
}

int main(int argc, char **argv)
{
    struct sockaddr_qrtr peer;
    uint8_t request[11] = {0};
    struct qmi_header *header = (struct qmi_header *)request;
    uint8_t buffer[4096];
    unsigned long node;
    unsigned long port;
    int duration;
    int fd;
    time_t deadline;

    if (argc != 4) {
        fprintf(stderr, "usage: %s NODE PORT DURATION\n", argv[0]);
        return 2;
    }
    node = strtoul(argv[1], NULL, 10);
    port = strtoul(argv[2], NULL, 10);
    duration = atoi(argv[3]);
    if (node > UINT32_MAX || port > UINT32_MAX ||
        duration < 1 || duration > 600)
        return 2;

    signal(SIGINT, stop_handler);
    signal(SIGTERM, stop_handler);
    fd = socket(AF_QIPCRTR, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        perror("socket(AF_QIPCRTR)");
        return 1;
    }
    memset(&peer, 0, sizeof(peer));
    peer.sq_family = AF_QIPCRTR;
    peer.sq_node = (uint32_t)node;
    peer.sq_port = (uint32_t)port;
    if (connect(fd, (const struct sockaddr *)&peer, sizeof(peer)) < 0) {
        perror("connect(AF_QIPCRTR)");
        close(fd);
        return 1;
    }

    header->type = QMI_REQUEST;
    write_le16(&header->transaction, 1);
    write_le16(&header->message, VOICE_INDICATION_REGISTER);
    write_le16(&header->length, 4);
    request[7] = 0x13; /* Call Notification Events */
    write_le16(request + 8, 1);
    request[10] = 1;
    if (send(fd, request, sizeof(request), 0) != (ssize_t)sizeof(request)) {
        perror("send indication register");
        close(fd);
        return 1;
    }

    for (;;) {
        struct pollfd pollfd = {.fd = fd, .events = POLLIN};
        int ready = poll(&pollfd, 1, 5000);
        ssize_t received;
        if (ready <= 0) {
            fprintf(stderr, "event=subscription-failed reason=timeout identifiers_logged=0\n");
            close(fd);
            return 1;
        }
        received = recv(fd, buffer, sizeof(buffer), 0);
        if (received < 0) {
            perror("recv subscription response");
            close(fd);
            return 1;
        }
        if (response_succeeded(buffer, (size_t)received))
            break;
    }
    printf("event=subscription-accepted identifiers_logged=0\n");
    fflush(stdout);

    deadline = time(NULL) + duration;
    while (!stopping && time(NULL) < deadline) {
        struct pollfd pollfd = {.fd = fd, .events = POLLIN};
        int remaining = (int)(deadline - time(NULL));
        int timeout = remaining > 1 ? 1000 : remaining * 1000;
        int ready = poll(&pollfd, 1, timeout);
        if (ready < 0) {
            if (errno == EINTR)
                continue;
            perror("poll");
            close(fd);
            return 1;
        }
        if (ready > 0 && (pollfd.revents & POLLIN)) {
            ssize_t received = recv(fd, buffer, sizeof(buffer), 0);
            if (received < 0) {
                if (errno == EINTR)
                    continue;
                perror("recv indication");
                close(fd);
                return 1;
            }
            inspect_all_call_status(buffer, (size_t)received);
        }
    }
    printf("event=complete identifiers_logged=0\n");
    close(fd);
    return 0;
}
