#include "mot_config.h"
#include <linux/string.h>
/* for reading moto bootargs */
#include <linux/of.h>

// BEGIN IKSWR-130356
typedef struct moto_product {
	char hw_device[ARRAY_VALUE_MAX];
	char hw_radio[ARRAY_VALUE_MAX];
	char wifi_cfg_name[ARRAY_VALUE_MAX];
	char txpowerctrl_name[ARRAY_VALUE_MAX];
	char vlp_cfg_name[ARRAY_VALUE_MAX];
} moto_product;

static moto_product products_list[] = {
		{"aito",   "NA",     "wifi",   "txpowerctrl_na"},
                {"aito",   "JP",     "wifi",   "txpowerctrl_JP"},
                {"aito",   "ROW",    "wifi",   "txpowerctrl_ROW"},
                {"aito",   "DOCOMO", "wifi",   "txpowerctrl_DOCOMO"},
		{"aito",   "all",    "wifi",   "txpowerctrl"},
		{"vienna",   "LATAM",    "wifi",   "txpowerctrl_LATAM"},
		{"vienna",   "APEM",    "wifi",   "txpowerctrl_APEM"},
		{"vienna",   "all",    "wifi",   "txpowerctrl"},
		{"nice",   "INDIA",    "wifi",   "txpowerctrl_INDIA"},
		{"nice",   "INDSA",    "wifi",   "txpowerctrl_INDSA"},
		{"nice",   "OTLTM",    "wifi",   "txpowerctrl_OTLTM"},
		{"nice",   "OTAPM",    "wifi",   "txpowerctrl_OTAPM"},
		{"nice",   "LATAM",    "wifi",   "txpowerctrl_LATAM"},
		{"nice",   "BRLAP",    "wifi",   "txpowerctrl_BRLAP"},
		{"nice",   "APEM",    "wifi",   "txpowerctrl_APEM"},
		{"nice",   "PRC",    "wifi_prc",   "txpowerctrl_PRC"},
		{"nice",   "all",    "wifi",   "txpowerctrl"},
		{"cybert",   "INDIA",  "wifi",   "txpowerctrl_INDIA"},
		{"cybert",   "ROW",    "wifi",   "txpowerctrl_ROW"},
		{"cybert",   "JAPAN",    "wifi",   "txpowerctrl_JAPAN"},
		{"cybert",   "all",    "wifi",   "txpowerctrl"},
		{"fuji",   "all",    "wifi",   "txpowerctrl", "vlp"},
		{{0},        {0},   {0},	{0}},
};

extern int mmi_get_bootarg(char *key, char **value);
extern void mmi_free_bootarg_res(void);

void get_moto_config_file_name(char* name, WIFI_CFG_ENUM index)
{
	char device[ARRAY_VALUE_MAX] = { 0 };
	char radio[ARRAY_VALUE_MAX] = { 0 };
	int num = 0;
	int i = 0;
	char *s;

	if (mmi_get_bootarg("androidboot.device=", &s) == 0) {
		memcpy(device, s, strlen(s));
		DBGLOG(RLM, INFO, "[MOTO]bootargs get device: %s\n", device);
		mmi_free_bootarg_res();
	}
	if (mmi_get_bootarg("androidboot.radio=", &s) == 0) {
		memcpy(radio, s, strlen(s));
		DBGLOG(RLM, INFO, "[MOTO]bootargs get radio: %s\n", radio);
		mmi_free_bootarg_res();
	}

    num = sizeof(products_list) / sizeof(moto_product);
    for (i = 0; i < num; i++) {
        if (strncmp(device, (products_list + i)->hw_device, ARRAY_VALUE_MAX) == 0) {
            if (strncmp(radio, (products_list + i)->hw_radio, ARRAY_VALUE_MAX) == 0 ||
                strncmp((products_list + i)->hw_radio, "all", ARRAY_VALUE_MAX) == 0) {
                if (index == WIFI_CFG_INDEX) {
                    snprintf(name, ARRAY_VALUE_MAX, "%s.cfg", (products_list + i)->wifi_cfg_name);
                } else if (index == VLP_CFG_INDEX) {
                    snprintf(name, ARRAY_VALUE_MAX, "%s.cfg", (products_list + i)->vlp_cfg_name);
                }
                else {
                    snprintf(name, ARRAY_VALUE_MAX, "%s.cfg", (products_list + i)->txpowerctrl_name);
                }
                DBGLOG(RLM, ERROR, "[MOTO]Use moto config file name: %s\n", name);
                return;
            }
        }
    }

}
// END IKSWR-130356
