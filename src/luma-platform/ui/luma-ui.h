/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include "luma-action-bar.h"
#include "luma-application-icon.h"
#include "luma-adaptive-scaffold.h"
#include "luma-application-window.h"
#include "luma-pane.h"
#include "luma-island.h"
#include "luma-title-island.h"
#include "luma-list-first.h"
#include "luma-tab-bar.h"
#include "luma-navigation-sidebar.h"
#include "luma-media-fader.h"
#include "luma-audio-meter.h"
#include "luma-segmented-control.h"
#include "luma-inspector-section.h"
#include "luma-histogram.h"
#include "luma-curve-editor.h"
#include "luma-color-well.h"
#include "luma-context.h"
#include "luma-surface-backdrop.h"
#include "luma-save-sheet.h"

/* LumaUI parts (ADR-052): the C twins of luma_appkit's structure_*, action_*
 * and content_* parts, with the same widget trees and CSS classes. */
#include "luma-ui-kit.h"
#include "luma-layer-host.h"
#include "luma-mode-switch.h"
#include "luma-corner-pill.h"
#include "luma-sidebar-foot.h"
#include "luma-sidebar-toggle.h"
#include "luma-badges.h"
#include "luma-toast.h"
#include "luma-destructive-dialog.h"
#include "luma-stacked-button.h"
#include "luma-controls.h"
#include "luma-choice-list.h"
#include "luma-progress-line.h"
#include "luma-details-pane.h"
#include "luma-table-header.h"
#include "luma-action-center.h"
#include "luma-cards.h"
#include "luma-file-card.h"
#include "luma-selection-bubble.h"
#include "luma-navigation-trail-bar.h"
#include "luma-menu-drawer.h"
#include "luma-contact.h"
#include "luma-text-field.h"
#include "luma-place-search.h"
#include "luma-message-bubble.h"
#include "luma-type-label.h"
#include "luma-media-transport-lcd.h"
#include "luma-creative-workspace.h"
#include "luma-timeline.h"

G_BEGIN_DECLS

void luma_init(void);

/* Load an application's style sheet from a GResource and keep it in step with
 * the treatment: it is parsed again whenever the AppKit tokens change, so the
 * AppKit colours it names always resolve to the current treatment's values. */
void luma_ui_add_style_resource(const char *resource_path, guint priority);

G_END_DECLS

#include "luma-welcome-view.h"

#include "luma-empty-state.h"

#include "luma-reorder-hint.h"

#include "luma-creative-tools.h"
#include "luma-creative-layers.h"
#include "luma-creative-properties.h"
