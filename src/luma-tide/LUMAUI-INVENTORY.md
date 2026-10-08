# Tide LumaUI feature inventory

This is an implementation inventory, not a conform PASS claim. The complete
scenario has thirty states in each of dark, light, dark phone and light phone.
Shared visual differences remain tracked in Tide's numbered kit requests.

The mockup supplies no reachable action to open expanded Now Playing. Nick
authorized the recommended attached-deck song-title entry, with mouse and
native Enter/Space activation and no new visual chrome. The route remains a
Nick-to-confirm taste note in MORNING-DECISIONS. Actual pointer/key checks
exercise entry, desktop tabs, close and Escape without directly calling
open_now_playing(); check0904 completed rc0 on desktop and phone,
preserves playback, and confirms all fixture windows close. Later1008
identity/actions passed; its overall package result1 was solely missing AppStream.
Daytime isolated staged-package check1520 completes rc0, including AppStream,
all retained native scripts, 196 units and identity/title-entry actions.

| Surface or behavior | Implementation | Scenario coverage | Remaining shared work |
|---|---|---|---|
| Window, identity, island and closed-by-default details | AppWindow, Island, DetailsPane, LayerHost | All states | Frame-attached details variant (07) |
| Albums, Artists and Songs modes | CornerPill and labels-only ModeSwitch | albums, artists, songs | Sources pressed/status treatment (05) |
| Inline library navigation and counts | NavigationTrailBar and shared trail | albums, artists, songs, search results | Exact shared trail height (07) |
| Search, results and no matches | Search input, song filtering and sortable results | search, search-results, search-empty | CornerPill search slot and close button (05) |
| Album hero, artwork and metadata | CoverArt, ContentLitHeader and semantic type roles | album, album-generated, album-empty | Effective album title sizes and cover variants (02) |
| Album play/pause, shuffle, love and share | Shared action controls with real player callbacks; clipboard share | album-paused, love-album, paused | Album control tier and love appearance (07) |
| Song play/pause, hover play, current indicator and love | Tide SongRow, shared glyphs and Selection | song-paused, next-song, songs | Shared current-song treatment and column weights (03) |
| Large song and album track lists | SongList viewport, reusable rows and full-height spacers | Small fixture plus separate 20,000-row widget checks | No change to row bounds or recycling |
| Album gallery and related albums | Tide responsive composition of CoverArt tiles | albums, album-generated, album-empty; final-tile mouse/scroll PASS1520 on desktop/phone/desktop, old-package negative control PASS1510; full four-variant110316 captures reviewed | Generated-cover type (02); four full reports remain FAIL |
| Artist gallery and album navigation | PersonAvatar fills its responsive square; public body/600 labels; Tide grid | artists; desktop/phone/desktop square allocation and effective13/600 names PASS in daytime artist-candidate; old staged package negative rejects150 in168 slot | Artist gradient and initial role (02) |
| Attached desktop and compact phone deck | Tide frame-connected shoulders, shared MediaTransport and credits | All playing states | Cover, hue and compact semantic variants (01/02) |
| Transport, position and volume | MediaTransport callbacks to the single playback controller | paused, repeat, shuffle, next-song | Shared control appearance (01/07); range inventory fixed (06) |
| Artist and album credit links | Shared type roles in Tide link buttons | All playing states | Exact remaining type metrics (02) |
| Queue, playing item, following items and Clear | DetailsPane with DetailsItem and CoverArt | queue | Plain 38 px covers and media selection (02/03) |
| Output choice and selected output | Shared menu with actual available outputs | outputs | Media output menu variant (08) |
| Expanded Now Playing, Up next and About | Deck song-title button (mouse/Enter/Space), Tide artwork backdrop and layout, shared tabs, queue rows and labels | now-next, now-about; private-display mouse/key action check0904 rc0,1008 identity/actions PASS | Backdrop, white selection and title/credit roles (02) |
| Close Now Playing and Escape | Shared close action and Tide key handling | now-next, now-about | Full gate verification pending |
| Source list, status marks and Add source | DetailsRow, AddRow and Tide token-based source mark | sources | Shared row treatment (07) |
| Remote source details and real status facts | Shared hero, facts and stacked actions | source | Shared spacing and pane treatment (07) |
| Local source and downloads details | Real source adapters, facts, Sync and Show in Filer | source-local, source-downloads | Shared spacing (07) |
| Source menus via More, right-click, touch hold and keyboard | FloatingMenu with RichMenuItem; same callbacks as details | source-menu, source-local-menu | Phone menu/pane capture and shared variants (06/07) |
| Add/edit server, validation, password and keyring hint | TextField and shared primary action; real remote adapter | add-source, edit-source | Field labels, spacing and Connect tier (07) |
| Sign in after sign-out | Edit source form and existing account verification | edit-source | Shared controls (07) |
| Sign-out confirmation, retained downloads and Undo | DestructiveDialog, one-time backup and in-memory credential Undo; ToastHost tracks the attached deck | sign-out-confirm; data tests; actual mouse Cancel/confirm/Undo PASS at 1180 and 390 in daytime sign-out-candidate | Shared confirmation treatment (07) |
| Remove confirmation and Undo | DestructiveDialog and independent DeferredRemovals timers; Cancel restores the selected source | remove-confirm; data/timer tests; actual mouse Remove/Cancel PASS at 1180 and 390 in daytime sign-out-candidate | Shared confirmation treatment (07) |
| Recently played album ordering | Documented additive AlbumRecency store; locked RMW, backup and atomic replacement | Pure/live tests; fixture ordering stays deterministic | None in the data adapter |

Fixture execution isolates config, data, cache and state. It never starts the
real database, keyring, audio, remote sync or local history writes. Real artwork
comes from source artwork paths and cached textures; reference art is not a
repository asset. Data safety and large-library tests are independent of the
conform screenshot fixture.

Completion still requires four full PASS reports, no missing feature inventory,
green app/kit tests with evidenced baseline exceptions, a clean pushed branch,
and a safely installed, closed preview. No deviation or threshold change is
introduced by this inventory.
