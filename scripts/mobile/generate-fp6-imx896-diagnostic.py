#!/usr/bin/env python3
"""Generate a temporary IMX896 V4L2 driver from the FP6 stock profile.

The generated file is a bring-up artifact, not a redistributable profile dump.
It reuses the already-reviewed S5KKD1SP V4L2 plumbing and replaces only the
sensor-specific tables, geometry, identity, and power sequence.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from pathlib import Path


def load_analyzer(path: Path):
    spec = importlib.util.spec_from_file_location("luma_qti_profile", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load analyzer: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def c_array(name: str, registers: list[dict[str, int]]) -> str:
    rows = "\n".join(
        f"\t{{ CCI_REG8(0x{item['address']:04x}), 0x{item['value']:02x} }},"
        for item in registers
    )
    return f"static const struct cci_reg_sequence {name}[] = {{\n{rows}\n}};\n"


def replace_function(source: str, name: str, replacement: str, next_name: str) -> str:
    pattern = (
        rf"^static [^\n]*\b{name}\(.*?"
        rf"(?=^static [^\n]*\b{next_name}\()"
    )
    result, count = re.subn(
        pattern,
        lambda _match: replacement.rstrip() + "\n\n",
        source,
        flags=re.S | re.M,
    )
    if count != 1:
        raise RuntimeError(f"could not replace {name}: matched {count} blocks")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("profile", type=Path)
    parser.add_argument("base_driver", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    analyzer = load_analyzer(Path(__file__).with_name("analyze-qti-camera-profile.py"))
    data = args.profile.read_bytes()
    sections = analyzer.parse_sections(data)
    data_section = max((item for item in sections if item.kind == 1), key=lambda item: item.size)
    symbols = analyzer.parse_symbols(data, next(item for item in sections if item.kind == 2))
    arrays = {item["symbol_id"]: item["registers"] for item in analyzer.register_arrays(data, data_section, symbols)}
    init = arrays[8845]
    mode = arrays[887]
    if len(init) != 493 or len(mode) != 134:
        raise RuntimeError("the pinned FP6 IMX896 profile layout has changed")

    tail = args.base_driver.read_text().split("struct s5kkd1sp {", 1)[1]
    tail = "struct imx896 {" + tail
    tail = tail.replace("s5kkd1sp", "imx896").replace("S5KKD1SP", "IMX896")
    tail = tail.replace("MEDIA_BUS_FMT_SGRBG10_1X10", "MEDIA_BUS_FMT_SRGGB10_1X10")
    tail = tail.replace("3104", "4096").replace("2336", "3072")
    tail = tail.replace("3640 - 4096", "11904 - 4096")
    tail = tail.replace("5078 - 3072", "3790 - 3072")

    log_start = tail.index("static void imx896_log_reg")
    fill_start = tail.index("static void imx896_fill_format")
    tail = tail[:log_start] + tail[fill_start:]

    enable = r'''static int imx896_enable_streams(struct v4l2_subdev *sd,
				 struct v4l2_subdev_state *state,
				 u32 pad, u64 streams_mask)
{
	struct imx896 *sensor = to_imx896(sd);
	int ret;

	ret = pm_runtime_resume_and_get(sensor->dev);
	if (ret)
		return ret;
	ret = 0;
	cci_multi_reg_write(sensor->regmap, imx896_init_regs,
			    ARRAY_SIZE(imx896_init_regs), &ret);
	cci_multi_reg_write(sensor->regmap, imx896_4096x3072_regs,
			    ARRAY_SIZE(imx896_4096x3072_regs), &ret);
	if (!ret)
		ret = v4l2_ctrl_handler_setup(sensor->sd.ctrl_handler);
	if (!ret)
		cci_write(sensor->regmap, IMX896_REG_CTRL_MODE,
			  IMX896_MODE_STREAMING, &ret);
	if (!ret)
		return 0;

	dev_err(sensor->dev, "failed to start streaming: %d\n", ret);
	pm_runtime_put_autosuspend(sensor->dev);
	return ret;
}'''
    tail = replace_function(tail, "imx896_enable_streams", enable, "imx896_disable_streams")

    power = r'''static int imx896_enable_supply(struct imx896 *sensor, unsigned int index,
				 int min_uv, int max_uv, int load_ua)
{
	struct regulator *regulator = sensor->supplies[index].consumer;
	int ret;

	ret = regulator_set_voltage(regulator, min_uv, max_uv);
	if (ret)
		return ret;
	ret = regulator_set_load(regulator, load_ua);
	if (ret < 0)
		goto clear_voltage;
	ret = regulator_enable(regulator);
	if (!ret)
		return 0;
	regulator_set_load(regulator, 0);
clear_voltage:
	regulator_set_voltage(regulator, 0, max_uv);
	return ret;
}

static void imx896_disable_supply(struct imx896 *sensor, unsigned int index,
				   int max_uv)
{
	struct regulator *regulator = sensor->supplies[index].consumer;

	regulator_disable(regulator);
	regulator_set_load(regulator, 0);
	regulator_set_voltage(regulator, 0, max_uv);
}

static int imx896_power_on(struct device *dev)
{
	struct imx896 *sensor = to_imx896(dev_get_drvdata(dev));
	int ret;

	gpiod_set_value_cansleep(sensor->reset_gpio, 1);
	ret = imx896_enable_supply(sensor, IMX896_SUPPLY_AUX,
		IMX896_AUX_MIN_UV, IMX896_AUX_MAX_UV, IMX896_AUX_LOAD_UA);
	if (ret)
		return ret;
	ret = imx896_enable_supply(sensor, IMX896_SUPPLY_VDDA,
		IMX896_VDDA_MIN_UV, IMX896_VDDA_MAX_UV, IMX896_VDDA_LOAD_UA);
	if (ret)
		goto disable_aux;
	ret = imx896_enable_supply(sensor, IMX896_SUPPLY_VDDD,
		IMX896_VDDD_MIN_UV, IMX896_VDDD_MAX_UV, IMX896_VDDD_LOAD_UA);
	if (ret)
		goto disable_vdda;
	ret = imx896_enable_supply(sensor, IMX896_SUPPLY_VDDIO,
		IMX896_VDDIO_MIN_UV, IMX896_VDDIO_MAX_UV, IMX896_VDDIO_LOAD_UA);
	if (ret)
		goto disable_vddd;
	usleep_range(2000, 2100);
	gpiod_set_value_cansleep(sensor->reset_gpio, 0);
	usleep_range(2000, 2100);
	ret = clk_prepare_enable(sensor->mclk);
	if (ret)
		goto assert_reset;
	usleep_range(17000, 18000);
	return 0;

assert_reset:
	gpiod_set_value_cansleep(sensor->reset_gpio, 1);
	imx896_disable_supply(sensor, IMX896_SUPPLY_VDDIO, IMX896_VDDIO_MAX_UV);
disable_vddd:
	imx896_disable_supply(sensor, IMX896_SUPPLY_VDDD, IMX896_VDDD_MAX_UV);
disable_vdda:
	imx896_disable_supply(sensor, IMX896_SUPPLY_VDDA, IMX896_VDDA_MAX_UV);
disable_aux:
	imx896_disable_supply(sensor, IMX896_SUPPLY_AUX, IMX896_AUX_MAX_UV);
	return ret;
}

static int imx896_power_off(struct device *dev)
{
	struct imx896 *sensor = to_imx896(dev_get_drvdata(dev));

	clk_disable_unprepare(sensor->mclk);
	gpiod_set_value_cansleep(sensor->reset_gpio, 1);
	imx896_disable_supply(sensor, IMX896_SUPPLY_VDDIO, IMX896_VDDIO_MAX_UV);
	imx896_disable_supply(sensor, IMX896_SUPPLY_VDDD, IMX896_VDDD_MAX_UV);
	imx896_disable_supply(sensor, IMX896_SUPPLY_VDDA, IMX896_VDDA_MAX_UV);
	imx896_disable_supply(sensor, IMX896_SUPPLY_AUX, IMX896_AUX_MAX_UV);
	return 0;
}'''
    tail = replace_function(tail, "imx896_power_on", power, "imx896_init_controls")

    controls = r'''static int imx896_set_ctrl(struct v4l2_ctrl *ctrl)
{
	struct imx896 *sensor = container_of(ctrl->handler,
					     struct imx896, ctrl_handler);
	int ret;

	ret = pm_runtime_get_if_in_use(sensor->dev);
	if (ret <= 0)
		return ret;

	ret = 0;
	switch (ctrl->id) {
	case V4L2_CID_EXPOSURE:
		cci_write(sensor->regmap, CCI_REG16(0x0202), ctrl->val, &ret);
		break;
	case V4L2_CID_ANALOGUE_GAIN:
		cci_write(sensor->regmap, CCI_REG16(0x0204), ctrl->val, &ret);
		break;
	default:
		ret = -EINVAL;
		break;
	}

	pm_runtime_put(sensor->dev);
	return ret;
}

static const struct v4l2_ctrl_ops imx896_ctrl_ops = {
	.s_ctrl = imx896_set_ctrl,
};

static int imx896_init_controls(struct imx896 *sensor)
{
	struct v4l2_ctrl_handler *handler = &sensor->ctrl_handler;
	struct v4l2_fwnode_device_properties props;
	struct v4l2_ctrl *ctrl;
	s64 pixel_rate = IMX896_LINK_FREQ * 16 * IMX896_DATA_LANES / (7 * 10);
	int ret;

	v4l2_ctrl_handler_init(handler, 8);
	ctrl = v4l2_ctrl_new_int_menu(handler, NULL, V4L2_CID_LINK_FREQ,
				      0, 0, imx896_link_freq_menu);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	v4l2_ctrl_new_std(handler, NULL, V4L2_CID_PIXEL_RATE,
			  pixel_rate, pixel_rate, 1, pixel_rate);
	ctrl = v4l2_ctrl_new_std(handler, NULL, V4L2_CID_HBLANK,
				11904 - 4096, 11904 - 4096, 1, 11904 - 4096);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	ctrl = v4l2_ctrl_new_std(handler, NULL, V4L2_CID_VBLANK,
				3790 - 3072, 3790 - 3072, 1, 3790 - 3072);
	if (ctrl)
		ctrl->flags |= V4L2_CTRL_FLAG_READ_ONLY;
	v4l2_ctrl_new_std(handler, &imx896_ctrl_ops, V4L2_CID_EXPOSURE,
			  1, 3790 - 48, 1, 1000);
	v4l2_ctrl_new_std(handler, &imx896_ctrl_ops, V4L2_CID_ANALOGUE_GAIN,
			  0, 1023, 1, 0);
	if (handler->error)
		return handler->error;

	ret = v4l2_fwnode_device_parse(sensor->dev, &props);
	if (ret)
		return ret;
	ret = v4l2_ctrl_new_fwnode_properties(handler, NULL, &props);
	if (ret)
		return ret;
	sensor->sd.ctrl_handler = handler;
	return 0;
}'''
    tail = replace_function(tail, "imx896_init_controls", controls, "imx896_probe")
    tail = tail.replace("CCI_REG16(0x0000)", "CCI_REG16(0x0016)")
    tail = tail.replace(
        'dev_info(sensor->dev, "sensor identity 0x%04llx\\n", chip_id);',
        'if (chip_id != 0x0896) {\n\t\tret = dev_err_probe(sensor->dev, -ENODEV,\n\t\t\t\t    "unexpected sensor identity 0x%04llx\\n", chip_id);\n\t\tgoto power_off;\n\t}\n\tdev_info(sensor->dev, "sensor identity 0x%04llx\\n", chip_id);',
    )
    tail = tail.replace('"samsung,imx896"', '"sony,imx896"')
    tail = tail.replace("Samsung IMX896", "Sony IMX896")
    tail = tail.replace("V4L2_MBUS_CSI2_DPHY", "V4L2_MBUS_CSI2_CPHY")

    prefix = r'''// SPDX-License-Identifier: GPL-2.0
/* Sony IMX896 diagnostic driver for the Fairphone 6 rear main camera. */

#include <linux/clk.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/i2c.h>
#include <linux/module.h>
#include <linux/pm_runtime.h>
#include <linux/regulator/consumer.h>
#include <linux/units.h>
#include <media/v4l2-cci.h>
#include <media/v4l2-ctrls.h>
#include <media/v4l2-device.h>
#include <media/v4l2-fwnode.h>

#define IMX896_LINK_FREQ		1131433333ULL
#define IMX896_MCLK_FREQ		(24 * HZ_PER_MHZ)
#define IMX896_DATA_LANES		3
#define IMX896_REG_CTRL_MODE		CCI_REG8(0x0100)
#define IMX896_MODE_STREAMING		0x01
#define IMX896_MODE_STANDBY		0x00

static const s64 imx896_link_freq_menu[] = { IMX896_LINK_FREQ };
static const char * const imx896_supply_names[] = {
	"vddaux", "vdda", "vddd", "vddio",
};
#define IMX896_NUM_SUPPLIES ARRAY_SIZE(imx896_supply_names)
#define IMX896_SUPPLY_AUX 0
#define IMX896_SUPPLY_VDDA 1
#define IMX896_SUPPLY_VDDD 2
#define IMX896_SUPPLY_VDDIO 3
#define IMX896_AUX_MIN_UV 2800000
#define IMX896_AUX_MAX_UV 2800000
#define IMX896_AUX_LOAD_UA 91430
#define IMX896_VDDA_MIN_UV 1800000
#define IMX896_VDDA_MAX_UV 1800000
#define IMX896_VDDA_LOAD_UA 63100
#define IMX896_VDDD_MIN_UV 1100000
#define IMX896_VDDD_MAX_UV 1150000
#define IMX896_VDDD_LOAD_UA 913200
#define IMX896_VDDIO_MIN_UV 1800000
#define IMX896_VDDIO_MAX_UV 1800000
#define IMX896_VDDIO_LOAD_UA 3500

'''
    output = prefix + c_array("imx896_init_regs", init) + "\n" + c_array("imx896_4096x3072_regs", mode) + "\n" + tail
    args.output.write_text(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
