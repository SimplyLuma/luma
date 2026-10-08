# SPDX-License-Identifier: Apache-2.0
# lumaui-conform: loads the capture harness into the app process, and only
# when LUMAUI_CONFORM_DUMP is set. Lives in a scratch copy of the preview.
import os

if os.environ.get("LUMAUI_CONFORM_DUMP"):
    import lumaui_conform_harness

    lumaui_conform_harness.install()
