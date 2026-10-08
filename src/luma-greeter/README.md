# Luma Presence greeter

`luma-greeter` is Luma's native handheld presentation for greetd. It runs as
the unprivileged `greetd` account inside a dedicated Cage compositor, reads
identity from the host account database and AccountsService, and submits the
entered credential only through greetd's PAM-backed IPC socket.

The greeter has no credential database, user home dependency, web runtime, or
Android dependency. Its fixed four-byte credential buffer is wiped on every
state transition and immediately after it is copied into the bounded
authentication worker. greetd remains responsible for account policy,
rate-limiting, authentication, and starting the authenticated Luma session.

The quiet clock and passcode compositions are one responsive native component.
The identity zone is a vertically expanding box between the date and keypad;
GTK centers the avatar/name/dots group inside that live region rather than at a
hard-coded screen coordinate.

On the FP6, the source-owned pre-session launcher applies the panel's supported
3x wlroots output scale before GTK starts, then replaces itself with the
greeter. This keeps sizing at the compositor ownership boundary without a user
setting, login script, or persistent helper. The passcode view also owns a
left-edge back drag, which securely clears the partial PIN and returns to the
quiet locked view.

The Emergency call control opens a second, private-data-free native keypad; it
never places a call from the first tap. The greeter can call only the three
restricted `net.catcrafts.IMS1` methods for dialing an emergency number,
querying that same emergency call, and ending it. `imsd` validates both the
number and opaque call identity server-side. Until that native service owns its
system-bus name, the entry pill is insensitive rather than simulated or inert.
