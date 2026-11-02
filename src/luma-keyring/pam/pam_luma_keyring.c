// SPDX-License-Identifier: MPL-2.0
/*
 * pam_luma_keyring: keeps the recovery copy of an automatic sign-in's
 * keyring key in step with the account password.
 *
 *     -password optional pam_luma_keyring.so
 *
 * placed in the passwd stack after the modules that set the password. In
 * the update phase it hands the new password to `luma-keyring --pam-rewrap`
 * on a socket, and only when the password change really happened:
 *
 * - It reads PAM_AUTHTOK with pam_get_item and never starts a conversation,
 *   so it cannot add a prompt. No token (a module before it failed and
 *   cleared it) is PAM_IGNORE.
 * - Optional lines still run after a failing substack, so a token alone
 *   proves nothing. The new password must verify against the account's own
 *   hash in /etc/shadow (crypt(3), libxcrypt) before anything is written.
 * - Accounts without a sealed keyring key are left alone without starting
 *   anything.
 *
 * It always returns PAM_IGNORE: it can never fail, or succeed, a password
 * change. The helper is bounded in time and is killed past the bound.
 */

#define _GNU_SOURCE
#include <crypt.h>
#include <errno.h>
#include <fcntl.h>
#include <pwd.h>
#include <shadow.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

#include <security/pam_ext.h>
#include <security/pam_modules.h>

#ifndef LUMA_KEYRING_HELPER
#define LUMA_KEYRING_HELPER "/usr/libexec/luma-keyring"
#endif

#define STATE_DIR ".local/state/luma-keyring"
#define HELPER_TIMEOUT_MS 20000
#define BUFFER_SIZE 16384

struct options {
	const char *helper;
	bool debug;
};

static void parse_options(pam_handle_t *pamh, int argc, const char **argv, struct options *options)
{
	options->helper = LUMA_KEYRING_HELPER;
	options->debug = false;
	for (int i = 0; i < argc; i++) {
		if (strcmp(argv[i], "debug") == 0)
			options->debug = true;
		else if (strncmp(argv[i], "helper=", 7) == 0 && argv[i][7] == '/')
			options->helper = argv[i] + 7;
		else
			pam_syslog(pamh, LOG_ERR, "unknown option: %s", argv[i]);
	}
}

/* Whether the account has a sealed keyring key (or a pending one). lstat
 * only: nothing in the account's home is opened as root here. */
static bool has_slots(const struct passwd *account)
{
	static const char *const names[] = { "login.cred", "login.cred.new" };
	char path[BUFFER_SIZE];
	struct stat st;

	for (size_t i = 0; i < sizeof(names) / sizeof(names[0]); i++) {
		int n = snprintf(path, sizeof(path), "%s/" STATE_DIR "/%s", account->pw_dir, names[i]);
		if (n < 0 || (size_t)n >= sizeof(path))
			continue;
		if (lstat(path, &st) == 0 && S_ISREG(st.st_mode) && st.st_uid == account->pw_uid)
			return true;
	}
	return false;
}

/* The account's own hash from /etc/shadow; NULL when it cannot be read. */
static char *account_hash(const char *user)
{
	struct spwd entry, *found = NULL;
	char buffer[BUFFER_SIZE];
	char *hash = NULL;

	if (getspnam_r(user, &entry, buffer, sizeof(buffer), &found) == 0 && found != NULL &&
	    found->sp_pwdp != NULL)
		hash = strdup(found->sp_pwdp);
	explicit_bzero(buffer, sizeof(buffer));
	return hash;
}

static bool equal_in_constant_time(const char *a, const char *b)
{
	size_t length_a = strlen(a), length_b = strlen(b);
	size_t length = length_a > length_b ? length_a : length_b;
	unsigned char difference = length_a != length_b;

	for (size_t i = 0; i < length; i++) {
		unsigned char x = i < length_a ? (unsigned char)a[i] : 0;
		unsigned char y = i < length_b ? (unsigned char)b[i] : 0;
		difference |= x ^ y;
	}
	return difference == 0;
}

/* Whether the new password is the account's password now. */
static bool password_is_set(pam_handle_t *pamh, const char *user, const char *token)
{
	char *hash = account_hash(user);
	bool set = false;

	if (hash == NULL) {
		pam_syslog(pamh, LOG_NOTICE, "no password hash to check for %s", user);
		return false;
	}
	/* A locked or empty entry cannot hold the new password. */
	if (hash[0] != '\0' && hash[0] != '!' && hash[0] != '*') {
		struct crypt_data *data = calloc(1, sizeof(*data));
		if (data != NULL) {
			const char *computed = crypt_rn(token, hash, data, sizeof(*data));
			set = computed != NULL && computed[0] != '*' && equal_in_constant_time(computed, hash);
			explicit_bzero(data, sizeof(*data));
			free(data);
		}
	}
	explicit_bzero(hash, strlen(hash));
	free(hash);
	return set;
}

static long elapsed_ms(const struct timespec *start)
{
	struct timespec now;

	clock_gettime(CLOCK_MONOTONIC, &now);
	return (now.tv_sec - start->tv_sec) * 1000L + (now.tv_nsec - start->tv_nsec) / 1000000L;
}

static int wait_bounded(pid_t child, int *status)
{
	struct timespec start, pause = { 0, 20 * 1000000L };

	clock_gettime(CLOCK_MONOTONIC, &start);
	for (;;) {
		pid_t done = waitpid(child, status, WNOHANG);
		if (done == child)
			return 0;
		if (done < 0 && errno != EINTR)
			return -1;
		if (elapsed_ms(&start) >= HELPER_TIMEOUT_MS) {
			kill(child, SIGKILL);
			while (waitpid(child, status, 0) < 0 && errno == EINTR)
				;
			errno = ETIMEDOUT;
			return -1;
		}
		nanosleep(&pause, NULL);
	}
}

/* Runs the helper with the new password on its standard input. */
static void run_helper(pam_handle_t *pamh, const char *user, const char *token,
                       const struct options *options)
{
	char user_variable[BUFFER_SIZE];
	int pair[2], status = 0;
	pid_t child;

	if (snprintf(user_variable, sizeof(user_variable), "PAM_USER=%s", user) >= (int)sizeof(user_variable))
		return;
	if (socketpair(AF_UNIX, SOCK_STREAM | SOCK_CLOEXEC, 0, pair) < 0) {
		pam_syslog(pamh, LOG_ERR, "socketpair: %m");
		return;
	}
	child = fork();
	if (child < 0) {
		pam_syslog(pamh, LOG_ERR, "fork: %m");
		close(pair[0]);
		close(pair[1]);
		return;
	}
	if (child == 0) {
		char *const args[] = { (char *)"luma-keyring", (char *)"--pam-rewrap", NULL };
		char *const environment[] = { (char *)"PATH=/usr/bin:/usr/sbin", (char *)"LC_ALL=C.UTF-8",
		                              (char *)"PAM_TYPE=password", user_variable, NULL };
		sigset_t none;
		int null = open("/dev/null", O_RDWR | O_CLOEXEC);

		sigemptyset(&none);
		sigprocmask(SIG_SETMASK, &none, NULL);
		for (int signal_number = 1; signal_number < NSIG; signal_number++)
			signal(signal_number, SIG_DFL);
		if (null < 0 || dup2(pair[1], STDIN_FILENO) < 0 || dup2(null, STDOUT_FILENO) < 0 ||
		    dup2(null, STDERR_FILENO) < 0)
			_exit(127);
		/* passwd is set-user-ID root: act as root throughout. */
		if (geteuid() == 0 && (setresgid(0, 0, 0) < 0 || setresuid(0, 0, 0) < 0))
			_exit(127);
		if (chdir("/") < 0)
			_exit(127);
		execve(options->helper, args, environment);
		_exit(127);
	}
	close(pair[1]);
	{
		size_t length = strlen(token), sent = 0;
		while (sent < length) {
			ssize_t n = send(pair[0], token + sent, length - sent, MSG_NOSIGNAL);
			if (n < 0 && errno == EINTR)
				continue;
			if (n <= 0)
				break;
			sent += (size_t)n;
		}
		shutdown(pair[0], SHUT_WR);
		close(pair[0]);
	}
	if (wait_bounded(child, &status) < 0) {
		pam_syslog(pamh, LOG_ERR, "luma-keyring did not finish: %m");
		return;
	}
	if (WIFEXITED(status) && WEXITSTATUS(status) == 0) {
		if (options->debug)
			pam_syslog(pamh, LOG_DEBUG, "recovery copy of %s re-wrapped", user);
	} else {
		pam_syslog(pamh, LOG_WARNING, "recovery copy of %s not re-wrapped (status %d); "
		           "the session asks for the new password", user, status);
	}
}

PAM_EXTERN __attribute__((visibility("default"))) int
pam_sm_chauthtok(pam_handle_t *pamh, int flags, int argc, const char **argv)
{
	const void *token = NULL, *user = NULL;
	struct passwd account, *found = NULL;
	char buffer[BUFFER_SIZE];
	struct options options;

	parse_options(pamh, argc, argv, &options);
	if (!(flags & PAM_UPDATE_AUTHTOK))
		return PAM_IGNORE;
	if (pam_get_item(pamh, PAM_AUTHTOK, &token) != PAM_SUCCESS || token == NULL ||
	    ((const char *)token)[0] == '\0') {
		if (options.debug)
			pam_syslog(pamh, LOG_DEBUG, "no new password; nothing to do");
		return PAM_IGNORE;
	}
	if (pam_get_item(pamh, PAM_USER, &user) != PAM_SUCCESS || user == NULL ||
	    ((const char *)user)[0] == '\0')
		return PAM_IGNORE;
	if (getpwnam_r(user, &account, buffer, sizeof(buffer), &found) != 0 || found == NULL)
		return PAM_IGNORE;
	if (!has_slots(&account))
		return PAM_IGNORE;
	if (!password_is_set(pamh, user, token)) {
		pam_syslog(pamh, LOG_NOTICE, "password of %s not changed; recovery copy kept",
		           (const char *)user);
		return PAM_IGNORE;
	}
	run_helper(pamh, user, token, &options);
	return PAM_IGNORE;
}
