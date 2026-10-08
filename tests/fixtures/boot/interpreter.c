/* SPDX-License-Identifier: Apache-2.0 */
/* Executes theme logic with upstream Plymouth's interpreter, not a JS port.
 * Renderer mocks do not establish DRM, initramfs, or physical-device evidence. */
#include <stdio.h>
#include <stdlib.h>
#include "script.h"
#include "script-parse.h"
#include "script-execute.h"
#include "script-object.h"
#include "script-lib-math.h"
#include "script-lib-string.h"
static script_return_t check(script_state_t *state, void *unused) {
    script_obj_t *condition = script_obj_hash_get_element(state->local, "condition");
    if (!script_obj_as_bool(condition)) {
        char *label = script_obj_hash_get_string(state->local, "label");
        fprintf(stderr, "FAIL: %s\n", label); free(label); exit(1);
    }
    script_obj_unref(condition);
    return script_return_obj_null();
}
int main(int argc, char **argv) {
    script_state_t *state = script_state_new(NULL);
    script_lib_math_setup(state);
    script_lib_string_setup(state);
    script_add_native_function(state->global, "Assert", check, NULL, "condition", "label", NULL);
    for (int i = 1; i < argc; i++) {
        script_op_t *op = script_parse_file(argv[i]);
        if (!op) { fprintf(stderr, "PARSE FAILED: %s\n", argv[i]); return 2; }
        script_return_t result = script_execute(state, op);
        script_obj_unref(result.object);
        /* Keep parsed functions alive for later callback dispatch. */
    }
    puts("Plymouth interpreter behavior: PASS");
    return 0;
}
