// SPDX-License-Identifier: GPL-2.0-only
/*
 * Bounded FP6 diagnostic for the raw return registers of QSEE APP_START.
 *
 * Qualcomm SCM remaps secure-world a0 == -1 to Linux -EIO. This temporary
 * kretprobe records the otherwise-lost raw a0..a3 values for APP_START only.
 * It does not record the image address, image contents, command buffers, or
 * biometric data and performs no secure call itself.
 */

#include <linux/arm-smccc.h>
#include <linux/atomic.h>
#include <linux/init.h>
#include <linux/kprobes.h>
#include <linux/module.h>
#include <linux/ptrace.h>
#include <linux/types.h>
#include <linux/uaccess.h>

#define LUMA_QSEE_APP_START_SMC_ID 0x72000101UL

struct luma_qsee_trace_instance {
	struct arm_smccc_res *result;
	bool app_start;
};

static atomic_t app_start_returns = ATOMIC_INIT(0);

static int luma_qsee_trace_entry(struct kretprobe_instance *instance,
				 struct pt_regs *regs)
{
	struct luma_qsee_trace_instance *data = (void *)instance->data;
	unsigned long result_address = 0;

	data->result = NULL;
	data->app_start = false;
	if (regs_get_kernel_argument(regs, 0) != LUMA_QSEE_APP_START_SMC_ID)
		return 1;

	/* __arm_smccc_smc's ninth argument is the result pointer. AArch64 passes
	 * arguments zero through seven in x0..x7 and the ninth on the caller's
	 * stack at SP. Read only that pointer, using a no-fault helper.
	 */
	if (copy_from_kernel_nofault(&result_address, (void *)regs->sp,
				      sizeof(result_address)))
		return 1;
	if (!result_address)
		return 1;

	data->result = (struct arm_smccc_res *)result_address;
	data->app_start = true;
	return 0;
}

static int luma_qsee_trace_return(struct kretprobe_instance *instance,
				  struct pt_regs *regs)
{
	struct luma_qsee_trace_instance *data = (void *)instance->data;
	struct arm_smccc_res result;

	if (!data->app_start || !data->result)
		return 0;
	if (copy_from_kernel_nofault(&result, data->result, sizeof(result))) {
		pr_err("luma_qsee_raw: app_start result_copy_failed\n");
		return 0;
	}

	atomic_inc(&app_start_returns);
	pr_info("luma_qsee_raw: app_start raw_a0=%#lx raw_a1=%#lx raw_a2=%#lx raw_a3=%#lx\n",
		result.a0, result.a1, result.a2, result.a3);
	return 0;
}

static struct kretprobe luma_qsee_trace_probe = {
	.kp.symbol_name = "__arm_smccc_smc",
	.entry_handler = luma_qsee_trace_entry,
	.handler = luma_qsee_trace_return,
	.data_size = sizeof(struct luma_qsee_trace_instance),
	.maxactive = 8,
};

static int __init luma_qsee_trace_init(void)
{
	int ret;

	ret = register_kretprobe(&luma_qsee_trace_probe);
	if (ret)
		return ret;
	pr_info("luma_qsee_raw: armed for APP_START only\n");
	return 0;
}

static void __exit luma_qsee_trace_exit(void)
{
	unregister_kretprobe(&luma_qsee_trace_probe);
	pr_info("luma_qsee_raw: disarmed returns=%d missed=%d\n",
		atomic_read(&app_start_returns), luma_qsee_trace_probe.nmissed);
}

module_init(luma_qsee_trace_init);
module_exit(luma_qsee_trace_exit);

MODULE_DESCRIPTION("Bounded FP6 QSEE APP_START raw-return diagnostic");
MODULE_AUTHOR("Project Luma");
MODULE_LICENSE("GPL");
