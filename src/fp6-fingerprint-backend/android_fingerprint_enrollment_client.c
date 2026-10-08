/*
 * Exact-stock Android Binder enrollment client for the Fairphone 6.
 *
 * This constructor DSO runs in a disposable QREL 16.95.0 Bionic chroot.  It
 * asks stock Gatekeeper to verify a PIN against the fingerprint HAL's own
 * challenge, then passes the resulting hardware-auth token to the unmodified
 * FocalTech AIDL HAL.  The PIN and HAT never leave this process.  Only the
 * opaque Gatekeeper handle is stored, and the sensor remains match-on-chip.
 */

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

typedef int32_t binder_status_t;
typedef uint32_t transaction_code_t;
typedef uint32_t binder_flags_t;
typedef struct AIBinder AIBinder;
typedef struct AIBinder_Class AIBinder_Class;
typedef struct AParcel AParcel;
typedef struct AStatus AStatus;

enum {
    STATUS_OK = 0,
    STATUS_UNKNOWN_TRANSACTION = -74,
    FIRST_CALL_TRANSACTION = 1,
    TRANSACTION_GET_INTERFACE_HASH = 16777214,
    TRANSACTION_GET_INTERFACE_VERSION = 16777215,
    FLAG_CLEAR_BUF = 0x20,
    O_RDONLY_LINUX = 0,
    O_WRONLY_LINUX = 1,
    O_CREAT_LINUX = 0100,
    O_EXCL_LINUX = 0200,
    O_CLOEXEC_LINUX = 02000000,
};

#define PIN_CAPACITY 64
#define HANDLE_CAPACITY 512
#define MAC_CAPACITY 64
#define GATEKEEPER_UID 100000
#define CREDENTIAL_PATH "/data/luma-state/credential.handle"
#define CREDENTIAL_TEMP "/data/luma-state/.credential.handle.new"

typedef void *(*AIBinder_Class_onCreate)(void *args);
typedef void (*AIBinder_Class_onDestroy)(void *user_data);
typedef binder_status_t (*AIBinder_Class_onTransact)(
    AIBinder *binder, transaction_code_t code, const AParcel *in, AParcel *out);
typedef bool (*AParcel_byteArrayAllocator)(void *array_data, int32_t length,
                                           int8_t **out_buffer);

extern AIBinder *AServiceManager_waitForService(const char *instance);
extern AIBinder_Class *AIBinder_Class_define(
    const char *descriptor, AIBinder_Class_onCreate on_create,
    AIBinder_Class_onDestroy on_destroy, AIBinder_Class_onTransact on_transact);
extern AIBinder *AIBinder_new(const AIBinder_Class *clazz, void *args);
extern bool AIBinder_associateClass(AIBinder *binder, const AIBinder_Class *clazz);
extern void AIBinder_markVintfStability(AIBinder *binder);
extern binder_status_t AIBinder_prepareTransaction(AIBinder *binder, AParcel **in);
extern binder_status_t AIBinder_transact(AIBinder *binder, transaction_code_t code,
                                         AParcel **in, AParcel **out,
                                         binder_flags_t flags);
extern void ABinderProcess_startThreadPool(void);
extern binder_status_t AParcel_writeInt32(AParcel *parcel, int32_t value);
extern binder_status_t AParcel_writeInt64(AParcel *parcel, int64_t value);
extern binder_status_t AParcel_writeByteArray(AParcel *parcel,
                                               const int8_t *data,
                                               int32_t length);
extern binder_status_t AParcel_writeStrongBinder(AParcel *parcel, AIBinder *binder);
extern binder_status_t AParcel_writeString(AParcel *parcel, const char *string,
                                           int32_t length);
extern binder_status_t AParcel_readInt32(const AParcel *parcel, int32_t *value);
extern binder_status_t AParcel_readInt64(const AParcel *parcel, int64_t *value);
extern binder_status_t AParcel_readByteArray(const AParcel *parcel, void *array_data,
                                              AParcel_byteArrayAllocator allocator);
extern binder_status_t AParcel_readStrongBinder(const AParcel *parcel,
                                                AIBinder **binder);
extern int32_t AParcel_getDataPosition(const AParcel *parcel);
extern binder_status_t AParcel_setDataPosition(const AParcel *parcel,
                                                int32_t position);
extern void AParcel_markSensitive(AParcel *parcel);
extern binder_status_t AParcel_writeStatusHeader(AParcel *parcel,
                                                 const AStatus *status);
extern binder_status_t AParcel_readStatusHeader(const AParcel *parcel,
                                                AStatus **status);
extern AStatus *AStatus_newOk(void);
extern bool AStatus_isOk(const AStatus *status);
extern int32_t AStatus_getExceptionCode(const AStatus *status);
extern int32_t AStatus_getServiceSpecificError(const AStatus *status);
extern void AStatus_delete(AStatus *status);
extern void AParcel_delete(AParcel *parcel);

extern long read(int fd, void *buf, unsigned long count);
extern long write(int fd, const void *buf, unsigned long count);
extern int open(const char *path, int flags, ...);
extern int close(int fd);
extern int fsync(int fd);
extern int rename(const char *old_path, const char *new_path);
extern int unlink(const char *path);
extern unsigned int sleep(unsigned int seconds);
extern void _exit(int status);

struct byte_buffer {
    uint8_t *data;
    int32_t capacity;
    int32_t length;
};

struct hardware_auth_token {
    int64_t challenge;
    int64_t user_id;
    int64_t authenticator_id;
    int32_t authenticator_type;
    int64_t timestamp_ms;
    uint8_t mac[MAC_CAPACITY];
    int32_t mac_size;
};

struct credential_record {
    uint8_t magic[8];
    uint32_t version;
    uint32_t android_user_id;
    uint64_t secure_user_id;
    uint32_t handle_size;
    uint32_t reserved;
    uint8_t handle[HANDLE_CAPACITY];
};

static volatile int64_t generated_challenge;
static volatile int challenge_state;
static volatile int enrollment_done;
static volatile int enrollment_error;
static volatile int enrollment_id;
static volatile int authentication_done;
static volatile int authentication_error;
static volatile int session_closed;
static volatile int enumeration_done;
static volatile int enumeration_count;
static int gatekeeper_enroll_diag;
static int parcel_header_diag;

static void emit(const char *s, unsigned long n) { (void)write(1, s, n); }
#define EMIT(literal) emit((literal), sizeof(literal) - 1)

static void clear_bytes(void *data, unsigned long size) {
    volatile uint8_t *p = (volatile uint8_t *)data;
    while (size-- != 0) *p++ = 0;
}

static bool allocate_bytes(void *opaque, int32_t length, int8_t **out) {
    struct byte_buffer *buffer = (struct byte_buffer *)opaque;
    if (length < 0 || length > buffer->capacity) return false;
    buffer->length = length;
    *out = length == 0 ? NULL : (int8_t *)buffer->data;
    return true;
}

static binder_status_t write_ok(AParcel *out) {
    AStatus *status = AStatus_newOk();
    binder_status_t rc;
    if (status == NULL) return -1;
    rc = AParcel_writeStatusHeader(out, status);
    AStatus_delete(status);
    return rc;
}

static int status_reply_ok(AParcel *out) {
    AStatus *status = NULL;
    int ok = 0;
    if (AParcel_readStatusHeader(out, &status) == STATUS_OK && status != NULL)
        ok = AStatus_isOk(status) ? 1 : 0;
    if (status != NULL) AStatus_delete(status);
    return ok;
}

/*
 * Android maps primary user 0 to Gatekeeper UID 100000.  A replaced userdata
 * image can leave only that secure record behind.  Match locksettings' narrow
 * target-user cleanup; stock implementations may report either success or the
 * documented service-specific -1 when the record was already absent.
 */
static int gatekeeper_delete_primary_user(AIBinder *service) {
    AParcel *in = NULL;
    AParcel *out = NULL;
    AStatus *status = NULL;
    binder_status_t rc = AIBinder_prepareTransaction(service, &in);
    int accepted = 0;
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, GATEKEEPER_UID);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(service, FIRST_CALL_TRANSACTION + 1,
                               &in, &out, 0);
    if (rc == STATUS_OK && out != NULL &&
        AParcel_readStatusHeader(out, &status) == STATUS_OK && status != NULL) {
        if (AStatus_isOk(status)) {
            accepted = 1;
        } else if (AStatus_getExceptionCode(status) == -8 &&
                   AStatus_getServiceSpecificError(status) == -1) {
            accepted = 1;
        }
    }
    if (status != NULL) AStatus_delete(status);
    if (out != NULL) AParcel_delete(out);
    return accepted ? 0 : -1;
}

static void *binder_create(void *args) { return args; }
static void binder_destroy(void *data) { (void)data; }

static binder_status_t callback_transact(AIBinder *binder,
                                         transaction_code_t code,
                                         const AParcel *in, AParcel *out) {
    int32_t first = 0;
    int32_t second = 0;
    int64_t challenge = 0;
    (void)binder;

    if (code == FIRST_CALL_TRANSACTION) {
        if (AParcel_readInt64(in, &challenge) != STATUS_OK) return -1;
        if (write_ok(out) != STATUS_OK) return -1;
        generated_challenge = challenge;
        challenge_state = challenge != 0 ? 1 : -1;
        return STATUS_OK;
    }
    if (code == FIRST_CALL_TRANSACTION + 2 ||
        code == FIRST_CALL_TRANSACTION + 3 ||
        code == FIRST_CALL_TRANSACTION + 4) {
        if (AParcel_readInt32(in, &first) != STATUS_OK) return -1;
        if (AParcel_readInt32(in, &second) != STATUS_OK) return -1;
        if (write_ok(out) != STATUS_OK) return -1;
        if (code == FIRST_CALL_TRANSACTION + 2) {
            EMIT("event=fp6_enrollment_acquired contents_logged=0\n");
        } else if (code == FIRST_CALL_TRANSACTION + 3) {
            enrollment_error = 1;
            switch (first) {
            case 1: EMIT("event=fp6_enrollment_error category=hardware_unavailable contents_logged=0\n"); break;
            case 2: EMIT("event=fp6_enrollment_error category=unable_to_process contents_logged=0\n"); break;
            case 3: EMIT("event=fp6_enrollment_error category=timeout contents_logged=0\n"); break;
            case 4: EMIT("event=fp6_enrollment_error category=no_space contents_logged=0\n"); break;
            case 5: EMIT("event=fp6_enrollment_error category=canceled contents_logged=0\n"); break;
            case 7: EMIT("event=fp6_enrollment_error category=vendor contents_logged=0\n"); break;
            default: EMIT("event=fp6_enrollment_error category=other contents_logged=0\n"); break;
            }
        } else {
            enrollment_id = first;
            if (second == 0) {
                enrollment_done = 1;
                EMIT("event=fp6_enrollment_progress remaining=0\n");
            } else {
                EMIT("event=fp6_enrollment_progress remaining=nonzero\n");
            }
        }
        return STATUS_OK;
    }
    if (code == FIRST_CALL_TRANSACTION + 5) {
        /*
         * onAuthenticationSucceeded(enrollmentId, HardwareAuthToken).  The
         * stock HAL and trustlet have already performed the match.  Consume
         * only the non-secret enrollment identifier; never deserialize or log
         * the returned HAT in this physical acceptance client.
         */
        if (AParcel_readInt32(in, &first) != STATUS_OK) return -1;
        if (write_ok(out) != STATUS_OK) return -1;
        if (first <= 0) {
            authentication_error = 1;
            EMIT("event=fp6_authentication_error category=invalid_enrollment_id contents_logged=0\n");
        } else {
            authentication_done = 1;
            EMIT("event=fp6_authentication_match accepted=true hat_logged=0 raw_images=0\n");
        }
        return STATUS_OK;
    }
    if (code == FIRST_CALL_TRANSACTION + 6) {
        if (write_ok(out) != STATUS_OK) return -1;
        EMIT("event=fp6_authentication_no_match retry=true raw_images=0\n");
        return STATUS_OK;
    }
    if (code == FIRST_CALL_TRANSACTION + 15) {
        if (write_ok(out) != STATUS_OK) return -1;
        session_closed = 1;
        EMIT("event=fp6_fingerprint_session_closed clean=true\n");
        return STATUS_OK;
    }
    if (code == FIRST_CALL_TRANSACTION + 11) {
        int32_t count = 0;
        int32_t ignored = 0;
        int32_t i;
        if (AParcel_readInt32(in, &count) != STATUS_OK ||
            count < 0 || count > 64)
            return -1;
        for (i = 0; i < count; ++i)
            if (AParcel_readInt32(in, &ignored) != STATUS_OK) return -1;
        if (write_ok(out) != STATUS_OK) return -1;
        enumeration_count = count;
        enumeration_done = 1;
        if (count == 0)
            EMIT("event=fp6_fingerprint_enumeration templates=zero identifiers_logged=0\n");
        else
            EMIT("event=fp6_fingerprint_enumeration templates=nonzero identifiers_logged=0\n");
        return STATUS_OK;
    }
    if (code == TRANSACTION_GET_INTERFACE_VERSION) {
        if (write_ok(out) != STATUS_OK) return -1;
        return AParcel_writeInt32(out, 3);
    }
    if (code == TRANSACTION_GET_INTERFACE_HASH) {
        if (write_ok(out) != STATUS_OK) return -1;
        return AParcel_writeString(out, "", 0);
    }
    return STATUS_UNKNOWN_TRANSACTION;
}

static int read_parcelable_header(const AParcel *parcel, int32_t *end) {
    int32_t start = AParcel_getDataPosition(parcel);
    int32_t size = 0;
    parcel_header_diag = 0;
    if (AParcel_readInt32(parcel, &size) != STATUS_OK) parcel_header_diag = 1;
    else if (size == 1) {
        /* QREL's generated NDK ABI prefixes returned parcelables with present=1. */
        start = AParcel_getDataPosition(parcel);
        if (AParcel_readInt32(parcel, &size) != STATUS_OK) parcel_header_diag = 1;
        else if (size < 4) parcel_header_diag = 5;
    }
    else if (size == 0) parcel_header_diag = 2;
    else if (size == 2) parcel_header_diag = 6;
    else if (size == 3) parcel_header_diag = 7;
    else if (start < 0) parcel_header_diag = 3;
    else if (size > INT32_MAX - start) parcel_header_diag = 4;
    if (parcel_header_diag != 0) return -1;
    *end = start + size;
    return 0;
}

static int read_hat(const AParcel *parcel, struct hardware_auth_token *hat) {
    int32_t end = 0;
    int32_t timestamp_end = 0;
    struct byte_buffer mac = {hat->mac, MAC_CAPACITY, 0};
    if (read_parcelable_header(parcel, &end) != 0) return -1;
    if (AParcel_readInt64(parcel, &hat->challenge) != STATUS_OK ||
        AParcel_readInt64(parcel, &hat->user_id) != STATUS_OK ||
        AParcel_readInt64(parcel, &hat->authenticator_id) != STATUS_OK ||
        AParcel_readInt32(parcel, &hat->authenticator_type) != STATUS_OK ||
        read_parcelable_header(parcel, &timestamp_end) != 0 ||
        AParcel_readInt64(parcel, &hat->timestamp_ms) != STATUS_OK ||
        AParcel_setDataPosition(parcel, timestamp_end) != STATUS_OK ||
        AParcel_readByteArray(parcel, &mac, allocate_bytes) != STATUS_OK)
        return -1;
    hat->mac_size = mac.length;
    return AParcel_setDataPosition(parcel, end) == STATUS_OK ? 0 : -1;
}

static int write_hat(AParcel *parcel, const struct hardware_auth_token *hat) {
    int32_t start = AParcel_getDataPosition(parcel);
    int32_t timestamp_start;
    int32_t timestamp_end;
    int32_t end;
    if (AParcel_writeInt32(parcel, 0) != STATUS_OK ||
        AParcel_writeInt64(parcel, hat->challenge) != STATUS_OK ||
        AParcel_writeInt64(parcel, hat->user_id) != STATUS_OK ||
        AParcel_writeInt64(parcel, hat->authenticator_id) != STATUS_OK ||
        AParcel_writeInt32(parcel, hat->authenticator_type) != STATUS_OK)
        return -1;
    timestamp_start = AParcel_getDataPosition(parcel);
    if (AParcel_writeInt32(parcel, 1) != STATUS_OK) return -1;
    timestamp_start = AParcel_getDataPosition(parcel);
    if (AParcel_writeInt32(parcel, 0) != STATUS_OK ||
        AParcel_writeInt64(parcel, hat->timestamp_ms) != STATUS_OK)
        return -1;
    timestamp_end = AParcel_getDataPosition(parcel);
    if (AParcel_setDataPosition(parcel, timestamp_start) != STATUS_OK ||
        AParcel_writeInt32(parcel, timestamp_end - timestamp_start) != STATUS_OK ||
        AParcel_setDataPosition(parcel, timestamp_end) != STATUS_OK ||
        AParcel_writeByteArray(parcel, (const int8_t *)hat->mac,
                               hat->mac_size) != STATUS_OK)
        return -1;
    end = AParcel_getDataPosition(parcel);
    if (AParcel_setDataPosition(parcel, start) != STATUS_OK ||
        AParcel_writeInt32(parcel, end - start) != STATUS_OK ||
        AParcel_setDataPosition(parcel, end) != STATUS_OK)
        return -1;
    return 0;
}

static int load_credential(struct credential_record *record) {
    static const uint8_t magic[8] = {'L','U','M','A','F','P','G','K'};
    int fd = open(CREDENTIAL_PATH, O_RDONLY_LINUX | O_CLOEXEC_LINUX);
    long count;
    unsigned int i;
    if (fd < 0) return 1;
    count = read(fd, record, sizeof(*record));
    (void)close(fd);
    if (count != (long)sizeof(*record) || record->version != 1 ||
        record->android_user_id != GATEKEEPER_UID || record->handle_size == 0 ||
        record->handle_size > HANDLE_CAPACITY)
        return -1;
    for (i = 0; i < 8; ++i) if (record->magic[i] != magic[i]) return -1;
    return 0;
}

static int persist_credential(const struct credential_record *record) {
    int fd;
    long count;
    (void)unlink(CREDENTIAL_TEMP);
    fd = open(CREDENTIAL_TEMP,
              O_WRONLY_LINUX | O_CREAT_LINUX | O_EXCL_LINUX | O_CLOEXEC_LINUX,
              0600);
    if (fd < 0) return -1;
    count = write(fd, record, sizeof(*record));
    if (count != (long)sizeof(*record) || fsync(fd) != 0 || close(fd) != 0) {
        (void)close(fd);
        (void)unlink(CREDENTIAL_TEMP);
        return -1;
    }
    if (rename(CREDENTIAL_TEMP, CREDENTIAL_PATH) != 0) {
        (void)unlink(CREDENTIAL_TEMP);
        return -1;
    }
    return 0;
}

static int gatekeeper_enroll(AIBinder *service, const uint8_t *pin,
                             int32_t pin_size,
                             struct credential_record *record) {
    static const uint8_t magic[8] = {'L','U','M','A','F','P','G','K'};
    AParcel *in = NULL;
    AParcel *out = NULL;
    struct byte_buffer handle = {record->handle, HANDLE_CAPACITY, 0};
    int32_t end = 0;
    int32_t status = -1;
    int32_t timeout = 0;
    int64_t secure_user_id = 0;
    binder_status_t rc = AIBinder_prepareTransaction(service, &in);
    unsigned int i;
    if (rc == STATUS_OK) AParcel_markSensitive(in);
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, GATEKEEPER_UID);
    if (rc == STATUS_OK) rc = AParcel_writeByteArray(in, NULL, 0);
    if (rc == STATUS_OK) rc = AParcel_writeByteArray(in, NULL, 0);
    if (rc == STATUS_OK) rc = AParcel_writeByteArray(in, (const int8_t *)pin,
                                                      pin_size);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(service, FIRST_CALL_TRANSACTION + 2,
                               &in, &out, FLAG_CLEAR_BUF);
    if (rc != STATUS_OK || out == NULL) gatekeeper_enroll_diag = 1;
    else if (!status_reply_ok(out)) gatekeeper_enroll_diag = 2;
    else if (read_parcelable_header(out, &end) != 0) gatekeeper_enroll_diag = 3;
    else if (AParcel_readInt32(out, &status) != STATUS_OK ||
             AParcel_readInt32(out, &timeout) != STATUS_OK ||
             AParcel_readInt64(out, &secure_user_id) != STATUS_OK ||
             AParcel_readByteArray(out, &handle, allocate_bytes) != STATUS_OK ||
             AParcel_setDataPosition(out, end) != STATUS_OK)
        gatekeeper_enroll_diag = 4;
    else if (status != 0) gatekeeper_enroll_diag = 5;
    else if (secure_user_id == 0) gatekeeper_enroll_diag = 6;
    else if (handle.length <= 0) gatekeeper_enroll_diag = 7;
    if (gatekeeper_enroll_diag != 0) {
        if (out != NULL) AParcel_delete(out);
        return -1;
    }
    AParcel_delete(out);
    for (i = 0; i < 8; ++i) record->magic[i] = magic[i];
    record->version = 1;
    record->android_user_id = GATEKEEPER_UID;
    record->secure_user_id = (uint64_t)secure_user_id;
    record->handle_size = (uint32_t)handle.length;
    record->reserved = 0;
    if (persist_credential(record) != 0) {
        gatekeeper_enroll_diag = 8;
        return -1;
    }
    return 0;
}

static void emit_gatekeeper_enroll_diagnostic(void) {
    switch (gatekeeper_enroll_diag) {
    case 1: EMIT("event=fp6_gatekeeper_enroll_diag stage=transport\n"); break;
    case 2: EMIT("event=fp6_gatekeeper_enroll_diag stage=service_status\n"); break;
    case 3:
        switch (parcel_header_diag) {
        case 1: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_read\n"); break;
        case 2: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_size_0\n"); break;
        case 5: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_size_1\n"); break;
        case 6: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_size_2\n"); break;
        case 7: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_size_3\n"); break;
        case 3: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_position\n"); break;
        case 4: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_overflow\n"); break;
        default: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_header_unknown\n"); break;
        }
        break;
    case 4: EMIT("event=fp6_gatekeeper_enroll_diag stage=response_fields\n"); break;
    case 5: EMIT("event=fp6_gatekeeper_enroll_diag stage=secure_status\n"); break;
    case 6: EMIT("event=fp6_gatekeeper_enroll_diag stage=secure_user_id\n"); break;
    case 7: EMIT("event=fp6_gatekeeper_enroll_diag stage=credential_handle\n"); break;
    case 8: EMIT("event=fp6_gatekeeper_enroll_diag stage=persistence\n"); break;
    default: EMIT("event=fp6_gatekeeper_enroll_diag stage=unknown\n"); break;
    }
}

static int gatekeeper_verify(AIBinder *service, int64_t challenge,
                             const struct credential_record *record,
                             const uint8_t *pin, int32_t pin_size,
                             struct hardware_auth_token *hat) {
    AParcel *in = NULL;
    AParcel *out = NULL;
    int32_t end = 0;
    int32_t status = -1;
    int32_t timeout = 0;
    binder_status_t rc = AIBinder_prepareTransaction(service, &in);
    if (rc == STATUS_OK) AParcel_markSensitive(in);
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, GATEKEEPER_UID);
    if (rc == STATUS_OK) rc = AParcel_writeInt64(in, challenge);
    if (rc == STATUS_OK)
        rc = AParcel_writeByteArray(in, (const int8_t *)record->handle,
                                    (int32_t)record->handle_size);
    if (rc == STATUS_OK)
        rc = AParcel_writeByteArray(in, (const int8_t *)pin, pin_size);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(service, FIRST_CALL_TRANSACTION + 3,
                               &in, &out, FLAG_CLEAR_BUF);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out) ||
        read_parcelable_header(out, &end) != 0 ||
        AParcel_readInt32(out, &status) != STATUS_OK ||
        AParcel_readInt32(out, &timeout) != STATUS_OK || status != 0 ||
        read_hat(out, hat) != 0 ||
        AParcel_setDataPosition(out, end) != STATUS_OK ||
        hat->challenge != challenge || hat->user_id == 0 ||
        hat->mac_size != 32) {
        if (out != NULL) AParcel_delete(out);
        return -1;
    }
    AParcel_delete(out);
    return 0;
}

static int close_fingerprint_session(AIBinder *session) {
    AParcel *in = NULL;
    AParcel *out = NULL;
    binder_status_t rc;
    int i;

    session_closed = 0;
    rc = AIBinder_prepareTransaction(session, &in);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(session, FIRST_CALL_TRANSACTION + 10,
                               &in, &out, 0);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out)) {
        if (out != NULL) AParcel_delete(out);
        return -1;
    }
    AParcel_delete(out);
    for (i = 0; i < 10 && !session_closed; ++i) sleep(1);
    return session_closed ? 0 : -1;
}

__attribute__((constructor)) static void run_enrollment(void) {
    static const char fp_desc[] =
        "android.hardware.biometrics.fingerprint.IFingerprint";
    static const char session_desc[] =
        "android.hardware.biometrics.fingerprint.ISession";
    static const char callback_desc[] =
        "android.hardware.biometrics.fingerprint.ISessionCallback";
    static const char gatekeeper_desc[] = "android.hardware.gatekeeper.IGatekeeper";
    static const char fp_service_name[] =
        "android.hardware.biometrics.fingerprint.IFingerprint/default";
    static const char gatekeeper_service_name[] =
        "android.hardware.gatekeeper.IGatekeeper/default";
    uint8_t pin[PIN_CAPACITY];
    long pin_count;
    int32_t pin_size;
    struct credential_record credential;
    struct hardware_auth_token hat;
    AIBinder_Class *fp_class;
    AIBinder_Class *session_class;
    AIBinder_Class *callback_class;
    AIBinder_Class *gatekeeper_class;
    AIBinder *fp_service;
    AIBinder *gatekeeper_service;
    AIBinder *callback;
    AIBinder *session = NULL;
    AIBinder *cancellation = NULL;
    AParcel *in = NULL;
    AParcel *out = NULL;
    binder_status_t rc;
    int credential_state;
    int i;

    clear_bytes(pin, sizeof(pin));
    clear_bytes(&credential, sizeof(credential));
    clear_bytes(&hat, sizeof(hat));
#ifdef LUMA_AUTHENTICATE_ONLY
    EMIT("event=fp6_stock_authentication_client_start raw_images=0\n");
#else
    EMIT("event=fp6_stock_enrollment_client_start raw_images=0\n");
    EMIT("event=fp6_stock_enrollment_pin_waiting stdin_only=true\n");
    pin_count = read(0, pin, sizeof(pin));
    if (pin_count <= 0) {
        EMIT("event=fp6_stock_enrollment_fail reason=pin_read\n");
        _exit(40);
    }
    while (pin_count > 0 &&
           (pin[pin_count - 1] == '\n' || pin[pin_count - 1] == '\r'))
        --pin_count;
    if (pin_count <= 0 || pin_count > PIN_CAPACITY) {
        EMIT("event=fp6_stock_enrollment_fail reason=pin_length\n");
        _exit(41);
    }
    pin_size = (int32_t)pin_count;
#endif
    ABinderProcess_startThreadPool();
    fp_class = AIBinder_Class_define(fp_desc, binder_create, binder_destroy,
                                     callback_transact);
    session_class = AIBinder_Class_define(session_desc, binder_create,
                                          binder_destroy, callback_transact);
    callback_class = AIBinder_Class_define(callback_desc, binder_create,
                                           binder_destroy, callback_transact);
    gatekeeper_class = AIBinder_Class_define(gatekeeper_desc, binder_create,
                                             binder_destroy, callback_transact);
    if (fp_class == NULL || session_class == NULL || callback_class == NULL ||
        gatekeeper_class == NULL) {
        EMIT("event=fp6_stock_enrollment_fail reason=class_define\n");
        _exit(42);
    }
    fp_service = AServiceManager_waitForService(fp_service_name);
    gatekeeper_service = AServiceManager_waitForService(gatekeeper_service_name);
    if (fp_service == NULL || gatekeeper_service == NULL ||
        !AIBinder_associateClass(fp_service, fp_class) ||
        !AIBinder_associateClass(gatekeeper_service, gatekeeper_class)) {
        EMIT("event=fp6_stock_enrollment_fail reason=service_lookup\n");
        _exit(43);
    }
    callback = AIBinder_new(callback_class, NULL);
    if (callback == NULL) {
        EMIT("event=fp6_stock_enrollment_fail reason=callback_create\n");
        _exit(44);
    }
    AIBinder_markVintfStability(callback);
    rc = AIBinder_prepareTransaction(fp_service, &in);
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, 0);
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, 0);
    if (rc == STATUS_OK) rc = AParcel_writeStrongBinder(in, callback);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(fp_service, FIRST_CALL_TRANSACTION + 1,
                               &in, &out, 0);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out) ||
        AParcel_readStrongBinder(out, &session) != STATUS_OK || session == NULL ||
        !AIBinder_associateClass(session, session_class)) {
        EMIT("event=fp6_stock_enrollment_fail reason=create_session\n");
        _exit(45);
    }
    AParcel_delete(out);
    out = NULL;
#ifdef LUMA_AUTHENTICATE_ONLY
    enumeration_done = 0;
    enumeration_count = 0;
    rc = AIBinder_prepareTransaction(session, &in);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(session, FIRST_CALL_TRANSACTION + 5,
                               &in, &out, 0);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out)) {
        EMIT("event=fp6_stock_authentication_fail reason=enumerate_request\n");
        _exit(58);
    }
    AParcel_delete(out);
    out = NULL;
    for (i = 0; i < 10 && !enumeration_done; ++i) sleep(1);
    if (!enumeration_done || enumeration_count == 0) {
        EMIT("event=fp6_stock_authentication_fail reason=no_restored_templates\n");
#ifdef LUMA_PAUSE_ON_EMPTY_ENUMERATION
        EMIT("event=fp6_empty_enumeration_diagnostic_hold seconds=300\n");
        sleep(300);
#endif
        _exit(59);
    }
    /* ISession.authenticate(int64 operationId), transaction ordinal 3. */
    rc = AIBinder_prepareTransaction(session, &in);
    if (rc == STATUS_OK) rc = AParcel_writeInt64(in, 0);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(session, FIRST_CALL_TRANSACTION + 3,
                               &in, &out, 0);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out) ||
        AParcel_readStrongBinder(out, &cancellation) != STATUS_OK ||
        cancellation == NULL) {
        EMIT("event=fp6_stock_authentication_fail reason=fingerprint_authenticate\n");
        _exit(60);
    }
    AParcel_delete(out);
    out = NULL;
    EMIT("event=fp6_stock_authentication_waiting touch_sensor_now=true\n");
    for (i = 0; i < 120 && !authentication_done && !authentication_error; ++i)
        sleep(1);
    if (!authentication_done || authentication_error) {
        EMIT("event=fp6_stock_authentication_fail reason=no_match_or_timeout\n");
        _exit(61);
    }
    EMIT("event=fp6_stock_authentication_pass match=secure_match_on_chip raw_images=0\n");
    if (close_fingerprint_session(session) != 0) {
        EMIT("event=fp6_stock_authentication_fail reason=session_close\n");
        _exit(62);
    }
    _exit(0);
#else
    rc = AIBinder_prepareTransaction(session, &in);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(session, FIRST_CALL_TRANSACTION, &in, &out, 0);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out)) {
        EMIT("event=fp6_stock_enrollment_fail reason=generate_challenge\n");
        _exit(46);
    }
    AParcel_delete(out);
    out = NULL;
    for (i = 0; i < 15 && challenge_state == 0; ++i) sleep(1);
    if (challenge_state != 1) {
        EMIT("event=fp6_stock_enrollment_fail reason=challenge_callback\n");
        _exit(47);
    }
    credential_state = load_credential(&credential);
    if (credential_state < 0) {
        EMIT("event=fp6_stock_enrollment_fail reason=credential_record\n");
        _exit(48);
    }
    if (credential_state > 0) {
        if (gatekeeper_delete_primary_user(gatekeeper_service) != 0) {
            clear_bytes(pin, sizeof(pin));
            EMIT("event=fp6_stock_enrollment_fail reason=gatekeeper_user_reset\n");
            _exit(49);
        }
        EMIT("event=fp6_gatekeeper_primary_user_reset accepted=true\n");
        if (gatekeeper_enroll(gatekeeper_service, pin, pin_size, &credential) != 0) {
            clear_bytes(pin, sizeof(pin));
            emit_gatekeeper_enroll_diagnostic();
            EMIT("event=fp6_stock_enrollment_fail reason=gatekeeper_enroll\n");
            _exit(50);
        }
        EMIT("event=fp6_gatekeeper_credential_created protected=true\n");
    } else {
        EMIT("event=fp6_gatekeeper_credential_loaded protected=true\n");
    }
    if (gatekeeper_verify(gatekeeper_service, generated_challenge, &credential,
                          pin, pin_size, &hat) != 0) {
        clear_bytes(pin, sizeof(pin));
        EMIT("event=fp6_stock_enrollment_fail reason=gatekeeper_verify\n");
        _exit(51);
    }
    clear_bytes(pin, sizeof(pin));
    EMIT("event=fp6_gatekeeper_hat_verified challenge=matched contents_logged=0\n");
    rc = AIBinder_prepareTransaction(session, &in);
    if (rc == STATUS_OK) AParcel_markSensitive(in);
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, 1); /* QREL parcelable present */
    if (rc == STATUS_OK && write_hat(in, &hat) != 0) rc = -1;
    clear_bytes(&hat, sizeof(hat));
    if (rc == STATUS_OK)
        rc = AIBinder_transact(session, FIRST_CALL_TRANSACTION + 2,
                               &in, &out, FLAG_CLEAR_BUF);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out) ||
        AParcel_readStrongBinder(out, &cancellation) != STATUS_OK ||
        cancellation == NULL) {
        EMIT("event=fp6_stock_enrollment_fail reason=fingerprint_enroll\n");
        _exit(52);
    }
    AParcel_delete(out);
    out = NULL;
    EMIT("event=fp6_stock_enrollment_waiting touch_sensor_now=true\n");
    for (i = 0; i < 600 && !enrollment_done && !enrollment_error; ++i) sleep(1);
    if (!enrollment_done || enrollment_error || enrollment_id == 0) {
        EMIT("event=fp6_stock_enrollment_fail reason=acquisition_or_timeout\n");
        _exit(53);
    }
    EMIT("event=fp6_stock_enrollment_pass template=secure_match_on_chip raw_images=0\n");
#ifdef LUMA_PAUSE_AFTER_ENROLLMENT
    EMIT("event=fp6_post_enrollment_persistence_diagnostic_hold seconds=300\n");
    sleep(300);
#endif
#ifdef LUMA_ENROLL_THEN_AUTHENTICATE
    authentication_done = 0;
    authentication_error = 0;
    rc = AIBinder_prepareTransaction(session, &in);
    if (rc == STATUS_OK) rc = AParcel_writeInt64(in, 0);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(session, FIRST_CALL_TRANSACTION + 3,
                               &in, &out, 0);
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out) ||
        AParcel_readStrongBinder(out, &cancellation) != STATUS_OK ||
        cancellation == NULL) {
        EMIT("event=fp6_stock_authentication_fail reason=post_enrollment_authenticate\n");
        _exit(54);
    }
    AParcel_delete(out);
    out = NULL;
    EMIT("event=fp6_post_enrollment_authentication_waiting touch_sensor_now=true\n");
    for (i = 0; i < 120 && !authentication_done && !authentication_error; ++i)
        sleep(1);
    if (!authentication_done || authentication_error) {
        EMIT("event=fp6_stock_authentication_fail reason=post_enrollment_no_match_or_timeout\n");
        _exit(55);
    }
    EMIT("event=fp6_stock_authentication_pass match=secure_match_on_chip same_session=true raw_images=0\n");
#endif
    if (close_fingerprint_session(session) != 0) {
        EMIT("event=fp6_stock_enrollment_fail reason=session_close\n");
        _exit(56);
    }
    _exit(0);
#endif
}
