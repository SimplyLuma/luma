/* SPDX-License-Identifier: Apache-2.0 */
#pragma once
#include <adwaita.h>
G_BEGIN_DECLS
/**
 * luma_empty_state_configure:
 * @page: the application's native status page
 * @compact: whether this is a compact inspector or picker state
 *
 * Applies the shared empty-state presentation without replacing the page's
 * title, description, accessibility, child action, or state ownership.
 */
void luma_empty_state_configure(AdwStatusPage *page, gboolean compact);
G_END_DECLS
