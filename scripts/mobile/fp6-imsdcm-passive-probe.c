// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Bounded, passive QRTR probe for the Qualcomm IMS data-coordination service.
 *
 * The process publishes IMSDCM briefly and records only QMI framing and TLV
 * shapes.  It never logs TLV values and never sends a QMI response, so APNs,
 * addresses, subscription identifiers, and other carrier data stay private.
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <linux/qrtr.h>
#include <poll.h>
#include <signal.h>
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

#ifndef QRTR_PORT_CTRL
#define QRTR_PORT_CTRL 0xfffffffeU
#endif

#define IMSDCM_SERVICE 0x302U
#define IMSDCM_VERSION 1U
#define IMSDCM_INSTANCE 0U

struct qmi_header {
    uint8_t type;
    uint16_t transaction;
    uint16_t message;
    uint16_t length;
} __attribute__((packed));

static volatile sig_atomic_t stopping;

static uint16_t get_le16(const void *source)
{
    const uint8_t *p = source;
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static void stop_handler(int signal_number)
{
    (void)signal_number;
    stopping = 1;
}

static int send_server_control(int fd, uint32_t command,
                               const struct sockaddr_qrtr *local)
{
    struct qrtr_ctrl_pkt packet;
    struct sockaddr_qrtr destination;

    memset(&packet, 0, sizeof(packet));
    packet.cmd = htole32(command);
    packet.server.service = htole32(IMSDCM_SERVICE);
    packet.server.instance = htole32((IMSDCM_INSTANCE << 8) | IMSDCM_VERSION);
    if (command == QRTR_TYPE_DEL_SERVER) {
        packet.server.node = htole32(local->sq_node);
        packet.server.port = htole32(local->sq_port);
    }

    memset(&destination, 0, sizeof(destination));
    destination.sq_family = AF_QIPCRTR;
    destination.sq_node = local->sq_node;
    destination.sq_port = QRTR_PORT_CTRL;

    return sendto(fd, &packet, sizeof(packet), 0,
                  (const struct sockaddr *)&destination, sizeof(destination));
}

static void describe_qmi(const uint8_t *data, size_t size)
{
    const struct qmi_header *header;
    const uint8_t *cursor;
    size_t remaining;
    unsigned int count = 0;

    if (size < sizeof(*header)) {
        printf("event=short-datagram bytes=%zu\n", size);
        return;
    }

    header = (const struct qmi_header *)data;
    if ((size_t)get_le16(&header->length) + sizeof(*header) != size) {
        printf("event=invalid-qmi-length bytes=%zu declared=%u\n", size,
               get_le16(&header->length));
        return;
    }

    printf("event=qmi-request type=%u message=0x%04x bytes=%zu",
           header->type, get_le16(&header->message), size);

    cursor = data + sizeof(*header);
    remaining = size - sizeof(*header);
    while (remaining >= 3) {
        uint8_t tlv_type = cursor[0];
        uint16_t tlv_length = get_le16(cursor + 1);

        cursor += 3;
        remaining -= 3;
        if (tlv_length > remaining) {
            printf(" malformed_tlv=%u", tlv_type);
            remaining = 0;
            break;
        }
        printf(" tlv%u=%u:%u", count, tlv_type, tlv_length);
        count++;
        cursor += tlv_length;
        remaining -= tlv_length;
    }
    if (remaining)
        printf(" trailing=%zu", remaining);
    printf("\n");
    fflush(stdout);
}

int main(int argc, char **argv)
{
    struct sockaddr_qrtr local;
    socklen_t local_length = sizeof(local);
    int duration = 30;
    int fd;
    time_t deadline;

    if (argc == 2) {
        duration = atoi(argv[1]);
        if (duration < 1 || duration > 120) {
            fprintf(stderr, "duration must be between 1 and 120 seconds\n");
            return 2;
        }
    } else if (argc != 1) {
        fprintf(stderr, "usage: %s [duration-seconds]\n", argv[0]);
        return 2;
    }

    signal(SIGINT, stop_handler);
    signal(SIGTERM, stop_handler);

    fd = socket(AF_QIPCRTR, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    if (fd < 0) {
        perror("socket(AF_QIPCRTR)");
        return 1;
    }

    memset(&local, 0, sizeof(local));
    local.sq_family = AF_QIPCRTR;
    /* The AP is QRTR node 1 on the FP6; port zero requests a dynamic port. */
    local.sq_node = 1;
    local.sq_port = 0;
    if (bind(fd, (const struct sockaddr *)&local, sizeof(local)) < 0) {
        perror("bind(AF_QIPCRTR)");
        close(fd);
        return 1;
    }
    if (getsockname(fd, (struct sockaddr *)&local, &local_length) < 0 ||
        local_length != sizeof(local)) {
        perror("getsockname(AF_QIPCRTR)");
        close(fd);
        return 1;
    }

    if (send_server_control(fd, QRTR_TYPE_NEW_SERVER, &local) < 0) {
        perror("publish IMSDCM");
        close(fd);
        return 1;
    }

    printf("event=published service=%u version=%u instance=%u duration=%d\n",
           IMSDCM_SERVICE, IMSDCM_VERSION, IMSDCM_INSTANCE, duration);
    fflush(stdout);
    deadline = time(NULL) + duration;

    while (!stopping && time(NULL) < deadline) {
        struct pollfd poll_descriptor = { .fd = fd, .events = POLLIN };
        int timeout = (int)((deadline - time(NULL)) * 1000);
        int ready;

        if (timeout > 1000)
            timeout = 1000;
        if (timeout < 0)
            timeout = 0;
        ready = poll(&poll_descriptor, 1, timeout);
        if (ready < 0) {
            if (errno == EINTR)
                continue;
            perror("poll");
            break;
        }
        if (ready > 0 && (poll_descriptor.revents & POLLIN)) {
            uint8_t buffer[4096];
            struct sockaddr_qrtr peer;
            socklen_t peer_length = sizeof(peer);
            ssize_t received = recvfrom(fd, buffer, sizeof(buffer), 0,
                                        (struct sockaddr *)&peer, &peer_length);

            if (received < 0) {
                if (errno == EINTR)
                    continue;
                perror("recvfrom");
                break;
            }
            if (peer.sq_port != QRTR_PORT_CTRL)
                describe_qmi(buffer, (size_t)received);
        }
    }

    if (send_server_control(fd, QRTR_TYPE_DEL_SERVER, &local) < 0)
        perror("unpublish IMSDCM");
    printf("event=unpublished\n");
    close(fd);
    return 0;
}
