# Mobile capability integration

Stevia recognizes Luma, Luma-dark and Luma-light while preserving HighContrast
and the upstream fallback. The complete dark stylesheet is included in the
existing ARM keyboard builder.

Phosh patch 0046 binds radio visibility to hardware presence and initializes
mobile-data sensitivity. It remains unwired until the owning shell baseline
and native plugin package are qualified.

Tester patch 0001 removes synthetic portrait millimetre scaling for virtual
touch keyboards, preserves unrelated flags and restores the original flag
when leaving the profile. It remains an integration delta against the newer
Tester source; it does not change physical display or keyboard policy.

Focused checks cover theme selection, GTK stylesheet parsing, radio visibility
and virtual-profile restoration. Full package and physical hotplug checks are
separate integration requirements.
