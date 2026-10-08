/*
 * Exact-stock Android Binder challenge probe for the Fairphone 6.
 *
 * This is deliberately a constructor-only shared object.  It is compiled as
 * an AArch64 ELF DSO and preloaded into the QREL 16.95.0 /system/bin/sh so the
 * phone's own Bionic linker and libbinder_ndk implement the wire ABI.  The
 * probe creates a fingerprint session and asks only for a random challenge;
 * it never starts acquisition, reads the sensor, or writes credential state.
 */

#include <stdbool.h>
#include <stdint.h>
#include <stddef.h>

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
};

typedef void *(*AIBinder_Class_onCreate)(void *args);
typedef void (*AIBinder_Class_onDestroy)(void *user_data);
typedef binder_status_t (*AIBinder_Class_onTransact)(
    AIBinder *binder, transaction_code_t code, const AParcel *in, AParcel *out);

extern AIBinder *AServiceManager_waitForService(const char *instance);
extern AIBinder_Class *AIBinder_Class_define(
    const char *descriptor, AIBinder_Class_onCreate on_create,
    AIBinder_Class_onDestroy on_destroy, AIBinder_Class_onTransact on_transact);
extern AIBinder *AIBinder_new(const AIBinder_Class *clazz, void *args);
extern bool AIBinder_associateClass(AIBinder *binder, const AIBinder_Class *clazz);
extern void AIBinder_markVintfStability(AIBinder *binder);
extern void AIBinder_incStrong(AIBinder *binder);
extern void AIBinder_decStrong(AIBinder *binder);
extern binder_status_t AIBinder_prepareTransaction(AIBinder *binder, AParcel **in);
extern binder_status_t AIBinder_transact(AIBinder *binder, transaction_code_t code,
                                         AParcel **in, AParcel **out,
                                         binder_flags_t flags);
extern void ABinderProcess_startThreadPool(void);
extern binder_status_t AParcel_writeInt32(AParcel *parcel, int32_t value);
extern binder_status_t AParcel_writeInt64(AParcel *parcel, int64_t value);
extern binder_status_t AParcel_writeStrongBinder(AParcel *parcel, AIBinder *binder);
extern binder_status_t AParcel_writeString(AParcel *parcel, const char *string,
                                           int32_t length);
extern binder_status_t AParcel_readInt64(const AParcel *parcel, int64_t *value);
extern binder_status_t AParcel_readStrongBinder(const AParcel *parcel,
                                                AIBinder **binder);
extern binder_status_t AParcel_writeStatusHeader(AParcel *parcel,
                                                 const AStatus *status);
extern binder_status_t AParcel_readStatusHeader(const AParcel *parcel,
                                                AStatus **status);
extern AStatus *AStatus_newOk(void);
extern bool AStatus_isOk(const AStatus *status);
extern void AStatus_delete(AStatus *status);
extern void AParcel_delete(AParcel *parcel);

extern long write(int fd, const void *buf, unsigned long count);
extern unsigned int sleep(unsigned int seconds);
extern void _exit(int status);

static volatile int challenge_seen;

static void emit(const char *s, unsigned long n) {
    (void)write(1, s, n);
}

#define EMIT(literal) emit((literal), sizeof(literal) - 1)

static void *callback_create(void *args) { return args; }
static void callback_destroy(void *user_data) { (void)user_data; }

static binder_status_t write_ok(AParcel *out) {
    AStatus *status = AStatus_newOk();
    binder_status_t rc;
    if (status == NULL) return -1;
    rc = AParcel_writeStatusHeader(out, status);
    AStatus_delete(status);
    return rc;
}

static binder_status_t callback_transact(AIBinder *binder,
                                         transaction_code_t code,
                                         const AParcel *in, AParcel *out) {
    (void)binder;
    if (code == FIRST_CALL_TRANSACTION) {
        int64_t challenge = 0;
        if (AParcel_readInt64(in, &challenge) != STATUS_OK) return -1;
        if (write_ok(out) != STATUS_OK) return -1;
        challenge_seen = challenge != 0 ? 1 : 2;
        return STATUS_OK;
    }
    if (code == TRANSACTION_GET_INTERFACE_VERSION) {
        if (write_ok(out) != STATUS_OK) return -1;
        return AParcel_writeInt32(out, 3);
    }
    if (code == TRANSACTION_GET_INTERFACE_HASH) {
        /* The service does not need this for createSession/challenge. */
        if (write_ok(out) != STATUS_OK) return -1;
        return AParcel_writeString(out, "", 0);
    }
    return STATUS_UNKNOWN_TRANSACTION;
}

static int status_reply_ok(AParcel *out) {
    AStatus *status = NULL;
    int ok = 0;
    if (AParcel_readStatusHeader(out, &status) == STATUS_OK && status != NULL)
        ok = AStatus_isOk(status) ? 1 : 0;
    if (status != NULL) AStatus_delete(status);
    return ok;
}

__attribute__((constructor)) static void run_probe(void) {
    static const char fp_desc[] =
        "android.hardware.biometrics.fingerprint.IFingerprint";
    static const char session_desc[] =
        "android.hardware.biometrics.fingerprint.ISession";
    static const char callback_desc[] =
        "android.hardware.biometrics.fingerprint.ISessionCallback";
    static const char service_name[] =
        "android.hardware.biometrics.fingerprint.IFingerprint/default";
    AIBinder_Class *fp_class = NULL;
    AIBinder_Class *session_class = NULL;
    AIBinder_Class *callback_class = NULL;
    AIBinder *service = NULL;
    AIBinder *callback = NULL;
    AIBinder *session = NULL;
    AParcel *in = NULL;
    AParcel *out = NULL;
    binder_status_t rc;
    int i;

    EMIT("event=fp6_challenge_probe_start\n");
    ABinderProcess_startThreadPool();

    fp_class = AIBinder_Class_define(fp_desc, callback_create,
                                     callback_destroy, callback_transact);
    session_class = AIBinder_Class_define(session_desc, callback_create,
                                          callback_destroy, callback_transact);
    callback_class = AIBinder_Class_define(callback_desc, callback_create,
                                           callback_destroy, callback_transact);
    if (fp_class == NULL || session_class == NULL || callback_class == NULL) {
        EMIT("event=fp6_challenge_probe_fail reason=class_define\n");
        _exit(20);
    }

    service = AServiceManager_waitForService(service_name);
    if (service == NULL || !AIBinder_associateClass(service, fp_class)) {
        EMIT("event=fp6_challenge_probe_fail reason=service_lookup\n");
        _exit(21);
    }
    callback = AIBinder_new(callback_class, NULL);
    if (callback == NULL) {
        EMIT("event=fp6_challenge_probe_fail reason=callback_create\n");
        _exit(22);
    }
    AIBinder_markVintfStability(callback);

    rc = AIBinder_prepareTransaction(service, &in);
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, 0); /* sensorId */
    if (rc == STATUS_OK) rc = AParcel_writeInt32(in, 0); /* userId */
    if (rc == STATUS_OK) rc = AParcel_writeStrongBinder(in, callback);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(service, FIRST_CALL_TRANSACTION + 1,
                               &in, &out, 0); /* createSession */
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out) ||
        AParcel_readStrongBinder(out, &session) != STATUS_OK || session == NULL) {
        EMIT("event=fp6_challenge_probe_fail reason=create_session\n");
        _exit(23);
    }
    AParcel_delete(out);
    out = NULL;
    if (!AIBinder_associateClass(session, session_class)) {
        EMIT("event=fp6_challenge_probe_fail reason=session_class\n");
        _exit(24);
    }

    rc = AIBinder_prepareTransaction(session, &in);
    if (rc == STATUS_OK)
        rc = AIBinder_transact(session, FIRST_CALL_TRANSACTION,
                               &in, &out, 0); /* generateChallenge */
    if (rc != STATUS_OK || out == NULL || !status_reply_ok(out)) {
        EMIT("event=fp6_challenge_probe_fail reason=generate_challenge\n");
        _exit(25);
    }
    AParcel_delete(out);
    out = NULL;

    for (i = 0; i < 15 && challenge_seen == 0; ++i) sleep(1);
    if (challenge_seen == 1) {
        EMIT("event=fp6_challenge_probe_pass challenge=received_nonzero persistent_writes=0 acquisition=0\n");
        _exit(0);
    }
    if (challenge_seen == 2) {
        EMIT("event=fp6_challenge_probe_fail reason=zero_challenge\n");
        _exit(26);
    }
    EMIT("event=fp6_challenge_probe_fail reason=callback_timeout\n");
    _exit(27);
}
