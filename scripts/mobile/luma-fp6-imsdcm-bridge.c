// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Minimal Linux IMSDCM bridge for the Fairphone 6.
 *
 * The Qualcomm modem asks its application processor to coordinate the IMS
 * bearer through QMI service 0x302.  Luma already owns that bearer through
 * ModemManager; this bridge truthfully returns the existing interface and
 * address rather than creating a second data session.
 *
 * Privacy: APNs, IP addresses, subscription identifiers and QMI payloads are
 * deliberately absent from logs.
 */

#define _GNU_SOURCE

#include <arpa/inet.h>
#include <errno.h>
#include <ifaddrs.h>
#include <linux/qrtr.h>
#include <net/if.h>
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

#define IMSDCM_SERVICE 0x302U
#define IMSDCM_VERSION 1U
#define IMSDCM_INSTANCE 0U
#define IMSDCM_PDP_ACTIVATE 0x0020U
#define IMSDCM_PDP_ACTIVATE_INDICATION 0x0022U
#define IMSDCM_ADDRESS_CHANGE_INDICATION 0x0024U
#define IMSDCM_PDP_DEACTIVATE 0x0021U
#define IMSDCM_LINK_ADDRESS 0x0023U
#define IMSDCM_REGISTER_APP_STATE 0x002eU
#define IMSDCM_SUB_DESTROY_INSTANCE 0x0033U
#define IMSDCM_SERVICE_ENABLE_STATUS 0x0034U
#define QMI_REQUEST 0U
#define QMI_RESPONSE 2U
#define QMI_INDICATION 4U
#define QMI_RESULT_SUCCESS 0U
#define QMI_ERROR_NONE 0U
#define QMI_RESULT_FAILURE 1U
#define QMI_ERROR_NOT_SUPPORTED 94U
#define IMSDCM_APN_TYPE_IMS 0U
#define IMSDCM_RAT_LTE 1U
#define IMSDCM_RAT_EPC 2U
#define IMSDCM_IP_V6 1U
/* QREL 16.95.0 ImsPdnFactoryImpl allocates dynamic PDP IDs from 20. */
#define IMSDCM_PDP_ID 20U
#define IMS_INTERFACE_FALLBACK "qmapmux0.0"

struct qmi_header {
    uint8_t type;
    uint16_t transaction;
    uint16_t message;
    uint16_t length;
} __attribute__((packed));

struct qmi_packet {
    uint8_t data[2048];
    size_t length;
};

struct tlv_view {
    uint8_t type;
    const uint8_t *value;
    uint16_t length;
};

static volatile sig_atomic_t stopping;

static uint16_t read_le16(const void *source)
{
    const uint8_t *p = source;
    return (uint16_t)p[0] | ((uint16_t)p[1] << 8);
}

static uint32_t read_le32(const void *source)
{
    const uint8_t *p = source;
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void write_le16(void *destination, uint16_t value)
{
    uint8_t *p = destination;
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
}

static void write_le32(void *destination, uint32_t value)
{
    uint8_t *p = destination;
    p[0] = (uint8_t)value;
    p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16);
    p[3] = (uint8_t)(value >> 24);
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

static int packet_begin(struct qmi_packet *packet, uint8_t type,
                        uint16_t transaction, uint16_t message)
{
    struct qmi_header *header;

    memset(packet, 0, sizeof(*packet));
    packet->length = sizeof(*header);
    header = (struct qmi_header *)packet->data;
    header->type = type;
    write_le16(&header->transaction, transaction);
    write_le16(&header->message, message);
    return 0;
}

static int packet_add_tlv(struct qmi_packet *packet, uint8_t type,
                          const void *value, uint16_t length)
{
    struct qmi_header *header = (struct qmi_header *)packet->data;
    uint8_t *cursor;

    if (packet->length + 3U + length > sizeof(packet->data))
        return -1;
    cursor = packet->data + packet->length;
    cursor[0] = type;
    write_le16(cursor + 1, length);
    if (length)
        memcpy(cursor + 3, value, length);
    packet->length += 3U + length;
    write_le16(&header->length,
               (uint16_t)(packet->length - sizeof(*header)));
    return 0;
}

static int packet_add_u8(struct qmi_packet *packet, uint8_t type, uint8_t value)
{
    return packet_add_tlv(packet, type, &value, sizeof(value));
}

static int packet_add_u32(struct qmi_packet *packet, uint8_t type,
                          uint32_t value)
{
    uint8_t encoded[4];
    write_le32(encoded, value);
    return packet_add_tlv(packet, type, encoded, sizeof(encoded));
}

static int packet_add_result(struct qmi_packet *packet, uint16_t result,
                             uint16_t error)
{
    uint8_t encoded[4];
    write_le16(encoded, result);
    write_le16(encoded + 2, error);
    return packet_add_tlv(packet, 0x02, encoded, sizeof(encoded));
}

static int send_packet(int fd, const struct sockaddr_qrtr *peer,
                       const struct qmi_packet *packet)
{
    ssize_t sent = sendto(fd, packet->data, packet->length, 0,
                          (const struct sockaddr *)peer, sizeof(*peer));
    return sent == (ssize_t)packet->length ? 0 : -1;
}

static bool find_tlv(const uint8_t *data, size_t size, uint8_t wanted,
                     struct tlv_view *result)
{
    const uint8_t *cursor = data;
    size_t remaining = size;

    while (remaining >= 3) {
        uint16_t length = read_le16(cursor + 1);
        if ((size_t)length + 3U > remaining)
            return false;
        if (cursor[0] == wanted) {
            result->type = cursor[0];
            result->value = cursor + 3;
            result->length = length;
            return true;
        }
        cursor += 3U + length;
        remaining -= 3U + length;
    }
    return false;
}

static bool get_global_ims_ipv6(char *output, size_t output_size)
{
    struct ifaddrs *addresses = NULL;
    struct ifaddrs *entry;
    const char *interface = getenv("DEV");
    bool found = false;

    /*
     * The IMS mux is allocated dynamically.  It was qmapmux0.0 during the
     * first physical gate and qmapmux0.1 after modem recovery; pinning the
     * former made a healthy bearer look absent and rejected the modem's PDP
     * activation request.  The service passes DEV from /run/imsd.env.  Keep
     * a narrow qmapmux-only fallback for standalone diagnostics.
     */
    if (!interface || strncmp(interface, "qmapmux", 7) != 0 ||
        if_nametoindex(interface) == 0)
        interface = IMS_INTERFACE_FALLBACK;

    if (getifaddrs(&addresses) != 0)
        return false;
    for (entry = addresses; entry; entry = entry->ifa_next) {
        const struct sockaddr_in6 *address;

        if (!entry->ifa_addr || entry->ifa_addr->sa_family != AF_INET6 ||
            (strcmp(entry->ifa_name, interface) != 0 &&
             strncmp(entry->ifa_name, "qmapmux", 7) != 0))
            continue;
        address = (const struct sockaddr_in6 *)entry->ifa_addr;
        if (IN6_IS_ADDR_LINKLOCAL(&address->sin6_addr) ||
            IN6_IS_ADDR_LOOPBACK(&address->sin6_addr) ||
            IN6_IS_ADDR_UNSPECIFIED(&address->sin6_addr))
            continue;
        if (inet_ntop(AF_INET6, &address->sin6_addr, output, output_size)) {
            found = true;
            break;
        }
    }
    freeifaddrs(addresses);
    return found;
}

static bool validate_ims_activation(const struct qmi_header *header,
                                    size_t size, uint32_t *sequence,
                                    uint32_t *instance)
{
    struct tlv_view connection;
    struct tlv_view sequence_tlv;
    struct tlv_view instance_tlv;
    const uint8_t *value;
    uint8_t apn_length;
    size_t minimum;

    if (size < sizeof(*header) ||
        !find_tlv((const uint8_t *)header + sizeof(*header),
                  size - sizeof(*header), 0x01, &connection) ||
        !find_tlv((const uint8_t *)header + sizeof(*header),
                  size - sizeof(*header), 0x10, &sequence_tlv) ||
        sequence_tlv.length != 4)
        return false;

    value = connection.value;
    if (connection.length < 1)
        return false;
    apn_length = value[0];
    minimum = 1U + apn_length + 16U;
    if (connection.length != minimum || apn_length != 3 ||
        strncasecmp((const char *)value + 1, "ims", 3) != 0) {
        fprintf(stderr,
                "event=activation-contract apn-shape=invalid bytes=%u\n",
                connection.length);
        return false;
    }
    value += 1U + apn_length;
    fprintf(stderr,
            "event=activation-contract apn-type=%u rat=%u family=%u\n",
            read_le32(value), read_le32(value + 4), read_le32(value + 8));
    if (read_le32(value) != IMSDCM_APN_TYPE_IMS ||
        (read_le32(value + 4) != IMSDCM_RAT_LTE &&
         read_le32(value + 4) != IMSDCM_RAT_EPC) ||
        read_le32(value + 8) != IMSDCM_IP_V6)
        return false;

    *sequence = read_le32(sequence_tlv.value);
    *instance = 0;
    if (find_tlv((const uint8_t *)header + sizeof(*header),
                 size - sizeof(*header), 0x13, &instance_tlv)) {
        if (instance_tlv.length != 4)
            return false;
        *instance = read_le32(instance_tlv.value);
    }
    return true;
}

static int send_simple_response(int fd, const struct sockaddr_qrtr *peer,
                                const struct qmi_header *request,
                                uint16_t result, uint16_t error)
{
    struct qmi_packet response;
    packet_begin(&response, QMI_RESPONSE, read_le16(&request->transaction),
                 read_le16(&request->message));
    packet_add_result(&response, result, error);
    return send_packet(fd, peer, &response);
}

static void log_control_request_shape(const struct qmi_header *request,
                                      size_t size)
{
    const uint8_t *cursor;
    size_t remaining;

    if (size < sizeof(*request))
        return;
    cursor = (const uint8_t *)request + sizeof(*request);
    remaining = size - sizeof(*request);
    while (remaining >= 3) {
        uint8_t type = cursor[0];
        uint16_t length = read_le16(cursor + 1);

        cursor += 3;
        remaining -= 3;
        if (length > remaining) {
            fprintf(stderr,
                    "event=control-shape-invalid message=0x%04x\n",
                    read_le16(&request->message));
            return;
        }
        if (length == 1) {
            printf("event=control-field message=0x%04x tlv=0x%02x length=1 value=%u\n",
                   read_le16(&request->message), type, cursor[0]);
        } else if (length == 2) {
            printf("event=control-field message=0x%04x tlv=0x%02x length=2 value=%u\n",
                   read_le16(&request->message), type, read_le16(cursor));
        } else if (length == 4) {
            printf("event=control-field message=0x%04x tlv=0x%02x length=4 value=%u\n",
                   read_le16(&request->message), type, read_le32(cursor));
        } else {
            printf("event=control-field message=0x%04x tlv=0x%02x length=%u value=redacted\n",
                   read_le16(&request->message), type, length);
        }
        cursor += length;
        remaining -= length;
    }
    fflush(stdout);
}

static int handle_activate(int fd, const struct sockaddr_qrtr *peer,
                           const struct qmi_header *request, size_t size)
{
    struct qmi_packet response;
    struct qmi_packet indication;
    uint32_t sequence;
    uint32_t instance;
    char address[INET6_ADDRSTRLEN];
    uint8_t address_info[4 + 1 + INET6_ADDRSTRLEN];
    size_t address_length;

    if (!validate_ims_activation(request, size, &sequence, &instance) ||
        !get_global_ims_ipv6(address, sizeof(address))) {
        fprintf(stderr, "event=activation-rejected reason=contract\n");
        return send_simple_response(fd, peer, request, QMI_RESULT_FAILURE,
                                    QMI_ERROR_NOT_SUPPORTED);
    }

    packet_begin(&response, QMI_RESPONSE, read_le16(&request->transaction),
                 IMSDCM_PDP_ACTIVATE);
    packet_add_result(&response, QMI_RESULT_SUCCESS, QMI_ERROR_NONE);
    packet_add_u8(&response, 0x10, IMSDCM_PDP_ID);
    packet_add_u32(&response, 0x11, sequence);
    packet_add_u32(&response, 0x12, instance);
    if (send_packet(fd, peer, &response) != 0)
        return -1;

    /*
     * QREL 16.95.0 uses a distinct asynchronous indication ID and its
     * generated table carries mandatory PDP id (0x01), optional address
     * (0x10), and optional instance (0x11).  The stock successful path
     * leaves address-valid clear and includes the instance.  Do not emit the
     * obsolete result/sequence fields from older public IMSDCM tables.
     */
    packet_begin(&indication, QMI_INDICATION, 0,
                 IMSDCM_PDP_ACTIVATE_INDICATION);
    packet_add_u8(&indication, 0x01, IMSDCM_PDP_ID);
    packet_add_u32(&indication, 0x11, instance);
    if (send_packet(fd, peer, &indication) != 0)
        return -1;

    /*
     * Activation completion and connected-PDN status are separate QREL
     * indications.  Message 0x20 carries the result, request sequence,
     * address and instance after the 0x22 completion above.
     */
    address_length = strlen(address);
    if (address_length > 255)
        return -1;
    write_le32(address_info, IMSDCM_IP_V6);
    address_info[4] = (uint8_t)address_length;
    memcpy(address_info + 5, address, address_length);

    packet_begin(&indication, QMI_INDICATION, 0, IMSDCM_PDP_ACTIVATE);
    packet_add_result(&indication, QMI_RESULT_SUCCESS, QMI_ERROR_NONE);
    packet_add_u8(&indication, 0x01, IMSDCM_PDP_ID);
    packet_add_u32(&indication, 0x10, sequence);
    packet_add_tlv(&indication, 0x11, address_info,
                   (uint16_t)(5U + address_length));
    packet_add_u32(&indication, 0x12, instance);
    if (send_packet(fd, peer, &indication) != 0)
        return -1;

    /*
     * QREL also emits the dedicated IP-address-change callback once DSI has
     * made the assigned address visible.  Its generated structure is PDP id,
     * optional address aggregate, and optional instance (message 0x24).
     */
    packet_begin(&indication, QMI_INDICATION, 0,
                 IMSDCM_ADDRESS_CHANGE_INDICATION);
    packet_add_u8(&indication, 0x01, IMSDCM_PDP_ID);
    packet_add_tlv(&indication, 0x10, address_info,
                   (uint16_t)(5U + address_length));
    packet_add_u32(&indication, 0x11, instance);
    if (send_packet(fd, peer, &indication) != 0)
        return -1;

    printf("event=ims-bearer-attached family=ipv6\n");
    fflush(stdout);
    return 0;
}

static int handle_request(int fd, const struct sockaddr_qrtr *peer,
                          const uint8_t *data, size_t size)
{
    const struct qmi_header *request;
    uint16_t message;

    if (size < sizeof(*request))
        return -1;
    request = (const struct qmi_header *)data;
    if (request->type != QMI_REQUEST ||
        (size_t)read_le16(&request->length) + sizeof(*request) != size)
        return -1;
    message = read_le16(&request->message);

    switch (message) {
    case IMSDCM_PDP_ACTIVATE:
        return handle_activate(fd, peer, request, size);
    case IMSDCM_PDP_DEACTIVATE:
    case IMSDCM_LINK_ADDRESS:
    case IMSDCM_REGISTER_APP_STATE:
    case IMSDCM_SUB_DESTROY_INSTANCE:
    case IMSDCM_SERVICE_ENABLE_STATUS:
        log_control_request_shape(request, size);
        if (send_simple_response(fd, peer, request, QMI_RESULT_SUCCESS,
                                 QMI_ERROR_NONE) == 0) {
            printf("event=request-acknowledged message=0x%04x\n", message);
            fflush(stdout);
            return 0;
        }
        return -1;
    default:
        fprintf(stderr, "event=unsupported-request message=0x%04x\n", message);
        return send_simple_response(fd, peer, request, QMI_RESULT_FAILURE,
                                    QMI_ERROR_NOT_SUPPORTED);
    }
}

int main(int argc, char **argv)
{
    struct sockaddr_qrtr local;
    socklen_t local_length = sizeof(local);
    int duration = 90;
    int fd;
    int exit_status = 0;
    time_t deadline;

    if (argc == 2) {
        duration = atoi(argv[1]);
        if (duration < 1 || duration > 86400) {
            fprintf(stderr, "duration must be between 1 and 86400 seconds\n");
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
    if (send_server_control(fd, QRTR_TYPE_NEW_SERVER, &local) < 0) {
        perror("publish IMSDCM");
        close(fd);
        return 1;
    }
    printf("event=published service=%u duration=%d\n", IMSDCM_SERVICE,
           duration);
    fflush(stdout);
    deadline = time(NULL) + duration;

    while (!stopping && time(NULL) < deadline) {
        struct pollfd descriptor = { .fd = fd, .events = POLLIN };
        int timeout = (int)((deadline - time(NULL)) * 1000);
        int ready;

        if (timeout > 1000)
            timeout = 1000;
        if (timeout < 0)
            timeout = 0;
        ready = poll(&descriptor, 1, timeout);
        if (ready < 0) {
            if (errno == EINTR)
                continue;
            perror("poll");
            exit_status = 1;
            break;
        }
        if (ready > 0 && (descriptor.revents & POLLIN)) {
            uint8_t buffer[4096];
            struct sockaddr_qrtr peer;
            socklen_t peer_length = sizeof(peer);
            ssize_t received = recvfrom(fd, buffer, sizeof(buffer), 0,
                                        (struct sockaddr *)&peer, &peer_length);

            if (received < 0) {
                if (errno == EINTR)
                    continue;
                perror("recvfrom");
                exit_status = 1;
                break;
            }
            if (peer.sq_port != QRTR_PORT_CTRL &&
                handle_request(fd, &peer, buffer, (size_t)received) != 0) {
                fprintf(stderr, "event=request-failed\n");
                exit_status = 1;
                break;
            }
        }
    }

    if (send_server_control(fd, QRTR_TYPE_DEL_SERVER, &local) < 0) {
        perror("unpublish IMSDCM");
        exit_status = 1;
    }
    printf("event=unpublished\n");
    close(fd);
    return exit_status;
}
