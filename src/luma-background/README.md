<!-- SPDX-License-Identifier: CC-BY-SA-4.0 -->
# luma-background

The session service lets apps continue work after their windows close using a
small background agent that it registers, allows or refuses, starts, wakes,
limits and reports. The [declaration parser](luma_background/declaration.py),
[lifecycle manager](luma_background/manager.py) and
[D-Bus service](luma_background/service.py) define the source-owned contract.

| Module | Owns |
| --- | --- |
| `ids.py` | Application, agent and schedule identifiers, and every unit name derived from them |
| `declaration.py`, `desktop.py` | Reading installed `[background]` declarations and desktop entries |
| `registry.py` | Which apps have agents: declarations, portal autostart entries, portal decisions; which autostart entries an agent replaces |
| `policy.py` | The person's decisions over `defaults.toml` |
| `units.py`, `systemd.py` | Generated units and timers, and the user manager's D-Bus API |
| `manager.py` | Wakes, schedules, Power Saver, crashes, prompts, reporting (no D-Bus, testable) |
| `wakes.py` | NetworkManager, logind and power-profiles-daemon |
| `portal.py` | The Background portal backend and the portal permission table |
| `login.py` | Open at Login: whether an app starts at login, from every autostart source, and the standard writes that change it |
| `identity.py` | Who is calling, from D-Bus credentials |
| `prompt.py` | The one-time question, as a notification |
| `service.py`, `cli.py` | `org.projectluma.Background1`, and `luma-background` |
| `wake.py` | `org.projectluma.BackgroundWake1`, the polkit-guarded system helper that wakes a suspended computer for alarms |

Tests: `tests/luma-background` (unit), `tests/integration/luma-background`
(a real systemd user session in a disposable Fedora 44 container; never on a
machine someone uses). `system_checks.py` there exercises systemd-oomd and the
wake helper against the real system manager and polkit, in such a container
only.

Licensed MPL-2.0 (`LICENSE`). Documentation CC-BY-SA-4.0.
