# iio-sensor-proxy

Fedora's iio-sensor-proxy, rebuilt by `scripts/packages/build-iio-sensor-proxy.sh`
from the SRPM pinned in `config/desktop/inputs.env` (desktop-components group
`iio-sensor-proxy`, both architectures).

- `0001-poll-light-no-faster-than-default.patch`: the polled ambient light
  driver used the sensor's integration time as its polling interval. On a
  Fairphone 6 (STK36C61, 5.92 ms) that is 200 reads a second, each 0.69 ms of
  CPU on its I2C bus: 13-17% of a CPU whenever light is claimed. Integration
  times go down to 185 us, which polls in a busy loop. The patch keeps reading
  the integration time but never polls faster than `DEFAULT_POLL_TIME`
  (0.8 s). Luma-specific for now; drop it if upstream adopts a floor.
- `0000-luma-fedora-spec.patch`: adds the patch, the `luma.N` release and a
  `%check` running the upstream integration tests (`-Dtests=true`).
