// SPDX-License-Identifier: GPL-2.0
/*
 * Copyright (C) 2015 MediaTek Inc.
 */
#include <linux/kernel.h>
#include <linux/delay.h>
#include <linux/gpio.h>
#include <linux/irq.h>
#include <linux/interrupt.h>
#include <linux/of.h>
#include <linux/of_fdt.h>
#include <linux/of_irq.h>
#include <linux/of_address.h>
#include <linux/of_gpio.h>

#include "ccci_debug.h"
#include "ccci_config.h"
#include "ccci_common_config.h"
#include "ccci_modem.h"
#include "ccci_swtp.h"
#include "ccci_fsm.h"
#ifdef CONFIG_MOTO_SWTP_CUST
#include "ccci_core.h"
#endif

/* must keep ARRAY_SIZE(swtp_of_match) = ARRAY_SIZE(irq_name) */
const struct of_device_id swtp_of_match[] = {
	{ .compatible = SWTP_COMPATIBLE_DEVICE_ID, },
	{ .compatible = SWTP1_COMPATIBLE_DEVICE_ID,},
	{ .compatible = SWTP2_COMPATIBLE_DEVICE_ID,},
	{ .compatible = SWTP3_COMPATIBLE_DEVICE_ID,},
	{ .compatible = SWTP4_COMPATIBLE_DEVICE_ID,},
	{},
};

static const char irq_name[][16] = {
	"swtp0-eint",
	"swtp1-eint",
	"swtp2-eint",
	"swtp3-eint",
	"swtp4-eint",
	"",
};

struct swtp_t swtp_data;
static const char rf_name[] = "RF_cable";
#define MAX_RETRY_CNT 30

#if defined(CONFIG_MOTO_SWTP_CUST)
static int swtp_tx_power_mode = SWTP_DO_TX_POWER;
static ssize_t swtp_gpio_state_show(struct class *class,
		struct class_attribute *attr,
		char *buf)
{
	int ret = 0;

	if (swtp_tx_power_mode == SWTP_NO_TX_POWER) {
		ret = 1;
	}

	return sprintf(buf, "%d\n", ret);
}

static CLASS_ATTR_RO(swtp_gpio_state);

static struct class swtp_class = {
	.name			= "swtp",
	.owner			= THIS_MODULE,
};

struct rfcable_state_t {
	int	last_swtp_tx_power_mode;
	bool	is_needed_to_reset_md;
	struct	delayed_work	rfcable_delayed_work;
};

static struct rfcable_state_t rfcable_state;

static void reboot_md_delayed_work(struct work_struct *work)
{
	if(rfcable_state.is_needed_to_reset_md && rfcable_state.last_swtp_tx_power_mode != swtp_tx_power_mode){
		ccci_fsm_ioctl(CCCI_IOC_MD_RESET, 0);
		rfcable_state.is_needed_to_reset_md = false;
		rfcable_state.last_swtp_tx_power_mode = swtp_tx_power_mode;
		CCCI_NORMAL_LOG(0, SYS, "%s reset MD\n", __func__);
	}
}

static void rfcable_state_update(void)
{
	if(rfcable_state.is_needed_to_reset_md && rfcable_state.last_swtp_tx_power_mode != swtp_tx_power_mode){
		schedule_delayed_work(&rfcable_state.rfcable_delayed_work, 2 * HZ);
		CCCI_NORMAL_LOG(0, SYS, "%s schedule reset MD\n", __func__);
	}
}

void set_rfcable_monitor_state(bool state)
{
	rfcable_state.is_needed_to_reset_md = state;
	if(rfcable_state.is_needed_to_reset_md == true && rfcable_state.last_swtp_tx_power_mode == -1)
		rfcable_state.last_swtp_tx_power_mode = swtp_tx_power_mode;//initial value for first bootup
}

void force_modem_reset(void)
{
	ccci_fsm_ioctl(CCCI_IOC_MD_RESET, 0);
}

unsigned int get_rfcable_state(void)
{
	unsigned int ret = 0;
#ifdef CONFIG_TARGET_BUILD_FACTORY
	if (swtp_tx_power_mode == SWTP_NO_TX_POWER) {
		ret = 1;
	}
	else{
		ret = 0;
	}
#else
	if (swtp_tx_power_mode == SWTP_NO_TX_POWER) {
		ret = 3;
	}
	else{
		ret = 2;
	}
#endif

	return ret;
}
#endif

#ifdef CONFIG_MOTO_SWTP_CUST
static bool bypass_rfcable_detect = false;
int mmi_get_bootarg(char *key, char *value, int len)
{
	const char *bootargs_tmp = NULL;
	char *idx = NULL;
	char *kvpair = NULL;
	int err = 1;
	struct device_node *n = of_find_node_by_path("/chosen");
	size_t bootargs_tmp_len = 0;
	char *bootargs_str = NULL;
	char *found_value = NULL;

	if (n == NULL)
		goto err;

	if (of_property_read_string(n, "mmi,bootconfig", &bootargs_tmp) != 0)
		goto putnode;

	bootargs_tmp_len = strlen(bootargs_tmp);
	/* The following operations need a non-const
	 * version of bootargs
	 */
	bootargs_str = kzalloc(bootargs_tmp_len + 1, GFP_KERNEL);
	if (!bootargs_str)
		goto putnode;

	strlcpy(bootargs_str, bootargs_tmp, bootargs_tmp_len + 1);

	idx = strnstr(bootargs_str, key, strlen(bootargs_str));
	if (idx) {
		kvpair = strsep(&idx, " ");
		if (kvpair)
			if (strsep(&kvpair, "=")) {
				found_value = strsep(&kvpair, "\n");
				if (found_value) {
					strlcpy(value, found_value, len);
					pr_info("mmi_get_bootarg: %s", found_value);
                                        err = 0;
				}
			}
	}

	if (bootargs_str)
	    kfree(bootargs_str);
putnode:
	of_node_put(n);
err:
	return err;
}
#endif

static int swtp_send_tx_power(struct swtp_t *swtp)
{
	unsigned long flags;
	int power_mode, ret = 0;
#ifdef CONFIG_MOTO_DISABLE_SWTP_FACTORY
	int factory_tx_power_mode = SWTP_NO_TX_POWER;
#endif
	if (swtp == NULL) {
		CCCI_LEGACY_ERR_LOG(-1, SYS, "%s:swtp is null\n", __func__);
		return -1;
	}

	spin_lock_irqsave(&swtp->spinlock, flags);

#ifdef CONFIG_MOTO_DISABLE_SWTP_FACTORY
// FACTORY SW: SET NO_TX directly
	ret = exec_ccci_kern_func(ID_UPDATE_TX_POWER,
		(char *)&factory_tx_power_mode, sizeof(factory_tx_power_mode));
	power_mode = factory_tx_power_mode;

#elif defined (CONFIG_MOTO_SWTP_CUST)
// Customer SW:  Check bypass RFcable flag and then set power accordingly
	CCCI_LEGACY_ERR_LOG(-1, SYS,
			"%s: bypass_rfcable_detect: %d\n", __func__ , bypass_rfcable_detect);
	if (bypass_rfcable_detect)
	{
		power_mode = SWTP_DO_TX_POWER; // always set DO tx power when bypass rfcable detect
		ret = exec_ccci_kern_func(ID_UPDATE_TX_POWER,
			(char *)&power_mode, sizeof(power_mode));
	}
	else
	{
		ret = exec_ccci_kern_func(ID_UPDATE_TX_POWER,
			(char *)&swtp->tx_power_mode, sizeof(swtp->tx_power_mode));
		power_mode = swtp->tx_power_mode;
	}
#else // MTK original
	ret = exec_ccci_kern_func(ID_UPDATE_TX_POWER,
		(char *)&swtp->tx_power_mode, sizeof(swtp->tx_power_mode));
	power_mode = swtp->tx_power_mode;
#endif
	spin_unlock_irqrestore(&swtp->spinlock, flags);

	if (ret != 0)
		CCCI_LEGACY_ERR_LOG(0, SYS,
			"%s to MD,state=%d,ret=%d\n",
			__func__, power_mode, ret);

	return ret;
}

static int swtp_switch_state(int irq, struct swtp_t *swtp)
{
	unsigned long flags;
	int i;

	if (swtp == NULL) {
		CCCI_LEGACY_ERR_LOG(-1, SYS, "%s:data is null\n", __func__);
		return -1;
	}

	spin_lock_irqsave(&swtp->spinlock, flags);
	for (i = 0; i < MAX_PIN_NUM; i++) {
		if (swtp->irq[i] == irq)
			break;
	}
	if (i == MAX_PIN_NUM) {
		spin_unlock_irqrestore(&swtp->spinlock, flags);
		CCCI_LEGACY_ERR_LOG(-1, SYS,
			"%s:can't find match irq\n", __func__);
		return -1;
	}

	if (swtp->eint_type[i] == IRQ_TYPE_LEVEL_LOW) {
		irq_set_irq_type(swtp->irq[i], IRQ_TYPE_LEVEL_HIGH);
		swtp->eint_type[i] = IRQ_TYPE_LEVEL_HIGH;
	} else {
		irq_set_irq_type(swtp->irq[i], IRQ_TYPE_LEVEL_LOW);
		swtp->eint_type[i] = IRQ_TYPE_LEVEL_LOW;
	}

	if (swtp->gpio_state[i] == SWTP_EINT_PIN_PLUG_IN)
		swtp->gpio_state[i] = SWTP_EINT_PIN_PLUG_OUT;
	else
		swtp->gpio_state[i] = SWTP_EINT_PIN_PLUG_IN;

#if defined(CONFIG_MOTO_SWTP_CUST)
	swtp->tx_power_mode = SWTP_DO_TX_POWER; // default as radiate mode, DO power
#else
	swtp->tx_power_mode = SWTP_NO_TX_POWER;
#endif

#if defined(CONFIG_MOTO_SWTP_CUST)
	for (i = 0; i < MAX_PIN_NUM; i++) {
            CCCI_LEGACY_ERR_LOG(-1, SYS,
			"%s: swtp->gpio_state[%d] is %d\n", __func__ , i, swtp->gpio_state[i]);
        }
#endif

	for (i = 0; i < MAX_PIN_NUM; i++) {
		if (swtp->gpio_state[i] == SWTP_EINT_PIN_PLUG_IN) {
#if defined(CONFIG_MOTO_SWTP_CUST)
			swtp->tx_power_mode = SWTP_NO_TX_POWER;
#else
			swtp->tx_power_mode = SWTP_DO_TX_POWER;
#endif
			break;
		}
	}

#if defined(CONFIG_MOTO_SWTP_CUST)
	CCCI_LEGACY_ERR_LOG(-1, SYS,
			"%s: bypass_rfcable_detect: %d\n", __func__ , bypass_rfcable_detect);
	if (!bypass_rfcable_detect) {
		inject_pin_status_event(swtp->tx_power_mode, rf_name);
		CCCI_LEGACY_ERR_LOG(-1, SYS,
				"%s: notify swtp tx power mode:%d (0 -- do swtp tx; 1 -- no swtp tx)\n", __func__ ,swtp->tx_power_mode);

	}
#else
	inject_pin_status_event(swtp->curr_mode, rf_name);
#endif
#if defined(CONFIG_MOTO_SWTP_CUST)
    swtp_tx_power_mode = swtp->tx_power_mode;
    rfcable_state_update();
#endif

	spin_unlock_irqrestore(&swtp->spinlock, flags);

	return swtp->tx_power_mode;
}

static void swtp_send_tx_power_state(struct swtp_t *swtp)
{
	int ret = 0;

	if (!swtp) {
		CCCI_LEGACY_ERR_LOG(-1, SYS,
			"%s:swtp is null\n", __func__);
		return;
	}

	ret = swtp_send_tx_power(swtp);
	if (ret < 0) {
		CCCI_LEGACY_ERR_LOG(0, SYS,
			"%s send tx power failed, ret=%d, schedule delayed work\n",
			__func__, ret);
		schedule_delayed_work(&swtp->delayed_work, 5 * HZ);
	}
}

static irqreturn_t swtp_irq_handler(int irq, void *data)
{
	struct swtp_t *swtp = (struct swtp_t *)data;
	int ret = 0;

	ret = swtp_switch_state(irq, swtp);
	if (ret < 0) {
		CCCI_LEGACY_ERR_LOG(0, SYS,
			"%s swtp_switch_state failed in irq, ret=%d\n",
			__func__, ret);
	} else
		swtp_send_tx_power_state(swtp);

	return IRQ_HANDLED;
}

static void swtp_tx_delayed_work(struct work_struct *work)
{
	struct swtp_t *swtp = container_of(to_delayed_work(work),
		struct swtp_t, delayed_work);
	int ret, retry_cnt = 0;

	while (retry_cnt < MAX_RETRY_CNT) {
		ret = swtp_send_tx_power(swtp);
		if (ret != 0) {
			msleep(2000);
			retry_cnt++;
		} else
			break;
	}
}

int swtp_md_tx_power_req_hdlr(int data)
{
	struct swtp_t *swtp = NULL;

	swtp = &swtp_data;
	swtp_send_tx_power_state(swtp);

	return 0;
}

static void swtp_init_delayed_work(struct work_struct *work)
{
	int i, ret = 0;
#ifdef CONFIG_MTK_EIC
	u32 ints[2] = { 0, 0 };
#else
	u32 ints[1] = { 0 };
#endif
	u32 ints1[2] = { 0, 0 };
	struct device_node *node = NULL;

	CCCI_NORMAL_LOG(-1, SYS, "%s at the begin...\n", __func__);
	CCCI_BOOTUP_LOG(-1, SYS, "%s at the begin...\n", __func__);
#if defined(CONFIG_MOTO_SWTP_CUST)
	ret = class_register(&swtp_class);

	ret = class_create_file(&swtp_class, &class_attr_swtp_gpio_state);
#endif

	if (ARRAY_SIZE(swtp_of_match) != ARRAY_SIZE(irq_name) ||
		ARRAY_SIZE(swtp_of_match) > MAX_PIN_NUM + 1 ||
		ARRAY_SIZE(irq_name) > MAX_PIN_NUM + 1) {
		ret = -3;
		CCCI_LEGACY_ERR_LOG(-1, SYS,
			"%s: invalid array count = %lu(of_match), %lu(irq_name)\n",
			__func__, ARRAY_SIZE(swtp_of_match),
			ARRAY_SIZE(irq_name));
		goto SWTP_INIT_END;
	}

	for (i = 0; i < MAX_PIN_NUM; i++)
		swtp_data.gpio_state[i] = SWTP_EINT_PIN_PLUG_OUT;

	for (i = 0; i < MAX_PIN_NUM; i++) {
		node = of_find_matching_node(NULL, &swtp_of_match[i]);
		if (node) {
			ret = of_property_read_u32_array(node, "debounce",
				ints, ARRAY_SIZE(ints));
			if (ret) {
				CCCI_LEGACY_ERR_LOG(0, SYS,
					"%s:swtp%d get debounce fail\n",
					__func__, i);
				break;
			}

			ret = of_property_read_u32_array(node, "interrupts",
				ints1, ARRAY_SIZE(ints1));
			if (ret) {
				CCCI_LEGACY_ERR_LOG(0, SYS,
					"%s:swtp%d get interrupts fail\n",
					__func__, i);
				break;
			}
#ifdef CONFIG_MTK_EIC /* for chips before mt6739 */
			swtp_data.gpiopin[i] = ints[0];
			swtp_data.setdebounce[i] = ints[1];
#else /* for mt6739,and chips after mt6739 */
			swtp_data.setdebounce[i] = ints[0];
			swtp_data.gpiopin[i] =
				of_get_named_gpio(node, "deb-gpios", 0);
#endif
			gpio_set_debounce(swtp_data.gpiopin[i],
				swtp_data.setdebounce[i]);
			swtp_data.eint_type[i] = ints1[1];
			swtp_data.irq[i] = irq_of_parse_and_map(node, 0);

			ret = request_irq(swtp_data.irq[i],
				swtp_irq_handler, IRQF_TRIGGER_NONE,
				irq_name[i], &swtp_data);
			if (ret) {
				CCCI_LEGACY_ERR_LOG(0, SYS,
					"swtp%d-eint IRQ LINE NOT AVAILABLE\n",
					i);
				break;
			}
		} else {
			CCCI_LEGACY_ERR_LOG(0, SYS,
				"%s:can't find swtp%d compatible node\n",
				__func__, i);
			ret = -4;
		}
	}
	register_ccci_sys_call_back(MD_SW_MD1_TX_POWER_REQ,
		swtp_md_tx_power_req_hdlr);

SWTP_INIT_END:
	CCCI_BOOTUP_LOG(0, SYS, "%s end: ret = %d\n", __func__, ret);
	CCCI_NORMAL_LOG(0, SYS, "%s end: ret = %d\n", __func__, ret);

}

int swtp_init(void)
{
#if defined(CONFIG_MOTO_SWTP_CUST)
	char bypass_rfcable_detect_str[16] = "false";
		/* rfcable state work setting */
	INIT_DELAYED_WORK(&rfcable_state.rfcable_delayed_work,
		reboot_md_delayed_work);
#endif
	/* init woke setting */
	INIT_DELAYED_WORK(&swtp_data.init_delayed_work,
		swtp_init_delayed_work);
	/* tx work setting */
	INIT_DELAYED_WORK(&swtp_data.delayed_work,
		swtp_tx_delayed_work);
#ifdef CONFIG_MOTO_DISABLE_SWTP_FACTORY
	swtp_data.tx_power_mode = SWTP_NO_TX_POWER;
#else
	swtp_data.tx_power_mode = SWTP_DO_TX_POWER;
#endif

	#if defined(CONFIG_MOTO_SWTP_CUST)
	swtp_tx_power_mode = swtp_data.tx_power_mode;
	rfcable_state.is_needed_to_reset_md = false;
	rfcable_state.last_swtp_tx_power_mode = -1;
	#endif

	spin_lock_init(&swtp_data.spinlock);

	/* schedule init work */
	schedule_delayed_work(&swtp_data.init_delayed_work, HZ);

#if defined(CONFIG_MOTO_SWTP_CUST)
	mmi_get_bootarg("androidboot.bypass_rfcable_detect=", bypass_rfcable_detect_str, sizeof(bypass_rfcable_detect_str));
	CCCI_LEGACY_ERR_LOG(-1, SYS,
			"%s: bypass_rfcable_detect_str: %s\n", __func__ , bypass_rfcable_detect_str);
	if (strncmp(bypass_rfcable_detect_str,"true",4) == 0) {
		bypass_rfcable_detect = true;
	}
#endif

	CCCI_BOOTUP_LOG(0, SYS, "%s end, init_delayed_work scheduled\n",
		__func__);
	return 0;
}
