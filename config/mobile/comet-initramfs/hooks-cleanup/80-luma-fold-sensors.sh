#!/bin/sh
# Fold: tell iio-sensor-proxy which AoC sensors to use. aoc-usf-iio labels each
# IIO device with its USF name; poll the accelerometer (it has no trigger, so
# the buffer driver cannot open it), use IMU 0 for orientation and mark IMU 1
# (the other half) as the base, and take light from the AoC's virtual ALS,
# which already follows the screen in use, rather than the two panel ALS.
# Proximity: aoc-usf-iio reports it as 0 (far) or 100 (near), so near is
# anything above 50.
d=/sysroot/etc/udev/rules.d
[ -d /sysroot/etc ] || exit 0
mkdir -p $d
cat > $d/81-luma-fold-sensors.rules <<"RULES"
SUBSYSTEM=="iio", ATTR{name}=="usf_accel", ENV{IIO_SENSOR_PROXY_TYPE}="iio-poll-accel"
SUBSYSTEM=="iio", ATTR{name}=="usf_accel", ATTR{label}=="*Accelerometer 0", ENV{ACCEL_LOCATION}="display"
SUBSYSTEM=="iio", ATTR{name}=="usf_accel", ATTR{label}=="*Accelerometer 1", ENV{ACCEL_LOCATION}="base"
SUBSYSTEM=="iio", ATTR{name}=="usf_als", ATTR{label}!="Virtual Ambient Light", ENV{IIO_SENSOR_PROXY_TYPE}=""
SUBSYSTEM=="iio", ATTR{name}=="usf_prox", ENV{PROXIMITY_NEAR_LEVEL}="50"
RULES
exit 0
