// SPDX-License-Identifier: MPL-2.0
/*
 * chauthtok SERVICE USER [root]: pam_chauthtok as passwd runs it, for the tests.
 *
 * Run as root. Like set-user-ID passwd run by USER, it keeps an effective
 * uid of 0 and takes the real uid of USER, so pam_unix asks for the current
 * password and pam_pwquality enforces its rules. With "root", the real uid
 * stays 0: an administrator changing USER's password. Answers come from standard input, one
 * per line; every prompt is printed as "prompt: TEXT", then "result: N".
 * The exit status is the PAM result.
 */

#define _GNU_SOURCE
#include <pwd.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include <security/pam_appl.h>

static int conversation(int count, const struct pam_message **messages,
                        struct pam_response **responses, void *data)
{
	struct pam_response *reply = calloc((size_t)count, sizeof(*reply));
	(void)data;

	if (reply == NULL)
		return PAM_BUF_ERR;
	for (int i = 0; i < count; i++) {
		const struct pam_message *message = messages[i];
		if (message->msg_style == PAM_PROMPT_ECHO_OFF || message->msg_style == PAM_PROMPT_ECHO_ON) {
			char *line = NULL;
			size_t size = 0;
			ssize_t length;

			printf("prompt: %s\n", message->msg);
			fflush(stdout);
			length = getline(&line, &size, stdin);
			if (length < 0) {
				free(line);
				free(reply);
				return PAM_CONV_ERR;
			}
			if (length > 0 && line[length - 1] == '\n')
				line[length - 1] = '\0';
			reply[i].resp = line;
		} else {
			printf("message: %s\n", message->msg);
		}
	}
	*responses = reply;
	return PAM_SUCCESS;
}

int main(int argc, char **argv)
{
	struct pam_conv conv = { conversation, NULL };
	pam_handle_t *pamh = NULL;
	struct passwd *account;
	int result;

	if (argc != 3 && !(argc == 4 && strcmp(argv[3], "root") == 0)) {
		fprintf(stderr, "usage: %s SERVICE USER [root]\n", argv[0]);
		return 2;
	}
	account = getpwnam(argv[2]);
	if (account == NULL || (argc == 3 && setresuid(account->pw_uid, 0, 0) != 0)) {
		perror("setresuid");
		return 2;
	}
	result = pam_start(argv[1], argv[2], &conv, &pamh);
	if (result == PAM_SUCCESS)
		result = pam_chauthtok(pamh, 0);
	printf("result: %d\n", result);
	pam_end(pamh, result);
	return result;
}
