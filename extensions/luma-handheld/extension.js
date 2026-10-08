// SPDX-License-Identifier: Apache-2.0

import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as IBusManager from 'resource:///org/gnome/shell/misc/ibusManager.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

const HOME_DOCK_FRACTION = 0.984;
const APP_PICKER_HOLD_MS = 420;
const BOTTOM_GESTURE_FALLBACK_PX = 104;
const BOTTOM_GESTURE_DIRECTION_PX = 18;
const EDGE_GESTURE_BEGIN_PX = 36;
const EDGE_GESTURE_DIRECTION_PX = 18;
const EDGE_GESTURE_TRAVEL_PX = 48;
const MOBILE_LAUNCHER_COLUMNS = 4;
const MOBILE_LAUNCHER_ICON_SIZE = 64;
const MOBILE_LAUNCHER_CELL_HEIGHT = 108;
const HANDHELD_DBUS_XML = `
<node>
  <interface name="org.project_luma.Handheld1">
    <method name="ShowAppSwitcher"/>
    <method name="ToggleDisplay"/>
    <method name="SleepDisplay"/>
    <method name="WakeDisplay"/>
    <method name="GetAndroidGeometry">
      <arg name="geometry" type="s" direction="out"/>
    </method>
    <method name="GetGestureDiagnostics">
      <arg name="diagnostics" type="s" direction="out"/>
    </method>
    <method name="SetExternalKeyboardVisible">
      <arg name="visible" type="b" direction="in"/>
    </method>
    <property name="Sleeping" type="b" access="read"/>
  </interface>
</node>`;

function isNormalWindow(window) {
    return window?.get_window_type() === Meta.WindowType.NORMAL &&
        !window.is_skip_taskbar();
}

function isAndroidWindow(window) {
    if (!isNormalWindow(window))
        return false;
    const app = Shell.WindowTracker.get_default().get_window_app(window);
    const appId = app?.get_id?.() ?? '';
    if (appId.startsWith('waydroid.'))
        return true;
    const windowClass = window.get_wm_class?.()?.toLowerCase?.() ?? '';
    if (windowClass.startsWith('waydroid.') ||
        windowClass === 'org.projectluma.android')
        return true;
    return window.get_title?.() === 'Luma Android';
}

function findActorByName(root, name) {
    if (!root)
        return null;
    if (root.get_name?.() === name || root.name === name)
        return root;
    for (const child of root.get_children?.() ?? []) {
        const match = findActorByName(child, name);
        if (match)
            return match;
    }
    return null;
}

function findActorByStyleClass(root, styleClass) {
    if (!root)
        return null;
    if (root.has_style_class_name?.(styleClass))
        return root;
    for (const child of root.get_children?.() ?? []) {
        const match = findActorByStyleClass(child, styleClass);
        if (match)
            return match;
    }
    return null;
}

function findActorsByStyleClass(root, styleClass, matches = []) {
    if (!root)
        return matches;
    if (root.has_style_class_name?.(styleClass) && root.mapped)
        matches.push(root);
    for (const child of root.get_children?.() ?? [])
        findActorsByStyleClass(child, styleClass, matches);
    return matches;
}

export default class LumaHandheldExtension extends Extension {
    enable() {
        this._theme = St.ThemeContext.get_for_stage(global.stage).get_theme();
        this._stylesheet = this.dir.get_child('handheld.css');
        this._theme.load_stylesheet(this._stylesheet);

        this._syncingWindows = false;
        this._trackedWindowSignals = new Map();
        this._lastAndroidGeometry = '';
        this._homeInProgress = false;
        this._oskEdgeGesture = Main.keyboard?._bottomDragGesture ?? null;
        this._oskEdgeWasEnabled = this._oskEdgeGesture?.enabled ?? false;
        this._keyboardWasVisible = Main.keyboard?.visible ?? false;
        this._externalKeyboardVisible = false;
        this._oskCollapsedAt = 0;
        this._keyboardVisibilityId = Main.keyboard?.connect(
            'visibility-changed', () => this._onKeyboardVisibilityChanged()) ?? 0;
        this._textCursorTimeoutId = 0;
        this._ibusManager = IBusManager.getIBusManager();
        this._ibusFocusInId = this._ibusManager.connect(
            'focus-in', () => this._requestKeyboardForTextFocus('ibus-focus'));
        this._capturedEventId = global.stage.connect(
            'captured-event', this._onCapturedEvent.bind(this));
        this._virtualKeyboard = global.stage.context.get_backend().get_default_seat()
            .create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);

        this._appSystem = Shell.AppSystem.get_default();
        this._mobileLaunchPendingApp = null;
        this._mobileLaunchTimeoutId = 0;
        this._appStateId = this._appSystem.connect(
            'app-state-changed', this._onAppStateChanged.bind(this));
        this._installedAppsId = this._appSystem.connect(
            'installed-changed', () => this._rebuildMobileLauncher());
        this._favoritesId = global.settings.connect(
            'changed::favorite-apps', () => this._rebuildMobileLauncher());
        this._createMobileLauncher();
        this._launchApp = null;
        this._launchStartedAt = 0;
        this._launchTimeoutId = 0;
        this._launchSurface = new St.Widget({
            name: 'lumaHandheldLaunchSurface',
            reactive: false,
            visible: false,
            layout_manager: new Clutter.BinLayout(),
        });
        this._launchSurface.add_style_class_name('luma-handheld-launch-surface');
        this._launchBox = new St.BoxLayout({
            style_class: 'luma-handheld-launch-box',
            orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        this._launchIcon = new St.Bin({
            style_class: 'luma-handheld-launch-icon',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._launchLabel = new St.Label({
            style_class: 'luma-handheld-launch-label',
            text: 'Opening…',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._launchDetail = new St.Label({
            style_class: 'luma-handheld-launch-detail',
            text: '',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._launchBox.add_child(this._launchIcon);
        this._launchBox.add_child(this._launchLabel);
        this._launchBox.add_child(this._launchDetail);
        this._launchSurface.add_child(this._launchBox);
        Main.uiGroup.add_child(this._launchSurface);

        this._keyboardSettingsButton = new St.Button({
            style_class: 'luma-handheld-keyboard-settings',
            child: new St.Icon({icon_name: 'emblem-system-symbolic'}),
            reactive: true,
            can_focus: true,
            visible: false,
            accessible_name: 'Mobile keyboard settings',
        });
        this._keyboardSettingsButton.connect('clicked', () => {
            const app = Shell.AppSystem.get_default().lookup_app(
                'org.project_luma.MobileInputSettings.desktop');
            app?.activate();
            this._closeKeyboardImmediate();
        });
        Main.uiGroup.add_child(this._keyboardSettingsButton);

        this._keyPreview = new St.Label({
            style_class: 'luma-handheld-key-preview',
            reactive: false,
            visible: false,
        });
        Main.uiGroup.add_child(this._keyPreview);

        this._lumaShifted = false;
        this._lumaKeyboardLayout = 'letters';
        this._lumaKeyboardRows = {letters: [], numbers: []};
        this._lumaLetterButtons = [];
        this._lumaCandidateButtons = [];
        this._nativeKeyboardActor = null;
        this._nativeSuggestions = null;
        this._lumaCommitQueue = Promise.resolve();
        this._createLumaKeyboardSurface();
        this._keyboardCandidateSubscriptionId = Gio.DBus.session.signal_subscribe(
            null,
            'org.project_luma.Keyboard1',
            'CandidatesChanged',
            '/org/project_luma/Keyboard',
            null,
            Gio.DBusSignalFlags.NONE,
            (_connection, _sender, _path, _interface, _signal, parameters) => {
                const [candidates] = parameters.deepUnpack();
                this._setLumaCandidates(candidates);
            });

        this._idleSettings = new Gio.Settings({
            schema_id: 'org.gnome.desktop.session',
        });
        this._idleSettingsId = this._idleSettings.connect(
            'changed::idle-delay', () => {
                if (this._idleRearmTimeoutId)
                    this._armIdleAfterWake();
                else
                    this._syncIdleWatch();
            });
        this._idleMonitor = global.backend.get_core_idle_monitor();
        this._idleWatchId = 0;
        this._idleRearmTimeoutId = 0;
        this._displaySleeping = false;
        this._displayPowerTimeoutId = 0;
        // Extension reload is an administrative/development transition, not a
        // user sleep request. Always recover a panel left off by a previous
        // instance before accepting new power-key state.
        this._setDisplayPower(0);
        this._handheldDbus = Gio.DBusExportedObject.wrapJSObject(
            HANDHELD_DBUS_XML, this);
        this._handheldDbus.export(
            Gio.DBus.session, '/org/project_luma/Handheld');
        Gio.DBus.session.call_sync(
            'org.freedesktop.DBus',
            '/org/freedesktop/DBus',
            'org.freedesktop.DBus',
            'RequestName',
            new GLib.Variant('(su)', [
                'org.project_luma.Handheld',
                0,
            ]),
            new GLib.VariantType('(u)'),
            Gio.DBusCallFlags.NONE,
            -1,
            null);
        this._oskSyncIdleId = 0;
        this._keyboardNavGesture = null;
        this._keyboardNavActor = null;
        this._bottomGestureAction = null;
        this._bottomTouchTracking = false;
        this._bottomTouchSequence = null;
        this._bottomTouchStartX = 0;
        this._bottomTouchStartY = 0;
        this._bottomTouchClaimed = false;
        this._edgeTouchTracking = false;
        this._edgeTouchSequence = null;
        this._edgeTouchSide = null;
        this._edgeTouchStartX = 0;
        this._edgeTouchStartY = 0;
        this._edgeTouchTriggered = false;
        this._bottomHoldId = 0;
        this._bottomHoldCommitted = false;
        this._bottomPreviewVisible = false;
        this._bottomPreviewStartedAt = 0;
        this._activityReconcileId = 0;
        this._drawerReturnHome = true;
        this._gestureDiagnostics = {
            bottomRecognized: 0,
            bottomCandidates: 0,
            bottomDirectionRejected: 0,
            bottomQuickHome: 0,
            bottomHoldActivity: 0,
            bottomHoldHome: 0,
            bottomCancelled: 0,
            edgeCandidates: 0,
            edgeDirectionRejected: 0,
            right: 0,
            top: 0,
            last: 'enabled',
        };

        this._homeLauncherIdleId = 0;

        this._mutterSettings = new Gio.Settings({
            schema_id: 'org.gnome.mutter',
        });
        this._wmPreferences = new Gio.Settings({
            schema_id: 'org.gnome.desktop.wm.preferences',
        });
        this._desktopDynamicWorkspaces =
            this._mutterSettings.get_boolean('dynamic-workspaces');
        this._desktopWorkspaceCount =
            this._wmPreferences.get_int('num-workspaces');
        this._normalWorkspaceSwipeTracker =
            Main.wm?._workspaceAnimation?._swipeTracker ?? null;
        this._normalWorkspaceSwipeWasEnabled =
            this._normalWorkspaceSwipeTracker?.enabled ?? true;
        this._overviewShowingId = Main.overview.connect('showing', () =>
            GLib.idle_add_once(GLib.PRIORITY_DEFAULT_IDLE, () => {
                this._syncWorkspacePolicy();
                this._reconcileEmptyActivityView();
                this._startActivityViewGuard();
            }));
        this._overviewShownId = Main.overview.connect('shown', () => {
            // The first guard scheduled from `showing` may observe `visible`
            // before GNOME commits the transition and retire itself. Arm it
            // again only after the picker is definitively shown so final-card
            // teardown is never dependent on that transition race.
            this._reconcileEmptyActivityView();
            this._startActivityViewGuard();
        });
        this._overviewHidingId = Main.overview.connect('hiding', () =>
            GLib.idle_add_once(GLib.PRIORITY_DEFAULT_IDLE,
                () => this._syncWorkspacePolicy()));
        this._overviewHiddenId = Main.overview.connect('hidden', () =>
            this._ensureHomeLauncher());

        this._dockSettings = new Gio.Settings({
            schema_id: 'org.gnome.shell.extensions.dash-to-dock',
        });
        this._dockAppOpen = false;
        this._dockFill = new St.Widget({
            name: 'lumaHandheldDockFill',
            reactive: false,
            visible: false,
        });
        this._dockFill.add_style_class_name('luma-handheld-dock-fill');
        Main.uiGroup.add_child(this._dockFill);

        this._dockPanAction = null;
        this._dockContainer = null;
        this._dockPanScrollView = null;
        this._dockContent = null;
        this._dockBackground = null;
        this._dockBackgroundAllocationId = 0;
        this._dockPanDestroyId = 0;
        this._dockPanRetryId = 0;

        // All navigation edges use the stage's captured touch stream. On the
        // FP6, gesture arbitration can discard an edge action before
        // recognition when an app, dock, or OSK owns the touched actor.
        this._bottomGesture = null;
        this._edges = [];

        this._dockPanRetryId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, 250, () => this._attachDockPan());

        this._monitorsId = Main.layoutManager.connect(
            'monitors-changed', () => this._syncPosture());
        this._focusId = global.display.connect(
            'notify::focus-window', () => this._syncFocusedWindow());
        this._workspaceId = global.workspace_manager.connect(
            'active-workspace-changed', () => this._syncFocusedWindow());
        this._windowCreatedId = global.display.connect(
            'window-created', (_display, window) => {
                this._trackWindowLifecycle(window);
                GLib.idle_add_once(GLib.PRIORITY_DEFAULT_IDLE,
                    () => this._syncFocusedWindow());
            });
        this._restackedId = global.display.connect(
            'restacked', () => this._syncHomeStacking());
        for (const window of this._allNormalWindows())
            this._trackWindowLifecycle(window);

        this._syncPosture();
        this._syncFocusedWindow();
        this._syncIdleWatch();
        this._syncLumaKeyboardSurface();
        console.log('Luma Handheld: interaction policy enabled');
    }

    _syncPosture() {
        const monitor = Main.layoutManager.primaryMonitor;
        // This extension is enabled only by the mobile capability overlay.
        // Portrait posture activates touch edges; a docked landscape primary
        // display turns them off without changing the shared Shell package.
        this._active = monitor !== null && monitor.height > monitor.width;

        if (this._active) {
            Main.uiGroup.add_style_class_name('luma-handheld');
            Main.panel.add_style_class_name('luma-handheld');
        } else {
            Main.uiGroup.remove_style_class_name('luma-handheld');
            Main.panel.remove_style_class_name('luma-handheld');
            this._hideAppDrawer(false);
            this._hideHomeSurface();
        }

        this._syncOskEdgeGesture();
        this._syncKeyboardNavGesture();
        this._syncKeyboardSettingsButton();
        this._syncLumaKeyboardSurface();
        this._syncDockKeyboardOcclusion();
        this._syncClockPresentation();
        this._syncDockContentAlignment();
        this._syncDockMode();
        this._syncWorkspacePolicy();
        this._syncMobileLauncherGeometry();
        this._syncLaunchSurfaceGeometry();
        this._syncIdleWatch();

        if (this._active) {
            this._syncFocusedWindow();
            this._ensureHomeLauncher();
        }
    }

    _createMobileLauncher() {
        this._homeSurface = new St.Widget({
            name: 'lumaHandheldHome',
            style_class: 'luma-handheld-home',
            reactive: true,
            visible: false,
            layout_manager: new Clutter.BinLayout(),
        });
        this._homeScroll = new St.ScrollView({
            style_class: 'luma-handheld-home-scroll',
            overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.NEVER,
            x_expand: true,
            y_expand: true,
        });
        this._homeGrid = new St.BoxLayout({
            style_class: 'luma-handheld-launcher-grid',
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
        });
        this._homeScroll.set_child(this._homeGrid);
        this._homeSurface.add_child(this._homeScroll);
        // Home is part of the actual desktop stack: a sibling immediately
        // above the wallpaper group and below every compositor window actor.
        // Meta.BackgroundGroup itself is reserved for background actors and
        // produced unstable painting when it owned interactive St content.
        global.window_group.add_child(this._homeSurface);
        this._syncHomeStacking();

        this._drawerSurface = new St.Widget({
            name: 'lumaHandheldAppDrawer',
            style_class: 'luma-handheld-app-drawer',
            reactive: true,
            visible: false,
            layout_manager: new Clutter.BinLayout(),
        });
        this._drawerSheet = new St.BoxLayout({
            style_class: 'luma-handheld-app-drawer-sheet',
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
            y_expand: true,
        });
        this._drawerHandle = new St.Widget({
            style_class: 'luma-handheld-app-drawer-handle',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._drawerFavorites = new St.BoxLayout({
            style_class: 'luma-handheld-app-drawer-favorites',
            x_align: Clutter.ActorAlign.CENTER,
        });
        this._drawerSearch = new St.Entry({
            style_class: 'luma-handheld-app-drawer-search',
            hint_text: 'Search applications',
            can_focus: true,
            x_expand: true,
        });
        this._drawerSearch.set_primary_icon(new St.Icon({
            icon_name: 'system-search-symbolic',
        }));
        this._drawerSearch.clutter_text.connect('text-changed', () =>
            this._rebuildDrawerApps());
        this._drawerScroll = new St.ScrollView({
            style_class: 'luma-handheld-app-drawer-scroll',
            overlay_scrollbars: true,
            hscrollbar_policy: St.PolicyType.NEVER,
            vscrollbar_policy: St.PolicyType.AUTOMATIC,
            x_expand: true,
            y_expand: true,
        });
        this._drawerGrid = new St.BoxLayout({
            style_class: 'luma-handheld-launcher-grid',
            orientation: Clutter.Orientation.VERTICAL,
            x_expand: true,
        });
        this._drawerScroll.set_child(this._drawerGrid);
        this._drawerSheet.add_child(this._drawerHandle);
        this._drawerSheet.add_child(this._drawerFavorites);
        this._drawerSheet.add_child(this._drawerSearch);
        this._drawerSheet.add_child(this._drawerScroll);
        this._drawerSurface.add_child(this._drawerSheet);
        this._drawerPanGesture = new Clutter.PanGesture({
            pan_axis: Clutter.PanAxis.Y,
            min_n_points: 1,
            max_n_points: 1,
        });
        this._drawerPanGesture.set_begin_threshold(8);
        this._drawerPanGesture.connect('may-recognize', () =>
            this._active && Boolean(this._drawerSurface?.visible));
        this._drawerPanGesture.connect('recognize', () => {
            this._drawerSurface.remove_all_transitions();
            this._drawerDragDistance = 0;
        });
        this._drawerPanGesture.connect(
            'pan-update', action => this._updateDrawerPan(action));
        this._drawerPanGesture.connect(
            'end', action => this._endDrawerPan(action));
        this._drawerPanGesture.connect('cancel', () => this._resetDrawerPan());
        this._drawerSurface.add_action(this._drawerPanGesture);
        Main.uiGroup.add_child(this._drawerSurface);
        this._rebuildMobileLauncher();
    }

    _mobileApps() {
        const seen = new Set();
        return this._appSystem.get_installed()
            .filter(info => {
                try {
                    return info.should_show?.() !== false;
                } catch (_error) {
                    return false;
                }
            })
            .map(info => this._appSystem.lookup_app(info.get_id()))
            .filter(app => {
                if (!app || seen.has(app.get_id()))
                    return false;
                seen.add(app.get_id());
                return true;
            })
            .sort((a, b) => a.get_name().localeCompare(b.get_name()));
    }

    _favoriteApps() {
        return global.settings.get_strv('favorite-apps')
            .map(id => this._appSystem.lookup_app(id))
            .filter(Boolean);
    }

    _createLauncherButton(app, compact = false) {
        const box = new St.BoxLayout({
            style_class: compact
                ? 'luma-handheld-launcher-icon-box compact'
                : 'luma-handheld-launcher-icon-box',
            orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER,
            y_align: Clutter.ActorAlign.CENTER,
        });
        const icon = app.create_icon_texture(
            compact ? 50 : MOBILE_LAUNCHER_ICON_SIZE);
        icon.x_align = Clutter.ActorAlign.CENTER;
        box.add_child(icon);
        if (!compact) {
            const label = new St.Label({
                style_class: 'luma-handheld-launcher-label',
                text: app.get_name(),
                x_align: Clutter.ActorAlign.CENTER,
            });
            label.clutter_text.set({
                ellipsize: Pango.EllipsizeMode.END,
                single_line_mode: true,
                x_align: Clutter.ActorAlign.CENTER,
            });
            box.add_child(label);
        }
        const button = new St.Button({
            style_class: compact
                ? 'luma-handheld-launcher-button compact'
                : 'luma-handheld-launcher-button',
            child: box,
            reactive: true,
            can_focus: true,
            accessible_name: app.get_name(),
        });
        button.connect('clicked', () => this._launchMobileApp(app));
        return button;
    }

    _populateLauncherGrid(container, apps) {
        container.destroy_all_children();
        const monitor = Main.layoutManager.primaryMonitor;
        const width = monitor?.width ?? 496;
        const horizontalPadding = 14;
        const gap = 6;
        const cellWidth = Math.floor((width - horizontalPadding * 2 -
            gap * (MOBILE_LAUNCHER_COLUMNS - 1)) /
            MOBILE_LAUNCHER_COLUMNS);
        for (let index = 0; index < apps.length;
            index += MOBILE_LAUNCHER_COLUMNS) {
            const row = new St.BoxLayout({
                style_class: 'luma-handheld-launcher-row',
                x_align: Clutter.ActorAlign.CENTER,
            });
            for (let column = 0; column < MOBILE_LAUNCHER_COLUMNS; column++) {
                const app = apps[index + column];
                const cell = app
                    ? this._createLauncherButton(app)
                    : new St.Widget();
                cell.set_size(cellWidth, MOBILE_LAUNCHER_CELL_HEIGHT);
                row.add_child(cell);
            }
            container.add_child(row);
        }
    }

    _rebuildMobileLauncher() {
        if (!this._homeGrid || !this._drawerGrid)
            return;
        this._launcherApps = this._mobileApps();
        this._populateLauncherGrid(this._homeGrid, this._launcherApps);
        this._rebuildDrawerApps();
        this._drawerFavorites.destroy_all_children();
        for (const app of this._favoriteApps())
            this._drawerFavorites.add_child(
                this._createLauncherButton(app, true));
        this._syncMobileLauncherGeometry();
    }

    _rebuildDrawerApps() {
        if (!this._drawerGrid)
            return;
        const query = this._drawerSearch?.get_text().trim().toLocaleLowerCase() ?? '';
        const apps = (this._launcherApps ?? []).filter(app =>
            !query || app.get_name().toLocaleLowerCase().includes(query));
        this._populateLauncherGrid(this._drawerGrid, apps);
    }

    _syncMobileLauncherGeometry() {
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor || !this._homeSurface || !this._drawerSurface)
            return;
        const panelHeight = this._active ? 42 : 0;
        const dockHeight = this._active
            ? Math.max(84, this._dockContainer?.height ?? 84)
            : 0;
        this._homeSurface.set_position(monitor.x, monitor.y + panelHeight);
        this._homeSurface.set_size(
            monitor.width, Math.max(1, monitor.height - panelHeight - dockHeight));
        this._drawerSurface.set_position(monitor.x, monitor.y + panelHeight);
        this._drawerSurface.set_size(
            monitor.width, Math.max(1, monitor.height - panelHeight));
        this._drawerSheet.set_size(
            monitor.width, Math.max(1, monitor.height - panelHeight));
    }

    _showHomeSurface() {
        if (!this._active || !this._homeSurface)
            return;
        // A quick upward flick while Home is already visible is a settled
        // state, not another transition. Re-running overview/minimize work in
        // that case caused a one-frame black repaint on the FP6.
        if (this._homeSurface.visible && !this._drawerSurface?.visible &&
            !Main.overview.visible && !this._hasVisibleApplication()) {
            this._syncHomeStacking();
            return;
        }
        this._minimizeAllApplications();
        this._syncDockMode();
        Main.overview.hide();
        this._hideAppDrawer(false);
        this._syncMobileLauncherGeometry();
        this._homeSurface.show();
        this._syncHomeStacking();
    }

    _syncHomeStacking() {
        if (!this._homeSurface ||
            this._homeSurface.get_parent() !== global.window_group)
            return;

        // Home is desktop content, but it is not a Meta.BackgroundActor and it
        // is not an application window. Keep it immediately above Shell's
        // wallpaper group and below the lowest compositor-managed window.
        // Mutter may restack window actors at any time, so the display
        // 'restacked' signal reapplies this invariant.
        const background = Main.layoutManager._backgroundGroup;
        global.window_group.set_child_above_sibling(
            this._homeSurface, background);

        const windowActors = new Set(global.get_window_actors());
        const lowestWindow = global.window_group.get_children()
            .find(child => windowActors.has(child));
        if (lowestWindow)
            global.window_group.set_child_below_sibling(
                this._homeSurface, lowestWindow);
    }

    _hideHomeSurface() {
        this._homeSurface?.hide();
    }

    _showAppDrawer() {
        if (!this._active || !this._drawerSurface)
            return;
        // All Apps is a temporary sheet, not a navigation destination. Back
        // or a downward dismissal must reveal whichever surface launched it.
        // Home deliberately remains mapped beneath application windows, so
        // actor visibility cannot identify the drawer's origin. The visible
        // application state is the authoritative presentation surface.
        this._drawerReturnHome = !this._hasVisibleApplication();
        Main.overview.hide();
        this._drawerSearch.set_text('');
        this._rebuildDrawerApps();
        this._syncMobileLauncherGeometry();
        this._drawerSurface.translation_y = this._drawerSurface.height;
        this._drawerSurface.show();
        Main.uiGroup.set_child_above_sibling(this._drawerSurface, null);
        if (this._dockContainer)
            this._dockContainer.hide();
        this._drawerSurface.ease({
            translation_y: 0,
            duration: 260,
            mode: Clutter.AnimationMode.EASE_OUT_CUBIC,
        });
    }

    _hideAppDrawer(animate = true, showHome = false) {
        if (!this._drawerSurface?.visible) {
            if (showHome)
                this._showHomeSurface();
            return;
        }
        const finish = () => {
            this._drawerSurface.hide();
            this._drawerSurface.translation_y = 0;
            if (this._dockContainer)
                this._dockContainer.show();
            if (showHome)
                this._showHomeSurface();
            else
                this._syncFocusedWindow();
        };
        this._drawerSurface.remove_all_transitions();
        if (!animate) {
            finish();
            return;
        }
        this._drawerSurface.ease({
            translation_y: this._drawerSurface.height,
            duration: 220,
            mode: Clutter.AnimationMode.EASE_IN_CUBIC,
            onComplete: finish,
        });
    }

    _updateDrawerPan(action) {
        if (!this._drawerSurface?.visible)
            return;
        const adjustment = this._drawerScroll.vadjustment;
        const deltaY = action.get_delta().get_y();
        const lower = adjustment.lower;
        const upper = Math.max(lower, adjustment.upper - adjustment.page_size);

        if (deltaY < 0) {
            const sheetDistance = Math.min(
                this._drawerSurface.translation_y, -deltaY);
            this._drawerSurface.translation_y -= sheetDistance;
            const scrollDelta = -deltaY - sheetDistance;
            adjustment.value = Math.clamp(
                adjustment.value + scrollDelta, lower, upper);
            return;
        }

        const scrollDistance = Math.min(
            Math.max(0, adjustment.value - lower), deltaY);
        adjustment.value -= scrollDistance;
        const sheetDistance = deltaY - scrollDistance;
        if (sheetDistance > 0) {
            this._drawerDragDistance += sheetDistance;
            this._drawerSurface.translation_y = Math.min(
                this._drawerSurface.height,
                this._drawerSurface.translation_y + sheetDistance * 0.82);
        }
    }

    _endDrawerPan(action) {
        if (!this._drawerSurface?.visible)
            return;
        const velocityY = action.get_velocity().get_y();
        const dismiss = this._drawerSurface.translation_y >=
            this._drawerSurface.height * 0.16 || velocityY > 650;
        if (dismiss) {
            this._hideAppDrawer(true, this._drawerReturnHome);
            return;
        }

        if (this._drawerSurface.translation_y > 0) {
            this._resetDrawerPan();
            return;
        }

        const adjustment = this._drawerScroll.vadjustment;
        const lower = adjustment.lower;
        const upper = Math.max(lower, adjustment.upper - adjustment.page_size);
        const target = Math.clamp(
            adjustment.value - velocityY * 0.18, lower, upper);
        adjustment.ease(target, {
            duration: 240,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
    }

    _resetDrawerPan() {
        if (!this._drawerSurface?.visible)
            return;
        this._drawerSurface.ease({
            translation_y: 0,
            duration: 180,
            mode: Clutter.AnimationMode.EASE_OUT_QUAD,
        });
        this._drawerDragDistance = 0;
    }

    _launchMobileApp(app) {
        this._cancelHomeLauncherIdle();
        this._mobileLaunchPendingApp = app;
        if (this._mobileLaunchTimeoutId)
            GLib.source_remove(this._mobileLaunchTimeoutId);
        this._mobileLaunchTimeoutId = GLib.timeout_add_once(
            GLib.PRIORITY_DEFAULT, 6000, () => {
                this._mobileLaunchTimeoutId = 0;
                this._mobileLaunchPendingApp = null;
                this._ensureHomeLauncher();
        });
        this._hideAppDrawer(false);
        Main.overview.hide();
        app.activate();
    }

    _cancelHomeLauncherIdle() {
        if (!this._homeLauncherIdleId)
            return;
        GLib.source_remove(this._homeLauncherIdleId);
        this._homeLauncherIdleId = 0;
    }

    _clearMobileLaunchPending() {
        if (this._mobileLaunchTimeoutId) {
            GLib.source_remove(this._mobileLaunchTimeoutId);
            this._mobileLaunchTimeoutId = 0;
        }
        this._mobileLaunchPendingApp = null;
    }

    _allNormalWindows() {
        const windows = [];
        const seen = new Set();
        for (let index = 0;
            index < global.workspace_manager.get_n_workspaces(); index++) {
            const workspace = global.workspace_manager.get_workspace_by_index(index);
            for (const window of global.display.get_tab_list(
                Meta.TabList.NORMAL_ALL, workspace)) {
                if (!seen.has(window) && isNormalWindow(window)) {
                    seen.add(window);
                    windows.push(window);
                }
            }
        }
        return windows;
    }

    _hasRunningApplication() {
        return this._allNormalWindows().length > 0;
    }

    _trackWindowLifecycle(window) {
        if (!window || this._trackedWindowSignals.has(window))
            return;
        const signalId = window.connect('unmanaged', () => {
            this._trackedWindowSignals.delete(window);
            GLib.idle_add_once(GLib.PRIORITY_DEFAULT_IDLE, () => {
                if (!this._trackedWindowSignals)
                    return;
                this._reconcileEmptyActivityView();
                this._syncFocusedWindow();
            });
        });
        this._trackedWindowSignals.set(window, signalId);
    }

    _reconcileEmptyActivityView() {
        if (!this._active || !Main.overview.visible ||
            this._hasRunningApplication())
            return;
        this._resetBottomGesture();
        this._showHomeSurface();
    }

    _startActivityViewGuard() {
        if (this._activityReconcileId)
            return;
        this._activityReconcileId = GLib.timeout_add(
            GLib.PRIORITY_DEFAULT, 80, () => {
                if (!this._active || !Main.overview.visible) {
                    this._activityReconcileId = 0;
                    return GLib.SOURCE_REMOVE;
                }
                if (!this._hasRunningApplication()) {
                    this._activityReconcileId = 0;
                    this._resetBottomGesture();
                    this._showHomeSurface();
                    return GLib.SOURCE_REMOVE;
                }
                return GLib.SOURCE_CONTINUE;
            });
        GLib.Source.set_name_by_id(
            this._activityReconcileId, '[luma] empty activity guard');
    }

    _minimizeAllApplications() {
        this._homeInProgress = true;
        try {
            for (const window of this._allNormalWindows()) {
                if (!window.is_hidden() && window.can_minimize())
                    window.minimize();
            }
        } finally {
            this._homeInProgress = false;
        }
    }

    _syncWorkspacePolicy() {
        if (!this._mutterSettings || !this._wmPreferences)
            return;
        if (this._active) {
            if (this._mutterSettings.get_boolean('dynamic-workspaces'))
                this._mutterSettings.set_boolean('dynamic-workspaces', false);
            if (this._wmPreferences.get_int('num-workspaces') !== 1)
                this._wmPreferences.set_int('num-workspaces', 1);
        } else {
            if (this._mutterSettings.get_boolean('dynamic-workspaces') !==
                this._desktopDynamicWorkspaces)
                this._mutterSettings.set_boolean(
                    'dynamic-workspaces', this._desktopDynamicWorkspaces);
            if (this._wmPreferences.get_int('num-workspaces') !==
                this._desktopWorkspaceCount)
                this._wmPreferences.set_int(
                    'num-workspaces', this._desktopWorkspaceCount);
        }

        if (this._normalWorkspaceSwipeTracker)
            this._normalWorkspaceSwipeTracker.enabled = this._active
                ? false
                : this._normalWorkspaceSwipeWasEnabled;
        const overviewTracker = Main.overview?._overview?.controls
            ?._workspacesDisplay?._swipeTracker ?? null;
        if (overviewTracker)
            overviewTracker.enabled = !this._active;
    }

    _ensureHomeLauncher() {
        if (this._homeLauncherIdleId || !this._active ||
            this._displaySleeping || this._launchApp ||
            this._mobileLaunchPendingApp ||
            this._hasVisibleApplication() || this._homeSurface?.visible ||
            this._drawerSurface?.visible || Main.overview.visible ||
            Main.sessionMode?.isLocked)
            return;

        this._homeLauncherIdleId = GLib.idle_add(
            GLib.PRIORITY_DEFAULT_IDLE, () => {
                this._homeLauncherIdleId = 0;
                if (this._active && !this._displaySleeping &&
                    !this._launchApp && !this._mobileLaunchPendingApp &&
                    !this._hasVisibleApplication() &&
                    !Main.overview.visible && !Main.sessionMode?.isLocked)
                    this._showHomeSurface();
                return GLib.SOURCE_REMOVE;
            });
    }

    _syncOskEdgeGesture() {
        if (!this._oskEdgeGesture)
            return;

        // GNOME's stock bottom-edge action exists solely to reveal the OSK. On
        // handheld Luma that edge belongs to Home/Overview. Disabling this one
        // recognizer does not affect focus-triggered keyboard presentation.
        this._oskEdgeGesture.enabled = this._active
            ? false
            : this._oskEdgeWasEnabled;
    }

    _syncKeyboardNavGesture() {
        // The single stage-level bottom pan participates in touch arbitration
        // for every descendant, including the OSK. Do not attach a second
        // completed-at-80px edge action to the keyboard; two recognizers made
        // Home versus Activity depend on signal ordering.
        if (this._keyboardNavGesture && this._keyboardNavActor)
            this._keyboardNavActor.remove_action(this._keyboardNavGesture);
        this._keyboardNavGesture = null;
        this._keyboardNavActor = null;
    }

    _onKeyboardVisibilityChanged() {
        const visible = Main.keyboard.visible;
        if (this._keyboardWasVisible && !visible)
            this._oskCollapsedAt = GLib.get_monotonic_time();
        this._keyboardWasVisible = visible;
        this._syncOskEdgeGesture();
        this._syncKeyboardNavGesture();
        this._syncKeyboardSettingsButton();
        this._syncDockKeyboardOcclusion();
        this._syncLumaKeyboardSurface();
        if (this._oskSyncIdleId)
            GLib.source_remove(this._oskSyncIdleId);
        this._oskSyncIdleId = GLib.idle_add_once(
            GLib.PRIORITY_DEFAULT_IDLE, () => {
                this._oskSyncIdleId = 0;
                this._syncOskEdgeGesture();
            });
    }

    _syncKeyboardSettingsButton() {
        if (!this._keyboardSettingsButton)
            return;
        // Luma's keyboard owns a proper bottom safe-area control. Retain this
        // floating legacy affordance only as a fallback for the native OSK.
        if (!this._active || !Main.keyboard.visible ||
            this._lumaKeyboardSurface) {
            this._keyboardSettingsButton.hide();
            return;
        }
        const monitor = Main.layoutManager.primaryMonitor;
        const keyboardHeight = Main.layoutManager.keyboardBox.height;
        this._keyboardSettingsButton.set_position(
            monitor.x + monitor.width - 42,
            monitor.y + monitor.height - keyboardHeight - 38);
        this._keyboardSettingsButton.show();
        Main.uiGroup.set_child_above_sibling(
            this._keyboardSettingsButton, null);
    }

    _createLumaKeyboardSurface() {
        this._lumaKeyboardSurface = new St.BoxLayout({
            name: 'lumaHandheldKeyboard',
            style_class: 'luma-handheld-keyboard',
            orientation: Clutter.Orientation.VERTICAL,
            reactive: true,
            visible: false,
        });
        this._lumaCandidateRow = new St.BoxLayout({
            style_class: 'luma-handheld-candidates',
            orientation: Clutter.Orientation.HORIZONTAL,
        });
        for (let index = 0; index < 3; index++) {
            const button = new St.Button({
                style_class: 'luma-handheld-candidate',
                label: '',
                reactive: true,
                can_focus: true,
            });
            button.connect('clicked', () => this._acceptLumaCandidate(index));
            this._lumaCandidateButtons.push(button);
            this._lumaCandidateRow.add_child(button);
        }
        this._lumaKeyboardSurface.add_child(this._lumaCandidateRow);

        const layouts = {
            letters: [
                [...'qwertyuiop'].map(character => ({character, units: 1})),
                [...'asdfghjkl'].map(character => ({character, units: 1})),
                [
                    {iconName: 'osk-shift-symbolic', action: 'shift', units: 1.45},
                    ...[...'zxcvbnm'].map(character => ({character, units: 1})),
                    {iconName: 'osk-delete-symbolic',
                        keyval: Clutter.KEY_BackSpace, units: 1.55},
                ],
                [
                    {label: '?123', action: 'numbers', units: 1,
                        styleClass: 'luma-handheld-mode-key'},
                    {character: ',', units: 1},
                    {label: '', keyval: Clutter.KEY_space, units: 5.2,
                        styleClass: 'luma-handheld-space-key'},
                    {character: '.', units: 1},
                    {iconName: 'osk-enter-symbolic',
                        keyval: Clutter.KEY_Return, units: 1},
                ],
            ],
            numbers: [
                [...'1234567890'].map(character => ({character, units: 1})),
                [...'@#$%&-+()'].map(character => ({character, units: 1})),
                [
                    ...[...`=*\"':;!?`].map(character => ({character, units: 1})),
                    {iconName: 'osk-delete-symbolic',
                        keyval: Clutter.KEY_BackSpace, units: 1.55},
                ],
                [
                    {label: 'ABC', action: 'letters', units: 1,
                        styleClass: 'luma-handheld-mode-key'},
                    {character: ',', units: 1},
                    {label: '', keyval: Clutter.KEY_space, units: 5.2,
                        styleClass: 'luma-handheld-space-key'},
                    {character: '.', units: 1},
                    {iconName: 'osk-enter-symbolic',
                        keyval: Clutter.KEY_Return, units: 1},
                ],
            ],
        };
        for (const [layoutName, rows] of Object.entries(layouts)) {
            for (let rowIndex = 0; rowIndex < rows.length; rowIndex++) {
                const row = new St.BoxLayout({
                    style_class: `luma-handheld-key-row luma-handheld-key-row-${rowIndex + 1}`,
                    orientation: Clutter.Orientation.HORIZONTAL,
                    visible: layoutName === this._lumaKeyboardLayout,
                });
                const entries = [];
                for (const spec of rows[rowIndex]) {
                    const label = spec.character ?? spec.label;
                    const buttonParams = {
                        style_class: `keyboard-key luma-handheld-key ${spec.styleClass ?? ''}`,
                        reactive: true,
                        can_focus: true,
                        toggle_mode: spec.action === 'shift',
                    };
                    if (spec.iconName) {
                        buttonParams.child = new St.Icon({
                            icon_name: spec.iconName,
                        });
                    } else {
                        buttonParams.label = label ?? '';
                    }
                    const button = new St.Button(buttonParams);
                    button._lumaUnits = spec.units;
                    button._lumaCharacter = spec.character ?? null;
                    button._lumaKeyval = spec.keyval ?? null;
                    button._lumaAction = spec.action ?? null;
                    button._lumaTouchActivatedAt = 0;
                    if (button._lumaCharacter?.match(/[a-z]/))
                        this._lumaLetterButtons.push(button);
                    button.connect('clicked', () => {
                        // Touch is committed on TOUCH_BEGIN below so simultaneous
                        // thumbs do not compete for StButton's click gesture.
                        // Preserve clicked as a mouse/accessibility fallback.
                        if (!button._lumaTouchActivatedAt)
                            this._activateLumaKey(button);
                        button._lumaTouchActivatedAt = 0;
                    });
                    row.add_child(button);
                    entries.push(button);
                }
                this._lumaKeyboardRows[layoutName].push({actor: row, entries});
                this._lumaKeyboardSurface.add_child(row);
            }
        }

        this._lumaNavigationRow = new St.BoxLayout({
            style_class: 'luma-handheld-keyboard-navigation',
            orientation: Clutter.Orientation.HORIZONTAL,
            x_expand: true,
        });
        this._lumaInputMethodsButton = new St.Button({
            style_class: 'luma-handheld-keyboard-navigation-button',
            child: new St.Icon({icon_name: 'input-keyboard-symbolic'}),
            reactive: true,
            can_focus: true,
            accessible_name: 'Keyboard settings and input methods',
        });
        this._lumaInputMethodsButton.connect('clicked', () => {
            const app = Shell.AppSystem.get_default().lookup_app(
                'org.project_luma.MobileInputSettings.desktop');
            app?.activate();
            this._closeKeyboardImmediate();
        });
        this._lumaGestureArea = new St.Bin({
            style_class: 'luma-handheld-keyboard-gesture-area',
            x_expand: true,
            reactive: false,
            child: new St.Widget({
                style_class: 'luma-handheld-keyboard-gesture-pill',
                reactive: false,
                x_align: Clutter.ActorAlign.CENTER,
                y_align: Clutter.ActorAlign.CENTER,
            }),
        });
        this._lumaCollapseButton = new St.Button({
            style_class: 'luma-handheld-keyboard-navigation-button',
            child: new St.Icon({icon_name: 'pan-down-symbolic'}),
            reactive: true,
            can_focus: true,
            accessible_name: 'Hide keyboard',
        });
        this._lumaCollapseButton.connect(
            'clicked', () => this._closeKeyboardImmediate());
        this._lumaNavigationRow.add_child(this._lumaInputMethodsButton);
        this._lumaNavigationRow.add_child(this._lumaGestureArea);
        this._lumaNavigationRow.add_child(this._lumaCollapseButton);
        this._lumaKeyboardSurface.add_child(this._lumaNavigationRow);
        Main.uiGroup.add_child(this._lumaKeyboardSurface);
    }

    _activateLumaKey(button, event = null) {
        if (button._lumaAction === 'collapse') {
            this._closeKeyboardImmediate();
            return;
        }
        if (button._lumaAction === 'shift') {
            this._lumaShifted = !this._lumaShifted;
            button.set_checked(this._lumaShifted);
            this._syncLumaLetterLabels();
            return;
        }
        if (button._lumaAction === 'numbers' ||
            button._lumaAction === 'letters') {
            this._lumaKeyboardLayout = button._lumaAction;
            this._lumaShifted = false;
            this._syncLumaKeyboardLayout();
            return;
        }
        let keyval = button._lumaKeyval;
        if (button._lumaCharacter) {
            let character = button._lumaCharacter;
            if (this._lumaShifted && character.match(/[a-z]/))
                character = character.toUpperCase();
            keyval = character.codePointAt(0);
        }
        if (keyval === null || keyval === undefined)
            return;
        const controller = Main.keyboard?.keyboardActor?._keyboardController;
        if (!controller)
            return;
        const character = keyval === Clutter.KEY_BackSpace ||
            keyval === Clutter.KEY_Return
            ? '' : String.fromCodePoint(keyval);
        const point = event ? this._normalizeKeyboardTouch(event) : null;
        this._lumaCommitQueue = this._lumaCommitQueue
            .then(() => this._recordLumaTouch(character, point))
            .then(() => this._commitLumaKeyval(
                controller, keyval, character, point));
        this._lumaCommitQueue = this._lumaCommitQueue.catch(error =>
            console.error(`Luma Handheld: keyboard commit failed: ${error.message}`));
        if (this._lumaShifted && button._lumaCharacter?.match(/[a-z]/)) {
            this._lumaShifted = false;
            this._syncLumaLetterLabels();
        }
    }

    _syncLumaLetterLabels() {
        for (const button of this._lumaLetterButtons)
            button.set_label(this._lumaShifted
                ? button._lumaCharacter.toUpperCase()
                : button._lumaCharacter);
        const shift = this._lumaKeyboardRows.letters[2]?.entries[0];
        shift?.set_checked(this._lumaShifted);
        const shiftIcon = shift?.get_child?.();
        if (shiftIcon)
            shiftIcon.icon_name = this._lumaShifted
                ? 'osk-caps-lock-symbolic' : 'osk-shift-symbolic';
    }

    _syncLumaKeyboardLayout() {
        for (const [layoutName, rows] of Object.entries(this._lumaKeyboardRows)) {
            for (const {actor} of rows)
                actor.visible = layoutName === this._lumaKeyboardLayout;
        }
        this._syncLumaLetterLabels();
        this._syncLumaKeyboardSurface();
    }

    async _commitLumaKeyval(controller, keyval, character, point) {
        // Feed the active IBus engine directly. KeyboardController.commit()
        // may retain the route of the source that existed when Shell created
        // it; handleVirtualKey() is GNOME's authoritative IM path and makes
        // the Luma decoder, preedit, candidates, and autocorrection effective.
        if (Main.inputMethod.currentFocus) {
            try {
                if (await Main.inputMethod.handleVirtualKey(keyval))
                    return;
            } catch (_error) {
                // A toolkit without a usable IM focus still gets the ordinary
                // controller path below; one broken IM must never eat a key.
            }
        }

        try {
            if (this._lumaPredictionAllowed(controller)) {
                if (keyval === Clutter.KEY_BackSpace) {
                    await this._callLumaKeyboard('Backspace', null, '(b)');
                } else if (character.match(/^[A-Za-z]$/)) {
                    await this._callLumaKeyboard(
                        'Compose',
                        new GLib.Variant('(siix)', [
                            character,
                            point?.x ?? -1,
                            point?.y ?? -1,
                            GLib.get_monotonic_time() / 1000,
                        ]),
                        '(b)');
                } else {
                    const autocorrect = keyval === Clutter.KEY_space ||
                        keyval === Clutter.KEY_Return ||
                        character.match(/^[.,!?;:]$/);
                    const [replacement, originalLength] =
                        await this._callLumaKeyboard(
                            'Resolve',
                            new GLib.Variant('(b)', [Boolean(autocorrect)]),
                            '(su)');
                    await this._applyLumaReplacement(
                        controller, replacement, originalLength);
                }
            } else {
                this._setLumaCandidates([]);
            }
        } catch (_error) {
            // Prediction is an enhancement. Never let a stopped or restarting
            // decoder block the ordinary Mutter key-delivery fallback.
            this._setLumaCandidates([]);
        }

        if (keyval === Clutter.KEY_BackSpace) {
            controller.toggleDelete(true);
            controller.toggleDelete(false);
        } else if (keyval === Clutter.KEY_Return) {
            controller.keyvalPress(keyval);
            controller.keyvalRelease(keyval);
        } else {
            await controller.commit(character, new Set());
        }
    }

    _lumaPredictionAllowed(controller) {
        const deniedPurposes = new Set([
            Clutter.InputContentPurpose.DIGITS,
            Clutter.InputContentPurpose.NUMBER,
            Clutter.InputContentPurpose.PHONE,
            Clutter.InputContentPurpose.URL,
            Clutter.InputContentPurpose.EMAIL,
            Clutter.InputContentPurpose.PASSWORD,
            Clutter.InputContentPurpose.TERMINAL,
        ]);
        const hints = controller._contentHints ?? 0;
        const sensitiveHints =
            Clutter.InputContentHintFlags.SENSITIVE_DATA |
            Clutter.InputContentHintFlags.HIDDEN_TEXT;
        return !deniedPurposes.has(controller._purpose) &&
            !(hints & sensitiveHints);
    }

    _callLumaKeyboard(method, parameters, replyType) {
        return new Promise((resolve, reject) => {
            Gio.DBus.session.call(
                'org.project_luma.Keyboard',
                '/org/project_luma/Keyboard',
                'org.project_luma.Keyboard1',
                method,
                parameters,
                new GLib.VariantType(replyType),
                Gio.DBusCallFlags.NONE,
                500,
                null,
                (_connection, result) => {
                    try {
                        resolve(Gio.DBus.session.call_finish(result).deepUnpack());
                    } catch (error) {
                        reject(error);
                    }
                });
        });
    }

    async _applyLumaReplacement(controller, replacement, originalLength) {
        if (!replacement || originalLength < 1)
            return;
        for (let index = 0; index < originalLength; index++) {
            controller.toggleDelete(true);
            controller.toggleDelete(false);
        }
        await controller.commit(replacement, new Set());
    }

    _setLumaCandidates(candidates) {
        for (let index = 0; index < this._lumaCandidateButtons.length; index++) {
            const text = candidates[index] ?? '';
            const button = this._lumaCandidateButtons[index];
            button.set_label(text);
            button.reactive = text.length > 0;
        }
    }

    _acceptLumaCandidate(index) {
        const controller = Main.keyboard?.keyboardActor?._keyboardController;
        if (!controller)
            return;
        this._lumaCommitQueue = this._lumaCommitQueue
            .then(() => this._callLumaKeyboard(
                'SelectCandidate',
                new GLib.Variant('(u)', [index]),
                '(su)'))
            .then(([replacement, originalLength]) =>
                this._applyLumaReplacement(
                    controller, replacement, originalLength))
            .catch(error => console.error(
                `Luma Handheld: candidate selection failed: ${error.message}`));
    }

    _restoreNativeKeyboardActor() {
        if (!this._nativeKeyboardActor)
            return;
        if (this._nativeSuggestions) {
            this._nativeSuggestions.get_parent()?.remove_child(
                this._nativeSuggestions);
            this._nativeKeyboardActor.insert_child_at_index(
                this._nativeSuggestions, 0);
            this._nativeSuggestions = null;
        }
        if (this._nativeKeyboardAspect) {
            this._nativeKeyboardAspect.opacity = this._nativeAspectOpacity;
            this._nativeKeyboardAspect.reactive = this._nativeAspectReactive;
        }
        this._nativeKeyboardActor.opacity = this._nativeKeyboardOpacity;
        this._nativeKeyboardActor.reactive = this._nativeKeyboardReactive;
        this._nativeKeyboardActor = null;
        this._nativeKeyboardAspect = null;
    }

    _syncLumaKeyboardSurface() {
        if (!this._lumaKeyboardSurface)
            return;
        const nativeActor = Main.keyboard?.keyboardActor ?? null;
        if (!this._active || !Main.keyboard.visible || !nativeActor) {
            this._lumaKeyboardSurface.hide();
            this._restoreNativeKeyboardActor();
            return;
        }
        if (this._nativeKeyboardActor !== nativeActor) {
            this._restoreNativeKeyboardActor();
            this._nativeKeyboardActor = nativeActor;
            this._nativeKeyboardOpacity = nativeActor.opacity;
            this._nativeKeyboardReactive = nativeActor.reactive;
            this._nativeKeyboardAspect = nativeActor._aspectContainer ?? null;
            this._nativeAspectOpacity = this._nativeKeyboardAspect?.opacity ?? 255;
            this._nativeAspectReactive = this._nativeKeyboardAspect?.reactive ?? true;
            // The custom row is fed by the same local decoder and works for
            // clients that accept Mutter key events without exposing a full
            // IBus text-input focus (including Prairie's current Flutter UI).
            this._lumaCandidateRow.show();
        }
        if (this._nativeKeyboardAspect) {
            this._nativeKeyboardAspect.opacity = 0;
            this._nativeKeyboardAspect.reactive = false;
        }

        const monitor = Main.layoutManager.primaryMonitor;
        const nativeHeight = Main.layoutManager.keyboardBox.height;
        const typingHeight = Math.max(300, Math.min(
            Math.round(monitor.height * 0.39), nativeHeight || 330));
        const navigationHeight = 54;
        const height = Math.min(
            Math.round(monitor.height * 0.46),
            Math.max(404, typingHeight + navigationHeight));
        const width = monitor.width;
        this._lumaKeyboardSurface.set_position(
            monitor.x, monitor.y + monitor.height - height);
        this._lumaKeyboardSurface.set_size(width, height);

        const sidePadding = 10;
        const gap = 6;
        const candidateHeight = 48;
        const rowHeight = Math.floor((height - candidateHeight -
            navigationHeight - 22 - gap * 4) / 4);
        const candidateRow = this._nativeSuggestions ?? this._lumaCandidateRow;
        candidateRow.set_size(width - sidePadding * 2, candidateHeight);
        for (const button of this._lumaCandidateButtons)
            button.set_size(Math.floor((width - sidePadding * 2 - gap * 2) / 3), candidateHeight);
        for (const {actor, entries} of
            this._lumaKeyboardRows[this._lumaKeyboardLayout]) {
            const innerWidth = width - sidePadding * 2;
            actor.set_size(innerWidth, rowHeight);
            const units = entries.reduce((total, button) => total + button._lumaUnits, 0);
            const usable = innerWidth - gap * (entries.length - 1);
            for (const button of entries)
                button.set_size(Math.floor(usable * button._lumaUnits / units), rowHeight);
        }
        this._lumaNavigationRow.set_size(
            width - sidePadding * 2, navigationHeight);
        this._lumaInputMethodsButton.set_size(48, 48);
        this._lumaCollapseButton.set_size(48, 48);
        this._lumaGestureArea.set_height(48);
        Main.uiGroup.set_child_above_sibling(this._lumaKeyboardSurface, null);
        this._lumaKeyboardSurface.show();
    }

    _recordLumaTouch(character, point) {
        if (!point || !character.match(/[A-Za-z]/))
            return Promise.resolve();
        return new Promise(resolve => {
            Gio.DBus.session.call(
                'org.project_luma.Keyboard',
                '/org/project_luma/Keyboard',
                'org.project_luma.Keyboard1',
                'Touch',
                new GLib.Variant('(siix)', [
                    character, point.x, point.y,
                    GLib.get_monotonic_time() / 1000,
                ]),
                null,
                Gio.DBusCallFlags.NONE,
                100,
                null,
                (_connection, result) => {
                    try {
                        Gio.DBus.session.call_finish(result);
                    } catch (_error) {
                        // Nominal key centers remain a valid decoder fallback.
                    }
                    resolve();
                });
        });
    }

    _refreshLumaCandidates() {
        if (this._candidateGetPending)
            return;
        this._candidateGetPending = true;
        Gio.DBus.session.call(
            'org.project_luma.Keyboard',
            '/org/project_luma/Keyboard',
            'org.freedesktop.DBus.Properties',
            'Get',
            new GLib.Variant('(ss)', [
                'org.project_luma.Keyboard1', 'Candidates',
            ]),
            new GLib.VariantType('(v)'),
            Gio.DBusCallFlags.NONE,
            250,
            null,
            (_connection, result) => {
                this._candidateGetPending = false;
                try {
                    const reply = Gio.DBus.session.call_finish(result);
                    const candidates = reply.get_child_value(0)
                        .get_variant().deepUnpack();
                    this._setLumaCandidates(candidates);
                } catch (_error) {
                    // The engine is demand-started by IBus. Its next signal is
                    // authoritative, so absence during focus transition is OK.
                }
            });
    }

    _requestKeyboardForTextFocus(reason) {
        if (!this._active || Main.keyboard.visible)
            return;
        const sinceCollapseMs = (GLib.get_monotonic_time() -
            this._oskCollapsedAt) / 1000;
        if (this._oskCollapsedAt && sinceCollapseMs < 600)
            return;
        if (this._textCursorTimeoutId)
            GLib.source_remove(this._textCursorTimeoutId);
        this._textCursorTimeoutId = GLib.timeout_add_once(
            GLib.PRIORITY_DEFAULT, 300, () => {
            this._textCursorTimeoutId = 0;
            if (this._active && !Main.keyboard.visible) {
                Main.keyboard.open(Main.layoutManager.focusIndex);
                console.log(`Luma Handheld: keyboard open requested (${reason})`);
            }
        });
    }

    _onCapturedEvent(_actor, event) {
        const type = event.type();
        const source = global.stage.get_event_actor(event);
        this._observeBottomNavigation(event);
        this._observeEdgeNavigation(event);
        if (this._active &&
            (type === Clutter.EventType.TOUCH_BEGIN ||
             type === Clutter.EventType.BUTTON_PRESS) &&
            this._actorHasStyleClass(source, 'show-apps')) {
            this._showAppDrawer();
            return Clutter.EVENT_STOP;
        }
        if (this._active && type === Clutter.EventType.TOUCH_BEGIN) {
            const key = this._findKeyboardKey(
                source, event);
            this._showKeyPreview(key, event);
            if (key?.has_style_class_name?.('luma-handheld-key')) {
                key._lumaTouchActivatedAt = GLib.get_monotonic_time();
                this._activateLumaKey(key, event);
            }
        } else if (type === Clutter.EventType.TOUCH_END ||
                 type === Clutter.EventType.TOUCH_CANCEL)
            this._keyPreview?.hide();

        return Clutter.EVENT_PROPAGATE;
    }

    _observeBottomNavigation(event) {
        if (!this._active)
            return;
        const type = event.type();
        const sequence = event.get_event_sequence();

        if (type === Clutter.EventType.TOUCH_BEGIN) {
            if (this._bottomTouchTracking)
                return;
            const monitor = Main.layoutManager.primaryMonitor;
            if (!monitor ||
                !Boolean((Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW) &
                    Main.actionMode))
                return;
            const [x, y] = event.get_coords();
            if (y < this._bottomNavigationStartY(monitor))
                return;
            this._bottomTouchTracking = true;
            this._bottomTouchSequence = sequence;
            this._bottomTouchStartX = x;
            this._bottomTouchStartY = y;
            this._bottomTouchClaimed = false;
            this._recordGesture('bottomCandidates');
            return;
        }

        /*
         * GJS may wrap ClutterEventSequence with a different JS object for
         * each touch event.  Do not compare wrapper identity here: handheld
         * navigation deliberately tracks only the first active touch and
         * rejects any additional TOUCH_BEGIN above.
         */
        if (!this._bottomTouchTracking)
            return;

        if (type === Clutter.EventType.TOUCH_UPDATE &&
            !this._bottomTouchClaimed) {
            const [x, y] = event.get_coords();
            const deltaX = x - this._bottomTouchStartX;
            const deltaY = y - this._bottomTouchStartY;
            if (Math.hypot(deltaX, deltaY) < BOTTOM_GESTURE_DIRECTION_PX)
                return;
            if (deltaY < 0 && -deltaY >= Math.abs(deltaX)) {
                this._bottomTouchClaimed = true;
                this._recordGesture('bottomRecognized');
                this._beginBottomGesture(true);
            } else {
                this._recordGesture('bottomDirectionRejected');
                this._resetBottomTouch();
            }
            return;
        }

        if (type === Clutter.EventType.TOUCH_END ||
            type === Clutter.EventType.TOUCH_CANCEL) {
            const claimed = this._bottomTouchClaimed;
            this._resetBottomTouch();
            if (claimed)
                this._finishBottomGesture(
                    type === Clutter.EventType.TOUCH_CANCEL);
        }
    }

    _resetBottomTouch() {
        this._bottomTouchTracking = false;
        this._bottomTouchSequence = null;
        this._bottomTouchStartX = 0;
        this._bottomTouchStartY = 0;
        this._bottomTouchClaimed = false;
    }

    _observeEdgeNavigation(event) {
        if (!this._active)
            return;
        const type = event.type();
        const sequence = event.get_event_sequence();

        if (type === Clutter.EventType.TOUCH_BEGIN) {
            if (this._edgeTouchTracking)
                return;
            const monitor = Main.layoutManager.primaryMonitor;
            if (!monitor ||
                !Boolean((Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW) &
                    Main.actionMode))
                return;
            const [x, y] = event.get_coords();
            const right = monitor.x + monitor.width;
            const bottom = monitor.y + monitor.height;
            let side = null;
            if (y <= monitor.y + EDGE_GESTURE_BEGIN_PX)
                side = 'top';
            else if (y < this._bottomNavigationStartY(monitor) &&
                x >= right - EDGE_GESTURE_BEGIN_PX)
                side = 'right';
            if (!side || x < monitor.x || x >= right ||
                y < monitor.y || y >= bottom)
                return;
            this._edgeTouchTracking = true;
            this._edgeTouchSequence = sequence;
            this._edgeTouchSide = side;
            this._edgeTouchStartX = x;
            this._edgeTouchStartY = y;
            this._edgeTouchTriggered = false;
            this._recordGesture('edgeCandidates');
            return;
        }

        if (!this._edgeTouchTracking)
            return;

        if (type === Clutter.EventType.TOUCH_UPDATE &&
            !this._edgeTouchTriggered) {
            const [x, y] = event.get_coords();
            const deltaX = x - this._edgeTouchStartX;
            const deltaY = y - this._edgeTouchStartY;
            if (Math.hypot(deltaX, deltaY) < EDGE_GESTURE_DIRECTION_PX)
                return;
            const correctDirection = this._edgeTouchSide === 'top'
                ? deltaY > 0 && deltaY >= Math.abs(deltaX)
                : deltaX < 0 && -deltaX >= Math.abs(deltaY);
            if (!correctDirection) {
                this._recordGesture('edgeDirectionRejected');
                this._resetEdgeTouch();
                return;
            }
            const travel = this._edgeTouchSide === 'top' ? deltaY : -deltaX;
            if (travel < EDGE_GESTURE_TRAVEL_PX)
                return;
            this._edgeTouchTriggered = true;
            if (this._edgeTouchSide === 'top') {
                this._recordGesture('top');
                this._topSwipeAt(this._edgeTouchStartX);
            } else {
                this._recordGesture('right');
                this._goBack();
            }
            return;
        }

        if (type === Clutter.EventType.TOUCH_END ||
            type === Clutter.EventType.TOUCH_CANCEL)
            this._resetEdgeTouch();
    }

    _resetEdgeTouch() {
        this._edgeTouchTracking = false;
        this._edgeTouchSequence = null;
        this._edgeTouchSide = null;
        this._edgeTouchStartX = 0;
        this._edgeTouchStartY = 0;
        this._edgeTouchTriggered = false;
    }

    _actorHasStyleClass(actor, styleClass) {
        let current = actor;
        while (current) {
            if (current.has_style_class_name?.(styleClass))
                return true;
            current = current.get_parent?.() ?? null;
        }
        return false;
    }

    _showKeyPreview(actor, event) {
        const key = this._findKeyboardKey(actor);
        if (!key?.has_style_class_name?.('keyboard-key')) {
            this._keyPreview.hide();
            return;
        }

        const text = key.get_label?.() ?? key.get_child?.()?.text ?? '';
        if ([...text].length !== 1 || text.trim().length === 0) {
            this._keyPreview.hide();
            return;
        }
        const [x, y] = key.get_transformed_position();
        const [width] = key.get_transformed_size();
        this._keyPreview.text = text;
        this._keyPreview.set_position(
            Math.round(x + width / 2 - 28), Math.max(0, Math.round(y - 64)));
        Main.uiGroup.set_child_above_sibling(this._keyPreview, null);
        this._keyPreview.show();
    }

    _findKeyboardKey(actor, event = null) {
        let key = actor;
        while (key && key !== Main.keyboard?.keyboardActor &&
               key !== this._lumaKeyboardSurface &&
               !key.has_style_class_name?.('keyboard-key'))
            key = key.get_parent?.();
        if (key?.has_style_class_name?.('keyboard-key'))
            return key;
        if (!event || !this._lumaKeyboardSurface?.visible)
            return null;

        const [stageX, stageY] = event.get_coords();
        const [surfaceX, surfaceY] = this._lumaKeyboardSurface
            .get_transformed_position();
        const [surfaceWidth, surfaceHeight] = this._lumaKeyboardSurface
            .get_transformed_size();
        const [, candidatesY] = this._lumaCandidateRow.get_transformed_position();
        const [, candidatesHeight] = this._lumaCandidateRow.get_transformed_size();
        if (stageX < surfaceX || stageX >= surfaceX + surfaceWidth ||
            stageY < candidatesY + candidatesHeight ||
            stageY >= surfaceY + surfaceHeight)
            return null;

        let nearest = null;
        let nearestDistance = Number.POSITIVE_INFINITY;
        for (const candidate of findActorsByStyleClass(
            this._lumaKeyboardSurface, 'luma-handheld-key')) {
            const [x, y] = candidate.get_transformed_position();
            const [width, height] = candidate.get_transformed_size();
            const dx = Math.max(x - stageX, 0, stageX - (x + width));
            const dy = Math.max(y - stageY, 0, stageY - (y + height));
            const distance = dx * dx + dy * dy;
            if (distance < nearestDistance) {
                nearest = candidate;
                nearestDistance = distance;
            }
        }
        return nearest;
    }

    _normalizeKeyboardTouch(event) {
        const keys = findActorsByStyleClass(
            this._lumaKeyboardSurface ?? Main.keyboard?.keyboardActor,
            'keyboard-key');
        if (keys.length === 0)
            return null;
        const boxes = keys.map(key => {
            const [x, y] = key.get_transformed_position();
            const [width, height] = key.get_transformed_size();
            return {x, y, width, height};
        });
        const left = Math.min(...boxes.map(box => box.x));
        const top = Math.min(...boxes.map(box => box.y));
        const right = Math.max(...boxes.map(box => box.x + box.width));
        const bottom = Math.max(...boxes.map(box => box.y + box.height));
        if (right <= left || bottom <= top)
            return null;
        const [stageX, stageY] = event.get_coords();
        const x = Math.max(0, Math.min(999,
            Math.round((stageX - left) * 1000 / (right - left))));
        const y = Math.max(0, Math.min(399,
            Math.round((stageY - top) * 400 / (bottom - top))));
        return {x, y};
    }

    _beginBottomGesture(action) {
        if (this._bottomGestureAction)
            return;
        this._bottomGestureAction = action;
        this._bottomHoldCommitted = false;
        this._bottomPreviewVisible = true;
        this._bottomPreviewStartedAt = GLib.get_monotonic_time();
        Main.panel.closeCalendar();
        Main.panel.closeQuickSettings();
        this._clearMobileLaunchPending();
        this._cancelHomeLauncherIdle();
        this._closeKeyboardImmediate();
        this._bottomHoldId = GLib.timeout_add_once(
            GLib.PRIORITY_DEFAULT, APP_PICKER_HOLD_MS, () => {
                this._bottomHoldId = 0;
                if (!this._bottomGestureAction)
                    return;
                this._bottomHoldCommitted = true;
                if (this._hasRunningApplication()) {
                    this._recordGesture('bottomHoldActivity');
                    this._showWindowPicker();
                } else {
                    this._recordGesture('bottomHoldHome');
                    this._showHomeSurface();
                }
            });
    }

    _finishBottomGesture(cancelled) {
        if (!this._bottomGestureAction)
            return;
        const committed = this._bottomHoldCommitted;
        this._resetBottomGesture();
        if (cancelled)
            this._recordGesture('bottomCancelled');
        else if (!committed) {
            this._recordGesture('bottomQuickHome');
            this._goHome();
        }
    }

    _resetBottomGesture() {
        if (this._bottomHoldId) {
            GLib.source_remove(this._bottomHoldId);
            this._bottomHoldId = 0;
        }
        this._bottomGestureAction = null;
        this._bottomHoldCommitted = false;
        this._bottomPreviewVisible = false;
        this._bottomPreviewStartedAt = 0;
    }

    _syncIdleWatch() {
        if (this._idleWatchId) {
            this._idleMonitor.remove_watch(this._idleWatchId);
            this._idleWatchId = 0;
        }
        const delay = this._idleSettings.get_uint('idle-delay');
        if (this._idleRearmTimeoutId)
            return;
        if (!this._active || this._displaySleeping || delay === 0)
            return;
        this._idleWatchId = this._idleMonitor.add_idle_watch(
            delay * 1000, () => {
                this._idleWatchId = 0;
                this._sleepDisplay('idle');
            });
    }

    _armIdleAfterWake() {
        if (this._idleRearmTimeoutId) {
            GLib.source_remove(this._idleRearmTimeoutId);
            this._idleRearmTimeoutId = 0;
        }
        const delay = this._idleSettings.get_uint('idle-delay');
        if (!this._active || this._displaySleeping || delay === 0) {
            this._syncIdleWatch();
            return;
        }
        // A power-key read from evdev is intentionally outside Clutter, so it
        // does not reset Mutter's private idle clock. Give the freshly woken
        // panel one complete configured interval before consulting that clock
        // again. Real touch/key activity during this grace period still resets
        // Mutter normally and therefore extends the subsequent idle watch.
        this._idleRearmTimeoutId = GLib.timeout_add_once(
            GLib.PRIORITY_DEFAULT, delay * 1000, () => {
                this._idleRearmTimeoutId = 0;
                this._syncIdleWatch();
            });
    }

    _toggleDisplaySleep() {
        if (this._displaySleeping)
            this._wakeDisplay();
        else
            this._sleepDisplay('power-key');
    }

    _closeKeyboardImmediate() {
        if (Main.keyboard.keyboardActor)
            Main.keyboard.keyboardActor.close(true);
        else
            Main.keyboard.close();
    }

    ToggleDisplay() {
        // A powered-down output may temporarily remove portrait monitor
        // geometry, which makes _active false. Wake must never depend on the
        // very display state it is responsible for restoring.
        if (this._displaySleeping)
            this._wakeDisplay();
        else if (this._active)
            this._sleepDisplay('power-key');
    }

    ShowAppSwitcher() {
        if (this._active)
            this._showWindowPicker();
    }

    SleepDisplay() {
        if (this._active)
            this._sleepDisplay('power-key', true);
    }

    WakeDisplay() {
        this._wakeDisplay();
    }

    GetAndroidGeometry() {
        return this._lastAndroidGeometry || '{}';
    }

    GetGestureDiagnostics() {
        return JSON.stringify(this._gestureDiagnostics ?? {});
    }

    SetExternalKeyboardVisible(visible) {
        this._externalKeyboardVisible = visible;
        this._syncDockKeyboardOcclusion();
        console.log(`Luma Handheld: external keyboard ${visible ? 'visible' : 'hidden'}`);
    }

    get Sleeping() {
        return this._displaySleeping;
    }

    _sleepDisplay(reason, force = false) {
        if (this._displaySleeping && !force)
            return;
        if (this._idleRearmTimeoutId) {
            GLib.source_remove(this._idleRearmTimeoutId);
            this._idleRearmTimeoutId = 0;
        }
        this._displaySleeping = true;
        this._syncIdleWatch();
        this._closeKeyboardImmediate();
        Main.screenShield?.lock?.(true);
        if (this._displayPowerTimeoutId)
            GLib.source_remove(this._displayPowerTimeoutId);
        if (this._textCursorTimeoutId)
            GLib.source_remove(this._textCursorTimeoutId);
        this._displayPowerTimeoutId = GLib.timeout_add_once(
            GLib.PRIORITY_DEFAULT, 350, () => {
                this._displayPowerTimeoutId = 0;
                this._setDisplayPower(3);
            });
        this._handheldDbus.emit_property_changed(
            'Sleeping', new GLib.Variant('b', true));
        console.log(`Luma Handheld: display sleep (${reason})`);
    }

    _wakeDisplay() {
        const wasSleeping = this._displaySleeping;
        if (this._displayPowerTimeoutId) {
            GLib.source_remove(this._displayPowerTimeoutId);
            this._displayPowerTimeoutId = 0;
        }
        this._setDisplayPower(0);
        this._displaySleeping = false;
        this._handheldDbus.emit_property_changed(
            'Sleeping', new GLib.Variant('b', false));
        Main.screenShield?._wakeUpScreen?.();
        this._armIdleAfterWake();
        if (wasSleeping)
            console.log('Luma Handheld: display wake (power-key)');
    }

    _setDisplayPower(mode) {
        Gio.DBus.session.call(
            'org.gnome.Mutter.DisplayConfig',
            '/org/gnome/Mutter/DisplayConfig',
            'org.freedesktop.DBus.Properties',
            'Set',
            new GLib.Variant('(ssv)', [
                'org.gnome.Mutter.DisplayConfig',
                'PowerSaveMode',
                new GLib.Variant('i', mode),
            ]),
            null,
            Gio.DBusCallFlags.NONE,
            -1,
            null,
            (_connection, result) => {
                try {
                    Gio.DBus.session.call_finish(result);
                } catch (error) {
                    console.error(`Luma Handheld: display power failed: ${error.message}`);
                }
            });
    }

    _onAppStateChanged(_appSystem, app) {
        if (!this._active)
            return;
        if (app.state === Shell.AppState.STARTING)
            this._showLaunchSurface(app);
        else if (app.state === Shell.AppState.STOPPED) {
            const wasPending = this._mobileLaunchPendingApp === app;
            if (wasPending)
                this._clearMobileLaunchPending();
            if (this._launchApp === app) {
                if (app.get_id().startsWith('waydroid.'))
                    this._showAndroidLaunchFailure(app);
                else
                    this._hideLaunchSurface('stopped');
            }
            if (wasPending)
                this._ensureHomeLauncher();
        }
    }

    _showLaunchSurface(app) {
        this._hideLaunchSurface('superseded');
        this._launchApp = app;
        this._launchStartedAt = GLib.get_monotonic_time();
        this._launchIcon.set_child(app.create_icon_texture(80));
        this._launchLabel.text = `Opening ${app.get_name()}…`;
        this._launchDetail.text = '';
        this._syncLaunchSurfaceGeometry();
        Main.uiGroup.set_child_above_sibling(this._launchSurface, null);
        this._launchSurface.show();
        this._launchTimeoutId = GLib.timeout_add_once(
            GLib.PRIORITY_DEFAULT, 5000,
            () => this._hideLaunchSurface('timeout'));
        console.log(`Luma Handheld: launch acknowledged ${app.get_id()}`);
    }

    _showAndroidLaunchFailure(app) {
        if (this._launchApp !== app)
            return;
        if (this._launchTimeoutId) {
            GLib.source_remove(this._launchTimeoutId);
            this._launchTimeoutId = 0;
        }
        this._launchLabel.text = `${app.get_name()} couldn’t open`;
        this._launchDetail.text = 'Android stopped before the application appeared.';
        this._launchTimeoutId = GLib.timeout_add_once(
            GLib.PRIORITY_DEFAULT, 8000,
            () => this._hideLaunchSurface('android-failed'));

        let process;
        try {
            process = Gio.Subprocess.new(
                ['/usr/bin/luma-android', 'status'],
                Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE);
        } catch (error) {
            console.error(`Luma Handheld: Android status failed: ${error.message}`);
            return;
        }
        process.communicate_utf8_async(null, null, (source, result) => {
            try {
                const [, stdout] = source.communicate_utf8_finish(result);
                if (this._launchApp !== app)
                    return;
                const status = JSON.parse(stdout);
                if (status.fp6_kernel_admitted === false) {
                    this._launchDetail.text =
                        'Android graphics are blocked on this Fairphone kernel ' +
                        'to prevent another system freeze.';
                }
            } catch (error) {
                console.error(`Luma Handheld: Android status parse failed: ${error.message}`);
            }
        });
        console.warn(`Luma Handheld: Android launch failed ${app.get_id()}`);
    }

    _syncLaunchSurfaceGeometry() {
        if (!this._launchSurface)
            return;
        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const workArea = Main.layoutManager.getWorkAreaForMonitor(
            Main.layoutManager.primaryIndex);
        this._launchSurface.set_position(workArea.x, workArea.y);
        this._launchSurface.set_size(workArea.width, workArea.height);
    }

    _hideLaunchSurface(reason) {
        if (!this._launchApp)
            return;
        if (this._launchTimeoutId) {
            GLib.source_remove(this._launchTimeoutId);
            this._launchTimeoutId = 0;
        }
        const elapsedMs = Math.round(
            (GLib.get_monotonic_time() - this._launchStartedAt) / 1000);
        console.log(`Luma Handheld: launch ready ${this._launchApp.get_id()} ` +
            `${elapsedMs}ms (${reason})`);
        this._launchSurface.hide();
        this._launchIcon.set_child(null);
        this._launchDetail.text = '';
        this._launchApp = null;
        this._launchStartedAt = 0;
    }

    _syncClockPresentation() {
        const dateDisplay = Main.panel.statusArea.dateMenu?._dateDisplay;
        const clockContent = dateDisplay?.get_parent();
        const dateIndex = clockContent?.get_children().indexOf(dateDisplay) ?? -1;
        const separator = dateIndex > 0
            ? clockContent.get_child_at_index(dateIndex - 1)
            : null;
        if (separator)
            separator.visible = !this._active;
    }

    _attachDockPan() {
        if (this._dockPanAction)
            return GLib.SOURCE_REMOVE;

        const dockContainer = findActorByName(
            Main.uiGroup, 'dashtodockContainer');
        const scrollView = findActorByName(
            dockContainer, 'dashtodockDashScrollview');
        const dockContent = findActorByName(
            dockContainer, 'dashtodockDashContainer');
        const dockBackground = findActorByStyleClass(
            dockContainer, 'dash-background');
        if (!dockContainer || !scrollView || !dockContent || !dockBackground)
            return GLib.SOURCE_CONTINUE;

        const panAction = new Clutter.PanGesture({
            pan_axis: Clutter.PanAxis.X,
            min_n_points: 1,
            max_n_points: 1,
        });
        panAction.connect('may-recognize', () => this._active);
        panAction.connect('pan-update', action => {
            const delta = action.get_delta();
            scrollView.hadjustment.value -= delta.get_x();
        });
        scrollView.add_action(panAction);
        this._dockPanDestroyId = scrollView.connect('destroy', () => {
            this._dockPanAction = null;
            this._dockPanScrollView = null;
            this._dockPanDestroyId = 0;
        });
        this._dockPanAction = panAction;
        this._dockContainer = dockContainer;
        this._dockPanScrollView = scrollView;
        this._dockContent = dockContent;
        this._dockBackground = dockBackground;
        this._dockBackgroundAllocationId = dockBackground.connect(
            'notify::allocation', () => this._syncDockFillGeometry());
        const dockParent = dockContainer.get_parent();
        if (this._dockFill.get_parent() !== dockParent) {
            this._dockFill.get_parent()?.remove_child(this._dockFill);
            dockParent.add_child(this._dockFill);
        }
        dockParent.set_child_below_sibling(this._dockFill, dockContainer);
        this._syncDockContentAlignment();
        this._syncDockKeyboardOcclusion();
        this._syncDockFillGeometry();
        this._dockPanRetryId = 0;
        return GLib.SOURCE_REMOVE;
    }

    _syncDockContentAlignment() {
        if (this._dockContent)
            this._dockContent.translation_y = this._active ? 5 : 0;
    }

    _hasVisibleApplication() {
        return this._allNormalWindows().some(window => !window.is_hidden());
    }

    _syncDockMode() {
        const appOpen = this._active && this._hasVisibleApplication();
        this._dockAppOpen = appOpen;

        if (appOpen)
            Main.uiGroup.add_style_class_name('luma-handheld-app-open');
        else
            Main.uiGroup.remove_style_class_name('luma-handheld-app-open');

        // The layout never changes between home and app mode. Only a separate
        // actor beneath the dock paints across the side and bottom safe area.
        // This preserves the dock top edge and every icon coordinate exactly.
        const fraction = HOME_DOCK_FRACTION;
        if (Math.abs(this._dockSettings.get_double('height-fraction') -
            fraction) > 0.0005)
            this._dockSettings.set_double('height-fraction', fraction);
        if (this._dockSettings.get_boolean('extend-height'))
            this._dockSettings.set_boolean('extend-height', false);

        GLib.idle_add_once(GLib.PRIORITY_DEFAULT_IDLE,
            () => this._syncDockFillGeometry());
        this._syncDockKeyboardOcclusion();
    }

    _syncDockKeyboardOcclusion() {
        if (!this._dockContainer)
            return;
        const occluded = this._active &&
            (Boolean(Main.keyboard?.visible) || this._externalKeyboardVisible ||
                Boolean(this._drawerSurface?.visible));
        this._dockContainer.visible = !occluded;
        if (occluded)
            this._dockFill?.hide();
    }

    _syncDockFillGeometry() {
        if (!this._dockFill)
            return;
        const keyboardVisible = Boolean(Main.keyboard?.visible) ||
            this._externalKeyboardVisible;
        if (!this._dockAppOpen || !this._active || keyboardVisible ||
            !this._dockBackground) {
            this._dockFill.hide();
            return;
        }

        const monitor = Main.layoutManager.primaryMonitor;
        if (!monitor)
            return;
        const [, backgroundY] = this._dockBackground.get_transformed_position();
        const [parentX, parentY] = this._dockFill.get_parent()
            .get_transformed_position();
        const top = Math.round(backgroundY);
        this._dockFill.set_position(monitor.x - parentX, top - parentY);
        this._dockFill.set_size(
            monitor.width, Math.max(1, monitor.y + monitor.height - top));
        this._dockFill.show();
    }

    _detachDockPan() {
        if (this._dockBackgroundAllocationId && this._dockBackground)
            this._dockBackground.disconnect(this._dockBackgroundAllocationId);
        if (this._dockContent)
            this._dockContent.translation_y = 0;
        if (this._dockPanDestroyId && this._dockPanScrollView)
            this._dockPanScrollView.disconnect(this._dockPanDestroyId);
        if (this._dockPanAction && this._dockPanScrollView)
            this._dockPanScrollView.remove_action(this._dockPanAction);
        if (this._dockContainer)
            this._dockContainer.visible = true;
        this._dockPanAction = null;
        this._dockContainer = null;
        this._dockPanScrollView = null;
        this._dockContent = null;
        this._dockBackground = null;
        this._dockBackgroundAllocationId = 0;
        this._dockPanDestroyId = 0;
    }

    _bottomNavigationStartY(monitor) {
        const bottom = monitor.y + monitor.height;
        const dockPosition = this._dockBackground
            ?.get_transformed_position?.() ?? [];
        const dockY = dockPosition[1];
        if (Number.isFinite(dockY))
            return Math.max(monitor.y, Math.min(
                bottom - 28, Math.floor(dockY) - 8));
        return Math.max(monitor.y, bottom - BOTTOM_GESTURE_FALLBACK_PX);
    }

    _recordGesture(name) {
        if (!this._gestureDiagnostics)
            return;
        if (Object.hasOwn(this._gestureDiagnostics, name))
            this._gestureDiagnostics[name]++;
        this._gestureDiagnostics.last = name;
        this._gestureDiagnostics.lastAtMonotonicMs = Math.round(
            GLib.get_monotonic_time() / 1000);
    }

    _topSwipeAt(beginX) {
        const monitor = Main.layoutManager.primaryMonitor;
        if (monitor && beginX >= monitor.x + monitor.width / 2)
            this._openQuickSettings();
        else
            this._openCalendar();
    }

    _showWindowPicker() {
        if (!this._hasRunningApplication()) {
            this._showHomeSurface();
            return;
        }
        Main.panel.closeCalendar();
        Main.panel.closeQuickSettings();
        this._clearMobileLaunchPending();
        this._cancelHomeLauncherIdle();
        this._hideAppDrawer(false);
        this._bottomPreviewVisible = false;
        this._bottomPreviewStartedAt = 0;
        // Use Shell's compositor-native running-window picker. The FP6 rejected
        // both live-clone and static-card overlays with multi-second touch lag.
        Main.overview.show();
        this._startActivityViewGuard();
    }

    _goHome() {
        Main.panel.closeCalendar();
        Main.panel.closeQuickSettings();
        this._clearMobileLaunchPending();
        this._cancelHomeLauncherIdle();
        this._bottomPreviewVisible = false;
        this._bottomPreviewStartedAt = 0;
        if (this._drawerSurface?.visible) {
            this._hideAppDrawer(true, true);
            return;
        }
        GLib.idle_add_once(GLib.PRIORITY_DEFAULT_IDLE, () => {
            // Home is the persistent desktop layer below application windows.
            this._showHomeSurface();
        });
    }

    _openCalendar() {
        Main.panel.closeQuickSettings();
        Main.panel.toggleCalendar();
    }

    _openQuickSettings() {
        Main.panel.closeCalendar();
        Main.panel.toggleQuickSettings();
    }

    _goBack() {
        if (this._drawerSurface?.visible) {
            this._hideAppDrawer(true, this._drawerReturnHome);
            return;
        }
        if (Main.overview.visible) {
            Main.overview.hide();
            this._showHomeSurface();
            return;
        }

        Main.panel.closeCalendar();
        Main.panel.closeQuickSettings();

        const focused = global.display.focus_window;
        if (isAndroidWindow(focused)) {
            // Waydroid maps a focused hardware Escape key to Android Back. Keep
            // the gesture in the existing Wayland input path: no root shell,
            // global Android launcher, or second navigation surface is needed.
            const time = Clutter.get_current_event_time() * 1000;
            this._virtualKeyboard.notify_keyval(
                time, Clutter.KEY_Escape, Clutter.KeyState.PRESSED);
            this._virtualKeyboard.notify_keyval(
                time, Clutter.KEY_Escape, Clutter.KeyState.RELEASED);
            return;
        }

        // Alt+Left is the freedesktop/GTK navigation convention used by Filer,
        // Firefox, Settings, Help, and other navigation-stack applications.
        const time = Clutter.get_current_event_time() * 1000;
        this._virtualKeyboard.notify_keyval(
            time, Clutter.KEY_Alt_L, Clutter.KeyState.PRESSED);
        this._virtualKeyboard.notify_keyval(
            time, Clutter.KEY_Left, Clutter.KeyState.PRESSED);
        this._virtualKeyboard.notify_keyval(
            time, Clutter.KEY_Left, Clutter.KeyState.RELEASED);
        this._virtualKeyboard.notify_keyval(
            time, Clutter.KEY_Alt_L, Clutter.KeyState.RELEASED);
    }

    _syncFocusedWindow() {
        this._syncDockMode();
        if (!this._active || this._syncingWindows || this._homeInProgress)
            return;

        const focused = global.display.focus_window;
        if (!isNormalWindow(focused)) {
            this._reconcileEmptyActivityView();
            this._ensureHomeLauncher();
            return;
        }

        const focusedApp = Shell.WindowTracker.get_default()
            .get_window_app(focused);
        if (focusedApp === this._mobileLaunchPendingApp ||
            (this._mobileLaunchPendingApp?.get_id().startsWith('waydroid.') &&
                isAndroidWindow(focused)))
            this._clearMobileLaunchPending();

        this._hideAppDrawer(false);

        if (this._launchApp) {
            if (focusedApp === this._launchApp ||
                (this._launchApp.get_id().startsWith('waydroid.') &&
                    isAndroidWindow(focused)))
                this._hideLaunchSurface('window-focused');
        }

        this._syncingWindows = true;
        try {
            if (isAndroidWindow(focused)) {
                this._fitAndroidWindow(focused);
                this._logAndroidGeometry(focused);
            } else if (focused.can_maximize() && !focused.is_maximized()) {
                focused.maximize(Meta.MaximizeFlags.BOTH);
            }

            const workspace = focused.get_workspace();
            const windows = global.display.get_tab_list(
                Meta.TabList.NORMAL, workspace);
            for (const window of windows) {
                if (window === focused || !isNormalWindow(window) ||
                    window.is_hidden() || !window.can_minimize())
                    continue;
                window.minimize();
            }
        } finally {
            this._syncingWindows = false;
        }
    }

    _fitAndroidWindow(window) {
        const fit = () => {
            if (!isAndroidWindow(window))
                return;
            const workArea = Main.layoutManager.getWorkAreaForMonitor(
                window.get_monitor());
            const frame = window.get_frame_rect();
            if (frame.x === workArea.x && frame.y === workArea.y &&
                frame.width === workArea.width &&
                frame.height === workArea.height)
                return;
            if (window.is_maximized())
                window.unmaximize(Meta.MaximizeFlags.BOTH);
            window.move_resize_frame(false, workArea.x, workArea.y,
                workArea.width, workArea.height);
        };
        fit();
        GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 250, () => {
            fit();
            this._logAndroidGeometry(window);
        });
    }

    _logAndroidGeometry(window) {
        GLib.timeout_add_once(GLib.PRIORITY_DEFAULT, 350, () => {
            if (!isAndroidWindow(window))
                return;
            const monitorIndex = window.get_monitor();
            const frame = window.get_frame_rect();
            const workArea = Main.layoutManager.getWorkAreaForMonitor(monitorIndex);
            const dock = findActorByName(Main.uiGroup, 'dashtodockContainer');
            const dockBackground = findActorByStyleClass(dock, 'dash-background');
            const dockPosition = dockBackground?.get_transformed_position?.() ?? [];
            const dockSize = dockBackground?.get_transformed_size?.() ?? [];
            const geometry = JSON.stringify({
                frame: {x: frame.x, y: frame.y,
                    width: frame.width, height: frame.height},
                workArea: {x: workArea.x, y: workArea.y,
                    width: workArea.width, height: workArea.height},
                dock: {x: Math.round(dockPosition[0] ?? -1),
                    y: Math.round(dockPosition[1] ?? -1),
                    width: Math.round(dockSize[0] ?? -1),
                    height: Math.round(dockSize[1] ?? -1)},
            });
            if (geometry !== this._lastAndroidGeometry) {
                this._lastAndroidGeometry = geometry;
                console.log(`Luma Handheld: Android geometry ${geometry}`);
            }
        });
    }

    disable() {
        if (this._displayPowerTimeoutId) {
            GLib.source_remove(this._displayPowerTimeoutId);
            this._displayPowerTimeoutId = 0;
        }
        if (this._displaySleeping) {
            this._setDisplayPower(0);
            this._displaySleeping = false;
        }
        if (this._monitorsId)
            Main.layoutManager.disconnect(this._monitorsId);
        if (this._focusId)
            global.display.disconnect(this._focusId);
        if (this._workspaceId)
            global.workspace_manager.disconnect(this._workspaceId);
        if (this._windowCreatedId)
            global.display.disconnect(this._windowCreatedId);
        if (this._keyboardVisibilityId)
            Main.keyboard.disconnect(this._keyboardVisibilityId);
        if (this._ibusFocusInId)
            this._ibusManager.disconnect(this._ibusFocusInId);
        if (this._capturedEventId)
            global.stage.disconnect(this._capturedEventId);
        if (this._appStateId)
            this._appSystem.disconnect(this._appStateId);
        if (this._installedAppsId)
            this._appSystem.disconnect(this._installedAppsId);
        if (this._favoritesId)
            global.settings.disconnect(this._favoritesId);
        if (this._overviewShowingId)
            Main.overview.disconnect(this._overviewShowingId);
        if (this._overviewShownId)
            Main.overview.disconnect(this._overviewShownId);
        if (this._overviewHidingId)
            Main.overview.disconnect(this._overviewHidingId);
        if (this._overviewHiddenId)
            Main.overview.disconnect(this._overviewHiddenId);
        if (this._restackedId)
            global.display.disconnect(this._restackedId);
        if (this._idleSettingsId)
            this._idleSettings.disconnect(this._idleSettingsId);
        if (this._idleWatchId)
            this._idleMonitor.remove_watch(this._idleWatchId);
        if (this._idleRearmTimeoutId)
            GLib.source_remove(this._idleRearmTimeoutId);
        if (this._displayPowerTimeoutId)
            GLib.source_remove(this._displayPowerTimeoutId);
        if (this._oskSyncIdleId)
            GLib.source_remove(this._oskSyncIdleId);
        if (this._homeLauncherIdleId)
            GLib.source_remove(this._homeLauncherIdleId);
        this._homeLauncherIdleId = 0;
        if (this._activityReconcileId)
            GLib.source_remove(this._activityReconcileId);
        this._activityReconcileId = 0;
        if (this._mobileLaunchTimeoutId)
            GLib.source_remove(this._mobileLaunchTimeoutId);
        this._mobileLaunchTimeoutId = 0;
        this._mobileLaunchPendingApp = null;
        if (this._keyboardCandidateSubscriptionId)
            Gio.DBus.session.signal_unsubscribe(
                this._keyboardCandidateSubscriptionId);
        this._keyboardCandidateSubscriptionId = 0;
        this._handheldDbus?.unexport();
        this._handheldDbus = null;
        Gio.DBus.session.call(
            'org.freedesktop.DBus',
            '/org/freedesktop/DBus',
            'org.freedesktop.DBus',
            'ReleaseName',
            new GLib.Variant('(s)', ['org.project_luma.Handheld']),
            null,
            Gio.DBusCallFlags.NONE,
            -1,
            null,
            null);
        this._resetBottomGesture();
        this._resetBottomTouch();
        this._resetEdgeTouch();
        if (this._dockPanRetryId)
            GLib.source_remove(this._dockPanRetryId);

        if (this._oskEdgeGesture)
            this._oskEdgeGesture.enabled = this._oskEdgeWasEnabled;
        if (this._keyboardNavGesture && this._keyboardNavActor)
            this._keyboardNavActor.remove_action(this._keyboardNavGesture);
        this._keyboardNavGesture = null;
        this._keyboardNavActor = null;

        this._detachDockPan();

        this._active = false;
        this._syncWorkspacePolicy();
        this._externalKeyboardVisible = false;
        this._dockAppOpen = false;
        Main.uiGroup.remove_style_class_name('luma-handheld-app-open');
        if (this._dockSettings && Math.abs(
            this._dockSettings.get_double('height-fraction') -
            HOME_DOCK_FRACTION) > 0.0005)
            this._dockSettings.set_double(
                'height-fraction', HOME_DOCK_FRACTION);
        if (this._dockSettings?.get_boolean('extend-height'))
            this._dockSettings.set_boolean('extend-height', false);
        this._dockFill?.destroy();
        this._dockFill = null;
        this._homeSurface?.destroy();
        this._homeSurface = null;
        this._homeScroll = null;
        this._homeGrid = null;
        if (this._drawerPanGesture && this._drawerSurface)
            this._drawerSurface.remove_action(this._drawerPanGesture);
        this._drawerPanGesture = null;
        this._drawerSurface?.destroy();
        this._drawerSurface = null;
        this._drawerSheet = null;
        this._drawerFavorites = null;
        this._drawerSearch = null;
        this._drawerScroll = null;
        this._drawerGrid = null;
        this._hideLaunchSurface('disabled');
        this._launchSurface?.destroy();
        this._launchSurface = null;
        this._launchBox = null;
        this._launchIcon = null;
        this._launchLabel = null;
        this._keyboardSettingsButton?.destroy();
        this._keyboardSettingsButton = null;
        this._keyPreview?.destroy();
        this._keyPreview = null;
        this._restoreNativeKeyboardActor();
        this._lumaKeyboardSurface?.destroy();
        this._lumaKeyboardSurface = null;
        this._lumaKeyboardRows = null;
        this._lumaLetterButtons = null;
        this._lumaCandidateButtons = null;
        this._lumaCommitQueue = null;
        this._syncClockPresentation();
        Main.uiGroup.remove_style_class_name('luma-handheld');
        Main.panel.remove_style_class_name('luma-handheld');

        if (this._theme && this._stylesheet)
            this._theme.unload_stylesheet(this._stylesheet);

        this._edges = null;
        this._bottomGesture = null;
        this._dockPanRetryId = 0;
        this._dockSettings = null;
        this._appSystem = null;
        for (const [window, signalId] of this._trackedWindowSignals)
            window.disconnect(signalId);
        this._trackedWindowSignals.clear();
        this._trackedWindowSignals = null;
        this._idleSettings = null;
        this._idleMonitor = null;
        this._idleRearmTimeoutId = 0;
        this._ibusManager = null;
        this._virtualKeyboard = null;
        this._oskEdgeGesture = null;
        this._stylesheet = null;
        this._theme = null;
        console.log('Luma Handheld: interaction policy disabled');
    }
}
