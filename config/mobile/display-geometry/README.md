# Display exclusions

A device profile can install its JSON as `/usr/share/luma/display-geometry.json`.
An owner override at `/etc/luma/display-geometry.json` takes precedence. Displays
without metadata keep their existing insets.

Schema 1 specifies the panel connector, native pixel dimensions and rectangular
camera/corner exclusions. The shared Shell projects them through Mutter's native
monitor transform. Corner exclusions move only affected status content; centered
exclusions do not add padding to the left clock. Full-screen surfaces fill the
panel while placing their header content clear of the exclusion.

The Titan profile uses a conservative 132 × 132 native-pixel top-left exclusion,
calibrated visually on the owner's device. It is not an exact hardware measurement.
Refine that profile independently of shared layout code.
