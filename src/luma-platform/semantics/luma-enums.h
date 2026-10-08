/* SPDX-License-Identifier: Apache-2.0 */
#pragma once

#include <glib-object.h>

G_BEGIN_DECLS

typedef enum {
  LUMA_ACTION_RISK_PASSIVE,
  LUMA_ACTION_RISK_LOW,
  LUMA_ACTION_RISK_CONSEQUENTIAL,
  LUMA_ACTION_RISK_DESTRUCTIVE,
  LUMA_ACTION_RISK_SECURITY_SENSITIVE,
} LumaActionRisk;

typedef enum {
  LUMA_PRIVACY_PUBLIC,
  LUMA_PRIVACY_PRIVATE,
  LUMA_PRIVACY_SENSITIVE,
  LUMA_PRIVACY_SECRET,
} LumaPrivacy;

typedef enum {
  LUMA_LIVE_CATEGORY_CALL,
  LUMA_LIVE_CATEGORY_MEDIA,
  LUMA_LIVE_CATEGORY_TIMER,
  LUMA_LIVE_CATEGORY_NAVIGATION,
  LUMA_LIVE_CATEGORY_EVENT,
  LUMA_LIVE_CATEGORY_TRANSFER,
  LUMA_LIVE_CATEGORY_RECORDING,
  LUMA_LIVE_CATEGORY_INSTALLATION,
  LUMA_LIVE_CATEGORY_GENERIC,
} LumaLiveCategory;

#define LUMA_TYPE_ACTION_RISK (luma_action_risk_get_type())
#define LUMA_TYPE_PRIVACY (luma_privacy_get_type())
#define LUMA_TYPE_LIVE_CATEGORY (luma_live_category_get_type())

GType luma_action_risk_get_type(void) G_GNUC_CONST;
GType luma_privacy_get_type(void) G_GNUC_CONST;
GType luma_live_category_get_type(void) G_GNUC_CONST;
const char *luma_action_risk_to_string(LumaActionRisk value);
const char *luma_privacy_to_string(LumaPrivacy value);
const char *luma_live_category_to_string(LumaLiveCategory value);

G_END_DECLS
