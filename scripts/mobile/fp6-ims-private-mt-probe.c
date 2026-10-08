// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Privacy-preserving probe for Qualcomm IMS Private MT-INVITE indications.
 * It subscribes to indication 0x3f and reports only its arrival; ICCID and SIP
 * header values are never decoded, printed, or persisted.
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
#ifndef QRTR_PORT_CTRL
#define QRTR_PORT_CTRL 0xfffffffeU
#endif

#define IMS_PRIVATE_SERVICE 0x4dU
#define IMS_PRIVATE_VERSION 1U
#define IMS_PRIVATE_INSTANCE 0U
#define IMS_PRIVATE_SUBSCRIBE 0x003eU
#define IMS_PRIVATE_MT_INVITE 0x003fU
#define QMI_REQUEST 0U
#define QMI_RESPONSE 2U
#define QMI_INDICATION 4U

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

static int send_lookup(int fd, const struct sockaddr_qrtr *local,
                       uint32_t command)
{
    struct qrtr_ctrl_pkt packet;
    struct sockaddr_qrtr destination;

    memset(&packet, 0, sizeof(packet));
    packet.cmd = htole32(command);
    packet.server.service = htole32(IMS_PRIVATE_SERVICE);
    packet.server.instance =
        htole32((IMS_PRIVATE_INSTANCE << 8) | IMS_PRIVATE_VERSION);
    if (command == QRTR_TYPE_DEL_LOOKUP) {
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

static bool is_private_server(const struct qrtr_ctrl_pkt *packet, size_t size,
                              struct sockaddr_qrtr *server)
{
    uint32_t encoded_instance;

    if (size < sizeof(*packet) || le32toh(packet->cmd) != QRTR_TYPE_NEW_SERVER ||
        le32toh(packet->server.service) != IMS_PRIVATE_SERVICE)
        return false;
    encoded_instance = le32toh(packet->server.instance);
    if ((encoded_instance & 0xffU) != IMS_PRIVATE_VERSION ||
        (encoded_instance >> 8) != IMS_PRIVATE_INSTANCE)
        return false;
    memset(server, 0, sizeof(*server));
    server->sq_family = AF_QIPCRTR;
    server->sq_node = le32toh(packet->server.node);
    server->sq_port = le32toh(packet->server.port);
    return true;
}

static int send_subscription(int fd, const struct sockaddr_qrtr *server)
{
    uint8_t request[sizeof(struct qmi_header) + 4];
    struct qmi_header *header = (struct qmi_header *)request;

    memset(request, 0, sizeof(request));
    header->type = QMI_REQUEST;
    write_le16(&header->transaction, 1);
    write_le16(&header->message, IMS_PRIVATE_SUBSCRIBE);
    write_le16(&header->length, 4);
    request[sizeof(*header)] = 0x10;
    write_le16(request + sizeof(*header) + 1, 1);
    request[sizeof(*header) + 3] = 1;
    return sendto(fd, request, sizeof(request), 0,
                  (const struct sockaddr *)server, sizeof(*server));
}

static int classify_qmi(const uint8_t *data, size_t size, bool *subscribed,
                        unsigned int *invite_count)
{
    const struct qmi_header *header;
    uint16_t message;

    if (size < sizeof(*header))
        return -1;
    header = (const struct qmi_header *)data;
    if ((size_t)read_le16(&header->length) + sizeof(*header) != size)
        return -1;
    message = read_le16(&header->message);

    if (header->type == QMI_RESPONSE && message == IMS_PRIVATE_SUBSCRIBE) {
        const uint8_t *payload = data + sizeof(*header);
        size_t remaining = size - sizeof(*header);

        while (remaining >= 3) {
            uint16_t length = read_le16(payload + 1);
            if ((size_t)length + 3U > remaining)
                return -1;
            if (payload[0] == 0x02 && length == 4) {
                uint16_t result = read_le16(payload + 3);
                uint16_t error = read_le16(payload + 5);
                if (result != 0 || error != 0) {
                    fprintf(stderr,
                            "event=subscription-rejected result=%u error=%u\n",
                            result, error);
                    return -1;
                }
                *subscribed = true;
                printf("event=subscription-accepted\n");
                fflush(stdout);
                return 0;
            }
            payload += 3U + length;
            remaining -= 3U + length;
        }
        return -1;
    }

    if (header->type == QMI_INDICATION && message == IMS_PRIVATE_MT_INVITE) {
        (*invite_count)++;
        printf("event=mt-invite-received count=%u\n", *invite_count);
        fflush(stdout);
        return 0;
    }

    printf("event=other-qmi type=%u message=0x%04x bytes=%zu\n",
           header->type, message, size);
    fflush(stdout);
    return 0;
}

int main(int argc, char **argv)
{
    struct sockaddr_qrtr local;
    struct sockaddr_qrtr server;
    socklen_t local_length = sizeof(local);
    int duration = 120;
    int fd;
    int exit_status = 0;
    time_t discovery_deadline;
    time_t deadline;
    bool found = false;
    bool subscribed = false;
    unsigned int invite_count = 0;

    if (argc == 2) {
        duration = atoi(argv[1]);
        if (duration < 1 || duration > 600) {
            fprintf(stderr, "duration must be between 1 and 600 seconds\n");
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
    local.sq_node = 1;
    local.sq_port = 0;
    if (bind(fd, (const struct sockaddr *)&local, sizeof(local)) < 0 ||
        getsockname(fd, (struct sockaddr *)&local, &local_length) < 0 ||
        local_length != sizeof(local)) {
        perror("bind/getsockname(AF_QIPCRTR)");
        close(fd);
        return 1;
    }
    if (send_lookup(fd, &local, QRTR_TYPE_NEW_LOOKUP) < 0) {
        perror("lookup IMS Private");
        close(fd);
        return 1;
    }

    discovery_deadline = time(NULL) + 5;
    while (!stopping && time(NULL) < discovery_deadline && !found) {
        uint8_t buffer[4096];
        struct sockaddr_qrtr peer;
        socklen_t peer_length = sizeof(peer);
        struct pollfd descriptor = { .fd = fd, .events = POLLIN };
        int ready = poll(&descriptor, 1, 1000);

        if (ready < 0 && errno == EINTR)
            continue;
        if (ready <= 0)
            continue;
        ssize_t received = recvfrom(fd, buffer, sizeof(buffer), 0,
                                    (struct sockaddr *)&peer, &peer_length);
        if (received >= 0 && peer.sq_port == QRTR_PORT_CTRL)
            found = is_private_server((const struct qrtr_ctrl_pkt *)buffer,
                                      (size_t)received, &server);
    }
    if (!found) {
        fprintf(stderr, "event=service-not-found\n");
        exit_status = 1;
        goto cleanup;
    }
    printf("event=service-found\n");
    if (send_subscription(fd, &server) < 0) {
        perror("subscribe IMS Private");
        exit_status = 1;
        goto cleanup;
    }

    deadline = time(NULL) + duration;
    while (!stopping && time(NULL) < deadline) {
        uint8_t buffer[32768];
        struct sockaddr_qrtr peer;
        socklen_t peer_length = sizeof(peer);
        struct pollfd descriptor = { .fd = fd, .events = POLLIN };
        int ready = poll(&descriptor, 1, 1000);

        if (ready < 0) {
            if (errno == EINTR)
                continue;
            perror("poll");
            exit_status = 1;
            break;
        }
        if (ready == 0)
            continue;
        ssize_t received = recvfrom(fd, buffer, sizeof(buffer), 0,
                                    (struct sockaddr *)&peer, &peer_length);
        if (received < 0) {
            if (errno == EINTR)
                continue;
            perror("recvfrom");
            exit_status = 1;
            break;
        }
        if (peer.sq_node == server.sq_node && peer.sq_port == server.sq_port &&
            classify_qmi(buffer, (size_t)received, &subscribed,
                         &invite_count) != 0) {
            exit_status = 1;
            break;
        }
    }

    if (!subscribed) {
        fprintf(stderr, "event=subscription-not-confirmed\n");
        exit_status = 1;
    }
    printf("event=complete invites=%u\n", invite_count);

cleanup:
    if (send_lookup(fd, &local, QRTR_TYPE_DEL_LOOKUP) < 0)
        perror("remove IMS Private lookup");
    close(fd);
    return exit_status;
}
