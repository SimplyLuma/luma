/* SPDX-License-Identifier: Apache-2.0 */
#include "luma-enums.h"

G_DEFINE_ENUM_TYPE(LumaActionRisk, luma_action_risk,
                   G_DEFINE_ENUM_VALUE(LUMA_ACTION_RISK_PASSIVE, "passive"),
                   G_DEFINE_ENUM_VALUE(LUMA_ACTION_RISK_LOW, "low"),
                   G_DEFINE_ENUM_VALUE(LUMA_ACTION_RISK_CONSEQUENTIAL,
                                       "consequential"),
                   G_DEFINE_ENUM_VALUE(LUMA_ACTION_RISK_DESTRUCTIVE,
                                       "destructive"),
                   G_DEFINE_ENUM_VALUE(LUMA_ACTION_RISK_SECURITY_SENSITIVE,
                                       "security-sensitive"))

G_DEFINE_ENUM_TYPE(LumaPrivacy, luma_privacy,
                   G_DEFINE_ENUM_VALUE(LUMA_PRIVACY_PUBLIC, "public"),
                   G_DEFINE_ENUM_VALUE(LUMA_PRIVACY_PRIVATE, "private"),
                   G_DEFINE_ENUM_VALUE(LUMA_PRIVACY_SENSITIVE, "sensitive"),
                   G_DEFINE_ENUM_VALUE(LUMA_PRIVACY_SECRET, "secret"))

G_DEFINE_ENUM_TYPE(
    LumaLiveCategory, luma_live_category,
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_CALL, "call"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_MEDIA, "media"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_TIMER, "timer"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_NAVIGATION, "navigation"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_EVENT, "event"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_TRANSFER, "transfer"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_RECORDING, "recording"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_INSTALLATION, "installation"),
    G_DEFINE_ENUM_VALUE(LUMA_LIVE_CATEGORY_GENERIC, "generic"))

const char *luma_action_risk_to_string(LumaActionRisk value) {
  switch (value) {
  case LUMA_ACTION_RISK_PASSIVE:
    return "passive";
  case LUMA_ACTION_RISK_LOW:
    return "low";
  case LUMA_ACTION_RISK_CONSEQUENTIAL:
    return "consequential";
  case LUMA_ACTION_RISK_DESTRUCTIVE:
    return "destructive";
  case LUMA_ACTION_RISK_SECURITY_SENSITIVE:
    return "security-sensitive";
  default:
    return "low";
  }
}

const char *luma_privacy_to_string(LumaPrivacy value) {
  switch (value) {
  case LUMA_PRIVACY_PUBLIC:
    return "public";
  case LUMA_PRIVACY_PRIVATE:
    return "private";
  case LUMA_PRIVACY_SENSITIVE:
    return "sensitive";
  case LUMA_PRIVACY_SECRET:
    return "secret";
  default:
    return "secret";
  }
}

const char *luma_live_category_to_string(LumaLiveCategory value) {
  switch (value) {
  case LUMA_LIVE_CATEGORY_CALL:
    return "call";
  case LUMA_LIVE_CATEGORY_MEDIA:
    return "media";
  case LUMA_LIVE_CATEGORY_TIMER:
    return "timer";
  case LUMA_LIVE_CATEGORY_NAVIGATION:
    return "navigation";
  case LUMA_LIVE_CATEGORY_EVENT:
    return "event";
  case LUMA_LIVE_CATEGORY_TRANSFER:
    return "transfer";
  case LUMA_LIVE_CATEGORY_RECORDING:
    return "recording";
  case LUMA_LIVE_CATEGORY_INSTALLATION:
    return "installation";
  case LUMA_LIVE_CATEGORY_GENERIC:
    return "generic";
  default:
    return "generic";
  }
}
