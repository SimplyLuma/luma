# Browser application callbacks

The patch applies to LineageOS Jelly `e9990a1a25ce305c1ac24a46c81b39231ed23381`.
It preserves upstream notices and licensing. A successful external activity
launch must consume the navigation even when it originated in a redirect.
Otherwise WebView attempts the same custom-scheme callback and displays an
unavailable-page error after the app has already received it.

Main-frame, incognito, repeated-navigation and handler checks remain intact.
No authorization URLs or credentials belong in logs, tests or task evidence;
qualification uses synthetic callback values. This source change alone does not
qualify a real account login or host-browser callback routing.
