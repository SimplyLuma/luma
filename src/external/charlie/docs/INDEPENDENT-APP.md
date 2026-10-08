# Independent Charlie interface and native mail owner

The signed Flatpak interface reads and edits exactly the existing `xdg-data/charlie` mailbox directory. This is an explicit mailbox-data grant, including its SQLite WAL; it is not a home-directory or credential grant. Native background mail and local search retain the same database and schema. Account passwords and OAuth refresh/access tokens remain in the native Secret Service owner and are never returned to the interface.

Native Charlie release 5 exports `org.projectluma.MailHost1`, separate from the interface's implicit `org.projectluma.Charlie.*` namespace. The fixed Start/Poll/Cancel protocol admits each actual signed Charlie connection through the maintained host deployment/lifetime checks. Opaque handles belong to one bus connection, terminal results are consumed, pending operations are bounded, caller departure cancels pending work, and idle service instances exit. Cancellation cannot undo a message already accepted by a remote server; an unknown outcome must not be automatically retried.

Only fixed account, sync, read-state, sending and sign-in operations are allowed. Server configuration is typed. Sending accepts bounded attachment bytes and safe basenames, never a host file path. Temporary send files are private and removed on completion. Password input is handed to native credential storage; refusal must not silently create an uncredentialed account. OAuth opens the provider's existing PKCE browser flow in the native owner and returns only account display metadata.

The native launcher resolves the signed independent app before opening a UI. The agent continues to use the native path. Removing the app does not reveal an old native interface or reset the mailbox. The application declares host Installer 59 and native Charlie 5 compatibility; older hosts must retain their working version until the service owner is available.

The canonical native producer retains the actual HTML reader, responsive GTK, mailbox/IMAP and background checks, alongside the new wire/owner/interface regression tests. Actual signed default launch, shared SQLite inode and WAL ownership, real broker positive/negative calls, account enrollment, reply/attachments, background notification activation and independent update/rollback remain required installed gates. Source tests alone do not close them. Distribution on another Linux OS requires the supported native service companion; standalone and other-platform delivery have not been qualified by this change.

Account changes use bounded UI workers and GLib completions, so a blocked
credential prompt leaves the GTK main loop available. Closing the account
surface suppresses its late callback. The native owner serializes each
account's refresh, avatar update and removal with a bounded set of lock stripes;
a retained account object cannot refresh after deletion. Cancellation before
credential commit changes nothing. Once that commit starts, its matching mailbox
metadata finishes under the same lock, even if the requesting surface closes.
OAuth's real loopback wait polls cancellation every 200 ms. An already active
network request keeps its existing finite transport timeout and is not retried
because delivery may already have occurred.

Native account guards also coordinate the background mail agent across processes
using stable, bounded advisory lock files. The guard validates owned private
files, resets inherited descriptors and reentrancy after fork, and bounds lock
waiting to 60 seconds. A canceled host request leaves that wait promptly.
The actual separate-process refresh/removal and fork-inheritance regressions
are part of normal package checks; process-local locks alone do not qualify it.
