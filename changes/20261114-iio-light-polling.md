---
type: fix
component: Sensors
packages: [iio-sensor-proxy]
---
Summary: Automatic brightness uses less power
Details: The sensor service reads the ambient light sensor at most every 0.8 seconds instead of up to 200 times a second. On the Fairphone 6 this used about a sixth of a processor core whenever automatic brightness was on.
