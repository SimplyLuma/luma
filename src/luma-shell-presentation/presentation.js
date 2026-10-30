// SPDX-License-Identifier: Apache-2.0
// Native Mutter/St presentation. The shared state service owns mode policy.
import Atk from 'gi://Atk';
import Clutter from 'gi://Clutter';
import Gio from 'gi://Gio';
import GioUnix from 'gi://GioUnix';
import GLib from 'gi://GLib';
import GnomeDesktop from 'gi://GnomeDesktop';
import Meta from 'gi://Meta';
import Pango from 'gi://Pango';
import Shell from 'gi://Shell';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import {peekNotificationLip} from 'resource:///org/gnome/shell/ui/lumaNotificationBeacon.js';
import {QuickMenuToggle} from 'resource:///org/gnome/shell/ui/quickSettings.js';
import {SwipeTracker} from 'resource:///org/gnome/shell/ui/swipeTracker.js';
import {homeLayout, pageProgress, visiblePageRange} from './layout.js';
import {readDisplayGeometry, statusInsets} from './safeAreas.js';
import {HandheldPowerButton} from './powerButton.js';
import {reconcileGrid} from './grid.js';
import {HomeEditor} from './homeEditor.js';
import {HomeWidgets} from './widgets.js';
import {TabletTasks, HandheldMotion} from './tabletTasks.js';
import {SwitcherDrag} from './switcherDrag.js';
import {HandheldOptions} from './handheldOptions.js';
import {tabletFrame} from './frameTokens.js';
import {gettext as _} from 'gettext';

const BUS = 'org.project_luma.ShellState1';
const PATH = '/org/project_luma/ShellState1';

function move(actor, parent) {
    actor.get_parent()?.remove_child(actor);
    parent.add_child(actor);
}

function button(name, onClick, styleClass = '') {
    const result = new St.Button({accessible_name: name, can_focus: true,
        reactive: true, track_hover: true, style_class: styleClass});
    result.connect('clicked', onClick);
    return result;
}

export class Presentation {
    constructor({stylesheet = null, widgets = null, settings = null} = {}) {
        this.mode = 'desktop';
        this._cancellable = new Gio.Cancellable();
        this._destroyed = false;
        this._stylesheet = stylesheet;
        if (stylesheet)
            St.ThemeContext.get_for_stage(global.stage).get_theme().load_stylesheet(stylesheet);
        this._signals = [];
        this._windows = new Map();
        this._homeMinimized = new Set();
        this._lastFocusedWindow = global.display.focus_window;
        this._statusWindowSignals = [];
        this._statusActionSignals = [];
        this._settings = settings ?? new Gio.Settings({schema_id: 'org.project_luma.shell-state'});
        this._powerButton = new HandheldPowerButton();
        this._motion = new HandheldMotion(this);
        this._page = 0;
        this._displayGeometry = null;
        for (const path of ['/etc/luma/display-geometry.json', '/usr/share/luma/display-geometry.json']) {
            try {
                const [ok, bytes] = GLib.file_get_contents(path);
                if (ok) this._displayGeometry = readDisplayGeometry(new TextDecoder().decode(bytes));
                if (this._displayGeometry) break;
            } catch { /* Displays without exclusions retain their normal insets. */ }
        }
        this._refreshDisplayGeometry();
        this._liveWidgets = widgets === null ? new HomeWidgets(this) : null;
        this._widgetHosts = new Map((widgets ?? this._liveWidgets.definitions()).map(widget => [widget.id, widget]));
        this._appSystem = Shell.AppSystem.get_default();
        this._clock = new GnomeDesktop.WallClock({time_only: true});
        this._proxy = null;
        this._home = new St.Widget({name: 'lumaConvergedHome', reactive: true, can_focus: true,
            style_class: 'luma-converged-home', visible: false});
        // Home is shell chrome, like Overview, not a compositor window actor.
        // Its explicit visibility yields to apps; wallpaper/window restacking
        // never owns or covers it. Existing menus remain above this layer.
        Main.layoutManager.addChrome(this._home, {affectsStruts: false, trackFullscreen: false});
        Main.uiGroup.set_child_above_sibling(this._home, global.window_group);
        this._status = new St.BoxLayout({style_class: 'luma-converged-status',
            reactive: true, visible: false});
        this._time = new St.Label({text: this._clock.clock.trim(),
            x_expand: true, x_align: Clutter.ActorAlign.START,
            y_align: Clutter.ActorAlign.CENTER});
        const clockButton = button(_('Calendar'), () =>
            Main.panel.statusArea.dateMenu.menu.toggle(), 'luma-converged-clock');
        clockButton.x_expand = false;
        clockButton.set_child(this._time);
        this._status.add_child(clockButton);
        this._clockButton = clockButton;
        this._status.add_child(new St.Widget({x_expand: true,
            style: 'background-color: transparent; border: 0; box-shadow: none;'}));
        this._statusRight = new St.BoxLayout({reactive: true, y_align: Clutter.ActorAlign.FILL});
        this._status.add_child(this._statusRight);
        Main.layoutManager.addChrome(this._status, {affectsStruts: false, trackFullscreen: false});
        this._connect(this._status, 'notify::visible', () => this._syncStatusCompositing());
        const settingsPan = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1,
            pan_axis: Clutter.PanAxis.Y});
        settingsPan.set_begin_threshold(4);
        settingsPan.connect('may-recognize', () => this.mode !== 'desktop' && !Main.sessionMode.isLocked);
        settingsPan.connect('recognize', () => {
            if (this.mode === 'mobile') this._handheldOptions.beginPull();
        });
        settingsPan.connect('pan-update', gesture => {
            const delta = gesture.get_accumulated_delta_abs().get_y();
            if (this.mode === 'mobile') this._handheldOptions.updatePull(delta);
            else if (delta > 24 * (this._scale ?? 1)) Main.panel.statusArea.quickSettings.menu.open();
        });
        settingsPan.connect('end', () => this._handheldOptions.endPull());
        settingsPan.connect('cancel', () => this._handheldOptions.endPull(true));
        this._statusRight.add_action_full('LumaQuickOptions', Clutter.EventPhase.CAPTURE, settingsPan);
        const settingsTap = new Clutter.ClickGesture();
        settingsTap.connect('may-recognize', gesture => !Main.sessionMode.isLocked &&
            global.stage.get_event_actor(gesture.get_point_event(0)) === this._statusRight);
        settingsTap.connect('recognize', () => Main.panel.statusArea.quickSettings.menu.toggle());
        this._statusRight.add_action(settingsTap);
        this._editor = new HomeEditor(this);
        this._gestureBar = button(_('Home'), () => this.showHome(), 'luma-converged-home-control');
        this._gestureBar.set_child(new St.Widget({style_class: 'luma-converged-home-pill',
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER}));
        Main.layoutManager.addChrome(this._gestureBar, {affectsStruts: false, trackFullscreen: false});
        this._gestureBar.hide();
        this._tasks = new TabletTasks(this);
        this._gridDrag = new SwitcherDrag(this);
        this._pageSwipe = new SwipeTracker(this._home, Clutter.Orientation.HORIZONTAL,
            Shell.ActionMode.NORMAL | Shell.ActionMode.OVERVIEW, {
                allowDrag: false, allowScroll: true, fingerCount: 1,
                exactFingerCount: true, phase: Clutter.EventPhase.CAPTURE,
                name: 'LumaHomePages',
            });
        this._pageSwipe.enabled = false;
        this._connect(this._pageSwipe, 'begin', tracker => {
            if (!this._layout || this._layout.pages < 2) return;
            this._strip.remove_all_transitions();
            const progress = this._paintedPage();
            this._syncPageViews(progress);
            tracker.confirmSwipe(this._home.width,
                this._pageViews.map((_page, index) => index), progress, Math.round(progress));
        });
        this._connect(this._pageSwipe, 'update', (_tracker, progress) => {
            this._strip.translation_x = this._pageOffset(progress);
        });
        this._connect(this._pageSwipe, 'end', (_tracker, duration, page) => {
            this._settlePage(Math.round(page), duration);
        });
        const homePan = new Clutter.PanGesture({min_n_points: 1, max_n_points: 1,
            pan_axis: Clutter.PanAxis.Y});
        homePan.set_begin_threshold(4);
        homePan.connect('may-recognize', () => this.mode !== 'desktop' &&
            !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter);
        homePan.connect('recognize', gesture => {
            this._homePanDistance = 0;
            this._tasks.beginNavigation(this._gestureBar, gesture);
            this._tasks.beginLift();
        });
        homePan.connect('pan-update', gesture => {
            this._homePanDistance += gesture.get_delta_abs().get_y();
            this._tasks.verticalUpdate(this._homePanDistance);
        });
        homePan.connect('end', () => {
            this._tasks.endNavigation();
            this._tasks.verticalEnd(this._homePanDistance);
        });
        homePan.connect('cancel', () => {
            this._tasks.endNavigation();
            if (this._tasks._hold) GLib.source_remove(this._tasks._hold);
            this._tasks._hold = 0;
            this._tasks._held = false;
            this._tasks.finishLift(false);
        });
        this._gestureBar.add_action_full('LumaHome', Clutter.EventPhase.CAPTURE, homePan);
        this._connect(this._home, 'notify::visible', () => {
            this._pageSwipe.enabled = this._home.visible && (this._layout?.pages ?? 1) > 1;
            this._updateStatusStyle();
            this._editor.syncVisibility();
            this._tasks?.sync();
            if (!this._home.visible) this._editor.setEditing(false);
        });
        this._connect(this._settings, 'changed::surface-treatment', () => this._updateStatusStyle());
        this._connect(this._settings, 'changed::reduce-transparency', () => this._syncAccessibilityStyles());
        this._connect(St.Settings.get(), 'notify::high-contrast', () => this._syncAccessibilityStyles());
        this._connect(this._clock, 'notify::clock', () => {
            this._time.text = this._clock.clock.trim();
        });
        this._connect(this._appSystem, 'installed-changed', () => this._rebuild());
        this._connect(Gio.AppInfoMonitor.get(), 'changed', () => this._queueRebuild());
        this._connect(Main.layoutManager, 'monitors-changed', () => {
            this._editor.cancel();
            this._refreshDisplayGeometry();
            this._tasks.layout();
            this._queueRebuild();
        });
        const themeContext = St.ThemeContext.get_for_stage(global.stage);
        this._connect(themeContext, 'notify::scale-factor', () => this._queueRebuild());
        this._connect(themeContext, 'notify::font', () => this._queueRebuild());
        this._connect(global.display, 'window-created', (_display, window) => {
            if (this.mode !== 'desktop') {
                GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                    if (this._destroyed || this.mode === 'desktop' || Main.sessionMode.isLocked ||
                        Main.sessionMode.isGreeter || !window.get_compositor_private())
                        return GLib.SOURCE_REMOVE;
                    this._manage(window);
                    return GLib.SOURCE_REMOVE;
                });
            }
        });
        this._connect(global.window_manager, 'map', (_manager, actor) => {
            if (this.mode !== 'desktop' && !Main.sessionMode.isLocked &&
                this._manage(actor.meta_window)) {
                this._home.hide();
                this._tasks.focus(actor.meta_window);
                this._setStatusWindow(actor.meta_window);
            }
        });
        this._connect(global.display, 'notify::focus-window', () => {
            const window = global.display.focus_window;
            if (window && window.get_window_type() === Meta.WindowType.NORMAL && !window.is_skip_taskbar()) {
                this._lastFocusedWindow = window;
                if (this.mode !== 'desktop' && !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter) {
                    this._home.hide();
                    this._tasks.focus(window);
                    this._setStatusWindow(window);
                }
            }
        });
        this._connect(Main.sessionMode, 'updated', () => {
            this._syncVisibility();
            this._queueVisibilitySync();
        });
        this._connect(Main.screenShield, 'locked-changed', () => this._queueVisibilitySync());
        this._connect(Main.screenShield, 'active-changed', () => this._queueVisibilitySync());
        this._connect(Main.overview, 'showing', () => {
            if (this.mode !== 'desktop') {
                Main.overview.hide();
                this.showHome();
            }
        });
        this._connect(this._home, 'key-press-event', (_actor, event) => {
            if (this._editor.keyboard(event)) return Clutter.EVENT_STOP;
            if (event.get_key_symbol() === Clutter.KEY_Page_Down) this._changePage(1);
            else if (event.get_key_symbol() === Clutter.KEY_Page_Up) this._changePage(-1);
            else return Clutter.EVENT_PROPAGATE;
            return Clutter.EVENT_STOP;
        });
        const seat = Clutter.get_default_backend().get_default_seat();
        if (Number.isInteger(seat.tablet_mode_switch_state))
            this._connect(seat, 'notify::tablet-mode-switch-state', () => this._reportInput());
        this._connect(seat, 'notify::touch-mode', () => this._reportInput());
        this._connect(seat, 'device-added', () => this._reportInput());
        this._connect(seat, 'device-removed', () => this._reportInput());
        this._addModeOptions();
        this._handheldOptions = new HandheldOptions(this);
        this._syncAccessibilityStyles();
        Gio.DBusProxy.new_for_bus(Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_LOAD_PROPERTIES, null, BUS, PATH, BUS,
            this._cancellable, (_source, result) => {
                try {
                    const proxy = Gio.DBusProxy.new_for_bus_finish(result);
                    if (this._destroyed) return;
                    this._proxy = proxy;
                    this._connect(proxy, 'g-signal', (_p, _sender, signal, parameters) => {
                        if (signal === 'Changed')
                            this._snapshot(JSON.parse(parameters.deep_unpack()[1]));
                    });
                    this._connect(proxy, 'notify::g-name-owner', () => this._refresh());
                    this._refresh();
                } catch (error) {
                    if (!this._destroyed) console.warn(`Presentation state unavailable: ${error.message}`);
                }
            });
    }

    _connect(object, signal, callback) {
        this._signals.push([object, object.connect(signal, callback)]);
    }

    _call(method, parameters, callback = null, onError = null) {
        if (!this._proxy || this._destroyed) return;
        this._proxy.call(method, parameters, Gio.DBusCallFlags.NONE, 3000, this._cancellable,
            (proxy, result) => {
                try {
                    const value = proxy.call_finish(result);
                    if (!this._destroyed) callback?.(value);
                }
                catch (error) {
                    console.warn(`Presentation ${method}: ${error.message}`);
                    if (!this._destroyed) onError?.(error);
                }
            });
    }

    _refresh() {
        const ready = Boolean(this._proxy?.g_name_owner);
        for (const option of this._modeButtons.values()) {
            option.reactive = ready;
            option.can_focus = ready;
        }
        if (!ready)
            return;
        this._call('GetSnapshot', null, result => {
            this._snapshot(JSON.parse(result.deep_unpack()[0]));
            this._reportInput();
        });
    }

    _snapshot(snapshot) {
        const favoritesChanged = JSON.stringify(snapshot.favorites) !== JSON.stringify(this._state?.favorites);
        const homeChanged = JSON.stringify(snapshot.home_layouts) !== JSON.stringify(this._state?.home_layouts);
        this._state = snapshot;
        this._updateStatusStyle();
        const mode = snapshot.presentation?.effective ?? 'desktop';
        const selected = snapshot.presentation?.selected ?? 'automatic';
        for (const [choice, option] of this._modeButtons) {
            option.checked = selected === choice;
            option.setOrnament(option.checked ? PopupMenu.Ornament.DOT : PopupMenu.Ornament.NONE);
        }
        this._options.iconName = {desktop: 'luma-monitor-symbolic', tablet: 'luma-tablet-symbolic',
            mobile: 'luma-smartphone-symbolic'}[mode] ?? 'luma-monitor-symbolic';
        this._options.menu.setHeader(this._options.iconName, _('Experience'));
        this._options.title = this._modeButtons.get(mode)?.label.text ?? mode;
        this._options.subtitle = null;
        if (this.mode === mode) {
            if ((favoritesChanged || homeChanged) && mode !== 'desktop') this._queueRebuild();
            return;
        }
        const previous = this.mode;
        this._editor.setEditing(false);
        this.mode = mode;
        this._gridDrag.setMode();
        if (mode === 'desktop') {
            // Return borrowed status actors before measuring/showing the Dash.
            this._leaveHandheld();
            Main.shelf?.setPresentationMode?.(mode);
        } else {
            Main.shelf?.setPresentationMode?.(mode);
            if (previous === 'desktop')
                this._enterHandheld();
            this._rebuild();
            this._syncVisibility();
        }
        this._tasks.setMode();
    }

    _refreshDisplayGeometry() {
        if (!this._displayGeometry) return;
        Gio.DBus.session.call('org.gnome.Mutter.DisplayConfig', '/org/gnome/Mutter/DisplayConfig',
            'org.gnome.Mutter.DisplayConfig', 'GetCurrentState', null, null,
            Gio.DBusCallFlags.NONE, 3000, this._cancellable, (bus, result) => {
                try {
                    const state = bus.call_finish(result).deep_unpack();
                    if (this._destroyed) return;
                    const monitor = Main.layoutManager.primaryMonitor;
                    const logical = state[2].find(entry => entry[0] === monitor?.x && entry[1] === monitor?.y);
                    const connector = logical?.[5]?.[0]?.[0];
                    const native = state[1].find(entry => entry[0][0] === connector);
                    const builtIn = native?.[2]?.['is-builtin']?.deep_unpack?.() ?? false;
                    if (!builtIn || (this._displayGeometry.connector && connector !== this._displayGeometry.connector))
                        this._displayGeometry = null;
                    else if (logical) this._displayGeometry.transform = logical[3];
                    this._queueRebuild();
                } catch (error) {
                    if (!this._destroyed) console.warn(`Display exclusion transform unavailable: ${error.message}`);
                }
            });
    }

    _reportInput() {
        const seat = Clutter.get_default_backend().get_default_seat();
        const devices = seat.list_devices();
        const touch = devices.some(d => d.get_device_type() === Clutter.InputDeviceType.TOUCHSCREEN_DEVICE);
        // Only the native hardware switch is posture evidence. Touch-mode may
        // merely mean that no mouse is present; older compositors leave this
        // unknown and retain the device profile instead of guessing.
        const hardwareState = seat.tablet_mode_switch_state;
        this._call('ReportInputCapabilities', new GLib.Variant('(s)', [JSON.stringify({
            touchscreen: touch, tablet_mode: hardwareState === 1 ? true : hardwareState === 0 ? false : null,
            keyboard: devices.some(d => d.get_device_type() === Clutter.InputDeviceType.KEYBOARD_DEVICE),
            pointer: devices.some(d => d.get_device_type() === Clutter.InputDeviceType.POINTER_DEVICE),
        })]));
    }

    _addModeOptions() {
        const menu = Main.panel.statusArea.quickSettings.menu;
        this._options = new QuickMenuToggle({title: _('Mode'),
            iconName: 'computer-symbolic', toggleMode: false, x_expand: true,
            menuButtonAccessibleName: _('Choose experience')});
        this._options.add_style_class_name('luma-options-utility');
        this._options.setPill(true);
        this._options.checked = true;
        this._options.menu.setHeader('computer-symbolic', _('Experience'));
        this._options.menu._studioTitle = _('Experience');
        this._options.connect('clicked', () => this._options.menu.open());
        this._modeButtons = new Map();
        for (const [mode, label] of [['automatic', _('Automatic')], ['desktop', _('Desktop')],
            ['tablet', _('Tablet')], ['mobile', _('Mobile')]]) {
            const option = new PopupMenu.PopupMenuItem(label);
            option.reactive = false;
            option.can_focus = false;
            option.accessible_role = Atk.Role.RADIO_MENU_ITEM;
            option.connect('activate', () => {
                this._call('SetPresentation', new GLib.Variant('(s)', [mode]), () => this._refresh());
                menu.close();
            });
            this._options.menu.addMenuItem(option);
            this._modeButtons.set(mode, option);
        }
        const install = () => {
            if (!menu._mainPage || this._optionsInstalled) return;
            // Register through the same native detail-page path as Wi-Fi/Bluetooth.
            menu.addItem(this._options, 2);
            if (menu._surfaceLayout) {
                menu._surfaceLayout.setModePicker(this._options);
                this._optionsInstalled = true;
                this._handheldOptions?.setEnabled(this.mode !== 'desktop');
                return;
            }
            this._options.get_parent().remove_child(this._options);
            this._modeUtilityRow = new St.BoxLayout({style_class: 'luma-options-toggle-row luma-experience-row',
                x_expand: true});
            this._modeUtilityRow.layout_manager.homogeneous = true;
            this._modeUtilityRow.add_child(this._options);
            const utilities = menu._studioUtilities;
            utilities.add_child(this._modeUtilityRow);
            const pair = () => {
                if (this._modeTilingRow) return;
                const row = utilities.get_children().find(child => child !== this._modeUtilityRow &&
                    child.get_children().some(item => item.constructor.name === 'TilingToggle'));
                if (!row) return;
                const index = utilities.get_children().indexOf(row);
                utilities.remove_child(row);
                this._modeTilingRow = row;
                // Keep the native wrapper: its destruction callback owns Tiling.
                // Experience survives provider removal and late provider creation.
                this._modeUtilityRow.insert_child_at_index(row, 0);
                row.x_expand = true;
                utilities.set_child_at_index(this._modeUtilityRow, index);
                this._connect(row, 'destroy', () => {
                    this._modeTilingRow = null;
                    this._signals = this._signals.filter(([object]) => object !== row);
                });
            };
            this._connect(utilities, 'child-added', pair);
            pair();
            this._options._menuButton.child.child.icon_name = 'luma-chevron-right-symbolic';
            this._optionsInstalled = true;
            this._handheldOptions?.setEnabled(this.mode !== 'desktop');
        };
        if (menu._mainPage) install();
        else {
            // Native radio providers initialize asynchronously. Follow creation
            // of their menu page instead of assuming startup-complete means ready.
            this._connect(menu.box, 'child-added', () => {
                if (this._optionsInstallIdle || this._optionsInstalled) return;
                this._optionsInstallIdle = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                    this._optionsInstallIdle = 0;
                    if (!this._destroyed) install();
                    return GLib.SOURCE_REMOVE;
                });
            });
        }
    }

    _enterHandheld() {
        Main.shelf?._restorePanelActors();
        Main.shelf?._trackWorkArea(false);
        Main.layoutManager.setChromeHidden(Main.shelf, true);
        this._loanStatus();
        Main.layoutManager.panelBox.hide();
        Main.panel.hide();
        Main.layoutManager.setChromeHidden(Main.layoutManager.panelBox, true);
        for (const actor of global.get_window_actors())
            this._manage(actor.meta_window);
        const focused = [global.display.focus_window?.get_transient_for() ?? global.display.focus_window,
            this._lastFocusedWindow, ...this._windows.keys()].find(window =>
            window?.get_compositor_private() && !window.minimized &&
            window.showing_on_its_workspace() && this._windows.has(window));
        if (focused) {
            this._home.hide();
            this._setStatusWindow(focused);
            Main.activateWindow(focused);
        } else {
            this._home.show();
            this._home.grab_key_focus();
        }
    }

    _setHandheldQuickOptions(enabled) {
        this._handheldOptions?.setEnabled(enabled);
    }

    _loanStatus() {
        if (this._statusLoans?.size) return;
        this._statusLoans = new Map();
        this._panelChildren = Main.panel._rightBox.get_children();
        for (const actor of this._panelChildren) {
            this._statusLoans.set(actor, {x_expand: actor.x_expand,
                x_align: actor.x_align, clip_to_allocation: actor.clip_to_allocation});
            move(actor, this._statusRight);
            actor.set_size(-1, -1);
            actor.x_expand = false;
            actor.x_align = Clutter.ActorAlign.END;
            actor.clip_to_allocation = true;
        }
        Main.panel.statusArea.quickSettings.set_size(-1, -1);
    }

    _releaseStatus() {
        for (const [actor, properties] of this._statusLoans ?? []) {
            if (actor.get_parent() === this._statusRight)
                move(actor, Main.panel._rightBox);
            Object.assign(actor, properties);
            actor.set_size(-1, -1);
        }
        this._statusLoans?.clear();
    }

    _leaveHandheld() {
        this._setHandheldQuickOptions(false);
        this._setStatusWindow(null);
        this._home.hide();
        this._status.hide();
        this._syncStatusCompositing();
        this._gestureBar.hide();
        this._releaseStatus();
        this._restoreMobileLockPanel();
        for (const [window, saved] of this._windows) {
            if (!window.get_compositor_private()) continue;
            window.disconnect(saved.unmanagedId);
            window.disconnect(saved.identityId);
            if (!saved.fullscreen) window.unmake_fullscreen();
            if (!saved.maximized) window.unmaximize();
            if (!saved.fullscreen && !saved.maximized)
                window.move_resize_frame(false, saved.rect.x, saved.rect.y,
                    saved.rect.width, saved.rect.height);
        }
        this._windows.clear();
        for (const window of this._homeMinimized) {
            if (window.get_compositor_private()) window.unminimize();
        }
        this._homeMinimized.clear();
        Main.layoutManager.setChromeHidden(Main.layoutManager.panelBox, false);
        Main.shelf?._sync();
    }

    _manage(window) {
        if (window.get_window_type() !== Meta.WindowType.NORMAL || window.is_override_redirect() ||
            window.is_skip_taskbar())
            return false;
        if (!this._windows.has(window)) {
            this._windows.set(window, {fullscreen: window.is_fullscreen(),
                maximized: window.get_maximize_flags(), rect: window.get_frame_rect()});
            const unmanagedId = window.connect('unmanaged', () => {
                window.disconnect(this._windows.get(window).identityId);
                this._tasks.unmanaged(window);
                this._windows.delete(window);
                this._homeMinimized.delete(window);
                GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
                    if (!this._destroyed && !this._tasks?._switcherPopup && this.mode !== 'desktop' && !Main.sessionMode.isLocked &&
                        !global.get_window_actors().some(actor => actor.meta_window.get_window_type() ===
                            Meta.WindowType.NORMAL && !actor.meta_window.minimized &&
                            actor.meta_window.showing_on_its_workspace()))
                        this._home.show();
                    return GLib.SOURCE_REMOVE;
                });
            });
            this._windows.get(window).unmanagedId = unmanagedId;
            this._windows.get(window).identityId = window.connect('notify::gtk-application-id', () => {
                if (this._tasks.pending) this._tasks.manage(window);
            });
        }
        if (!this._tasks.manage(window)) window.make_fullscreen();
        return true;
    }

    _syncStatusCompositing() {
        // Fullscreen scanout can bypass shell chrome on the physical output
        // even when a compositor screenshot includes it. Like notifications,
        // the persistent status overlay owns one balanced compositing hold.
        const visible = this._status.visible && this.mode !== 'desktop' &&
            !this._destroyed && !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter;
        if (visible === Boolean(this._statusComposited)) return;
        if (visible) global.compositor.disable_unredirect();
        else global.compositor.enable_unredirect();
        this._statusComposited = visible;
    }

    _restoreMobileLockPanel() {
        if (!this._mobileLockPanel) return;
        Main.panel.visible = this._mobileLockPanel.visible;
        this._mobileLockPanel = null;
    }

    _queueVisibilitySync() {
        if (this._destroyed || this._visibilitySource) return;
        // Session-mode listeners and the screen shield finish their transition
        // in the same main-loop turn. Restore borrowed status actors afterwards.
        this._visibilitySource = GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE, () => {
            this._visibilitySource = 0;
            if (!this._destroyed) this._syncVisibility();
            return GLib.SOURCE_REMOVE;
        });
    }

    _syncVisibility() {
        const unlocked = !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter;
        const mobileLock = this.mode === 'mobile' && Main.sessionMode.isLocked && !Main.sessionMode.isGreeter;
        if (!mobileLock) this._restoreMobileLockPanel();
        else {
            this._mobileLockPanel ??= {visible: Main.panel.visible};
            Main.panel.hide();
        }
        if (!unlocked && !this._lockSnapshot && this.mode !== 'desktop') {
            this._lockSnapshot = {home: this._home.visible,
                minimized: [...this._windows.keys()].filter(window => window.minimized)};
        }
        if (unlocked && this._lockSnapshot) {
            const saved = this._lockSnapshot;
            this._lockSnapshot = null;
            if (this.mode !== 'desktop') {
                // Mapping is deliberately ignored behind the lock screen.
                // Adopt those clients now, before restoring the visible Home.
                for (const actor of global.get_window_actors()) {
                    const window = actor.meta_window;
                    if (!this._windows.has(window) && this._manage(window) && saved.home) {
                        window.minimize();
                        this._homeMinimized.add(window);
                    }
                }
                for (const window of saved.minimized)
                    if (window.get_compositor_private()) window.minimize();
                if (saved.home) { this._home.show(); this._home.grab_key_focus(); }
            }
        }
        if (this.mode !== 'desktop') {
            if (unlocked) this._loanStatus();
            else this._releaseStatus();
        }
        this._status.visible = this.mode !== 'desktop' && unlocked;
        this._syncGestureVisibility();
        if (!unlocked) { this._home.hide(); this._handheldOptions.close(); }
        if (!unlocked) this._editor.setEditing(false);
        this._editor.syncVisibility();
        this._tasks.setMode();
    }

    _syncGestureVisibility() {
        this._gestureBar.visible = !this._destroyed && this.mode !== 'desktop' &&
            !Main.sessionMode.isLocked && !Main.sessionMode.isGreeter &&
            !Main.panel.statusArea.quickSettings.menu.isOpen && !peekNotificationLip()?.isOpen;
    }

    _setStatusWindow(window) {
        window = window?.get_transient_for() ?? window;
        if (window === this._statusWindow) {
            this._updateStatusStyle();
            return;
        }
        for (const [object, id] of this._statusWindowSignals) object.disconnect(id);
        this._statusWindowSignals = [];
        this._statusWindow = window;
        const bind = () => {
            for (const [object, id] of this._statusActionSignals) object.disconnect(id);
            this._statusActionSignals = [];
            this._statusActions = null;
            const name = window?.get_gtk_unique_bus_name();
            const path = window?.get_gtk_window_object_path();
            if (name && path) {
                const actions = Gio.DBusActionGroup.get(Gio.DBus.session, name, path);
                this._statusActions = actions;
                for (const signal of ['action-added', 'action-removed', 'action-state-changed'])
                    this._statusActionSignals.push([actions, actions.connect(signal, () => this._updateStatusStyle())]);
            }
            this._updateStatusStyle();
        };
        if (window) {
            for (const signal of ['notify::gtk-unique-bus-name', 'notify::gtk-window-object-path'])
                this._statusWindowSignals.push([window, window.connect(signal, bind)]);
            this._statusWindowSignals.push([window, window.connect('unmanaged', () => this._setStatusWindow(null))]);
        }
        bind();
    }

    _updateStatusStyle() {
        if (!this._status) return;
        const hint = this._statusActions?.get_action_state('luma-status-ink')?.deep_unpack();
        const treatment = this._settings.get_user_value('surface-treatment')?.deep_unpack();
        const darkSurface = treatment ? treatment === 'dark' : this._state?.appearance === 'prefer-dark';
        this._statusInk = this._home.visible ? 'light' :
            ['light', 'dark'].includes(hint) ? hint : darkSurface ? 'light' : 'dark';
        this._status.set_style(`padding: 0 ${this._safeStatus?.right ?? this._statusInset ?? 24}px 0 ${this._safeStatus?.left ?? this._statusInset ?? 24}px;` +
            `color: ${this._statusInk === 'light' ? '#ffffff' : '#191b1f'};`);
        this._tasks?.updateInk();
    }

    _syncAccessibilityStyles() {
        const highContrast = St.Settings.get().high_contrast;
        const opaque = highContrast || this._settings.get_boolean('reduce-transparency');
        for (const actor of [this._home, this._options, peekNotificationLip()].filter(Boolean)) {
            if (highContrast) actor.add_style_class_name('high-contrast');
            else actor.remove_style_class_name('high-contrast');
        }
        if (opaque) this._home.add_style_class_name('opaque');
        else this._home.remove_style_class_name('opaque');
        this._handheldOptions?.syncAccessibility();
        this._queueRebuild();
    }

    showHome() {
        if (this.mode === 'desktop' || Main.sessionMode.isLocked || Main.sessionMode.isGreeter)
            return;
        this._syncVisibility();
        this._tasks.beginLift();
        this._tasks.beforeHome();
        // State, app and monitor signals keep Home current while hidden. Reuse
        // its actors on return instead of rebuilding every icon and widget on
        // the same frame as the navigation animation.
        const rect = this._tasks.homeRect() ?? Main.layoutManager.primaryMonitor;
        if (rect && (!this._layout || this._home.x !== rect.x || this._home.y !== rect.y ||
            this._home.width !== rect.width || this._home.height !== rect.height))
            this._rebuild();
        for (const actor of global.get_window_actors()) {
            const window = actor.meta_window;
            if (window.get_window_type() === Meta.WindowType.NORMAL && !window.minimized) {
                this._homeMinimized.add(window);
                Main.wm.skipNextEffect(actor);
                window.minimize();
            }
        }
        this._home.show();
        this._tasks.finishLift(true);
        this._home.grab_key_focus();
        this._tasks.sync();
    }

    _catalog() {
        return this._appSystem.get_installed().filter(info => {
            if (!(info instanceof GioUnix.DesktopAppInfo) || !info.should_show() || info.get_boolean('Terminal'))
                return false;
            const id = info.get_id();
            return info.get_string('Type') === 'Application' &&
                !/^(org\.gnome\.Shell\.Extensions|org\.gnome\.Settings\.|luma-search|search\.)/i.test(id) &&
                !/^search$/i.test(info.get_display_name().trim());
        }).sort((a, b) => a.get_display_name().localeCompare(b.get_display_name()));
    }

    _launch(info) {
        this._editor.setEditing(false);
        if (this._tasks.launchFromHome(info)) return;
        this._tasks.closeOverlay();
        const app = this._appSystem.lookup_app(info.get_id());
        try {
            if (app) app.activate();
            else info.launch([], global.create_app_launch_context(0, -1));
            this._home.hide();
        } catch (error) { Main.notifyError(_('Unable to open application'), error.message); }
    }

    _appButton(info, dock = false) {
        const result = button(info.get_display_name(), () => {
            if (this._editor.editing && !dock) this._editor.select(info.get_id());
            else this._launch(info);
        },
            dock ? 'luma-converged-dock-app' : 'luma-converged-app');
        const box = new St.BoxLayout({orientation: Clutter.Orientation.VERTICAL,
            x_align: Clutter.ActorAlign.CENTER, style_class: 'luma-converged-app-content'});
        box.add_child(new St.Icon({gicon: info.get_icon(), icon_size: this._homeIconSize ?? 60,
            x_align: Clutter.ActorAlign.CENTER}));
        if (!dock) {
            const caption = new St.Label({text: info.get_display_name(),
                style_class: 'luma-converged-app-label', x_align: Clutter.ActorAlign.CENTER});
            caption.clutter_text.ellipsize = Pango.EllipsizeMode.END;
            box.add_child(caption);
        }
        if (this._layout?.compact) {
            box.set_style('spacing: 3px;');
            box.x_align = Clutter.ActorAlign.FILL;
            for (const child of box.get_children()) {
                if (child instanceof St.Label) child.set_style('font-size: 10px;');
            }
            result.set_style('padding: 2px; min-width: 0; min-height: 0;');
            result.clip_to_allocation = true;
        }
        result.set_child(box);
        return result;
    }

    _queueRebuild() {
        if (this._rebuildSource) return;
        this._rebuildSource = GLib.timeout_add(GLib.PRIORITY_DEFAULT, 150, () => {
            if (this._editor.dragging) return GLib.SOURCE_CONTINUE;
            this._rebuildSource = 0;
            this._rebuild();
            return GLib.SOURCE_REMOVE;
        });
    }

    _rebuild() {
        if (this.mode === 'desktop') return;
        if (this._editor.dragging) { this._queueRebuild(); return; }
        const display = Main.layoutManager.primaryMonitor;
        if (!display) return;
        const monitor = this._tasks.homeRect() ?? display;
        const scale = St.ThemeContext.get_for_stage(global.stage).scale_factor;
        this._scale = scale;
        this._pageSwipe.enabled = false;
        this._editor.resetItems();
        this._dots = null;
        this._home.destroy_all_children();
        this._home.set_position(monitor.x, monitor.y);
        this._home.set_size(monitor.width, monitor.height);
        this._status.set_position(display.x, display.y);
        this._status.set_size(display.width, 44 * scale);
        const lip = peekNotificationLip();
        if (lip && lip !== this._gestureLip) {
            this._gestureLip = lip;
            this._connect(lip, 'notify::height', () => this._syncGestureVisibility());
        }
        if (lip?.get_parent() === Main.uiGroup) {
            Main.uiGroup.set_child_above_sibling(lip, this._status);
            lip._place();
        }
        this._setHandheldQuickOptions(true);
        this._gestureBar.set_position(display.x, display.y + display.height - 28 * scale);
        this._gestureBar.set_size(display.width, 28 * scale);
        this._apps = this._catalog();
        this._layout = homeLayout(monitor.width / scale, monitor.height / scale,
            this.mode, this._apps.length);
        this._homeIconSize = this._layout.iconSize ?? 60;
        if (this._layout.compact) this._home.add_style_class_name('compact-home');
        else this._home.remove_style_class_name('compact-home');
        let metrics = {};
        if (this._apps.length) {
            const probe = this._appButton(this._apps[0]);
            this._home.add_child(probe);
            metrics = {cellHeight: Math.ceil(probe.get_preferred_height(-1)[1] / scale),
                minimumCellWidth: Math.ceil(probe.get_preferred_width(-1)[0] / scale) + 16};
            probe.destroy();
        }
        this._statusInset = this.mode === 'tablet' ? tabletFrame.edge :
            homeLayout(display.width / scale, display.height / scale, this.mode, this._apps.length, metrics).inset;
        const layout = homeLayout(monitor.width / scale, monitor.height / scale,
            this.mode, this._apps.length, metrics);
        this._layout = layout;
        const safe = statusInsets(this._displayGeometry, display, 44 * scale,
            (layout.compact ? 12 : this._statusInset) * scale, 8 * scale);
        this._safeStatus = {left: safe.left / scale, right: safe.right / scale};
        this._status.set_style(`padding: 0 ${safe.right / scale}px 0 ${safe.left / scale}px;` +
            `color: ${this._statusInk === 'light' ? '#ffffff' : '#191b1f'};`);
        const definitions = [...this._widgetHosts.values()].map((widget, index) => ({id: widget.id,
            kind: 'widget', w: layout.compact ? (index % 2 ? layout.columns - layout.widgetColumns : layout.widgetColumns) : widget.columns ?? 2, h: widget.rows ?? 2}));
        definitions.push(...this._apps.map(info => ({id: info.get_id(), kind: 'app', w: layout.appColumns ?? 1, h: 1})));
        const columns = Math.min(32, layout.columns);
        const rows = Math.min(32, Math.max(1, layout.otherRows));
        layout.columns = columns;
        this._gridKey = `${this.mode}:${columns}x${rows}`;
        const layouts = this._state?.home_layouts ?? {};
        const saved = layouts[this._gridKey] ?? Object.entries(layouts)
            .filter(([key]) => key.startsWith(`${this.mode}:`))
            .sort((a, b) => Math.abs(a[1].columns - columns) + Math.abs(a[1].rows - rows) -
                Math.abs(b[1].columns - columns) - Math.abs(b[1].rows - rows))[0]?.[1];
        this._grid = reconcileGrid(saved, definitions, columns, rows);
        layout.pages = this._grid.pages.length;
        this._updateStatusStyle();
        this._page = Math.min(this._page, layout.pages - 1);
        const viewport = new St.Widget({clip_to_allocation: true,
            width: monitor.width, height: monitor.height - (layout.compact ? layout.bottom : 120) * scale});
        this._home.add_child(viewport);
        this._strip = new St.Widget({width: monitor.width * layout.pages,
            height: monitor.height - (layout.compact ? layout.bottom : 120) * scale});
        viewport.add_child(this._strip);
        this._pageViews = [];
        const cellWidth = layout.contentWidth / layout.columns;
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        for (let page = 0; page < layout.pages; page++) {
            const pageView = new St.Widget({width: monitor.width, height: monitor.height - (layout.compact ? layout.bottom : 120) * scale,
                visible: page === this._page});
            pageView.set_position((rtl ? layout.pages - 1 - page : page) * monitor.width, 0);
            this._pageViews.push(pageView);
            this._strip.add_child(pageView);
            const gridTop = layout.top;
            for (const record of this._grid.pages[page]) {
                const info = this._apps.find(app => app.get_id() === record.id);
                const host = this._widgetHosts.get(record.id);
                const item = record.kind === 'app' ? this._appButton(info) : host.createActor();
                item.clip_to_allocation = true;
                pageView.add_child(item);
                const inset = record.kind === 'widget' ? (layout.compact ? 3 : (host.gap ?? 12) / 2) : 0;
                item.set_position((layout.inset + (rtl ? layout.columns - record.x - record.w : record.x) * cellWidth + inset) * scale,
                    (gridTop + record.y * layout.rowHeight + inset) * scale);
                item.set_size((cellWidth * record.w - inset * 2) * scale,
                    (layout.rowHeight * record.h - 8 - inset * 2) * scale);
                if (!this._tasks.pickerSide) this._editor.attach(item, record, () => record.kind === 'app'
                    ? new St.Icon({gicon: info.get_icon(), icon_size: this._homeIconSize ?? 60})
                    : new Clutter.Clone({source: item}));
            }
        }
        this._pageVisibilityKey = null;
        this._strip.connect('notify::translation-x', () => this._syncPageViews(this._paintedPage()));
        this._strip.translation_x = this._pageOffset(this._page);
        this._syncPageViews(this._page);
        this._buildDock({...monitor, x: 0, y: 0});
        this._buildPageIndicators(monitor);
        this._pageSwipe.enabled = this._home.visible && layout.pages > 1;
        this._editor.setEditing(this._editor.editing);
        if (this._editor.picked)
            this._editor.items.get(this._editor.picked)?.actor.grab_key_focus();
        if (this._tasks.active) this._tasks.layout();
    }

    _buildPageIndicators(monitor) {
        this._dots?.destroy();
        this._dots = null;
        if (this._layout.pages > 1) {
            const dots = new St.BoxLayout({style_class: 'luma-converged-pages'});
            const spacing = 14;
            const count = Math.min(this._layout.pages,
                Math.max(1, Math.floor((monitor.width / this._scale - 24) / spacing)), 7);
            const start = Math.clamp(this._page - Math.floor(count / 2), 0, this._layout.pages - count);
            for (let index = start; index < start + count; index++) {
                const dot = button(_('Home page %d of %d').format(index + 1, this._layout.pages), () => {
                    this._settlePage(index, 180);
                }, `luma-converged-page${index === this._page ? ' selected' : ''}`);
                dot.set_child(new St.Widget({style_class: 'luma-converged-page-dot'}));
                dots.add_child(dot);
            }
            this._home.add_child(dots);
            const width = count * spacing * this._scale;
            const compact = this._layout.compact;
            const dotHeight = compact ? this._layout.dotsHeight : 44;
            dots.set_position((monitor.width - width) / 2, monitor.height -
                (compact ? this._layout.dockBottom + this._layout.dockHeight + dotHeight : 156) * this._scale);
            dots.set_size(width, dotHeight * this._scale);
            for (const child of dots.get_children())
                child.set_style(`width: ${spacing}px; height: ${dotHeight}px; padding: 0; min-width: 0;`);
            this._dots = dots;
        }
    }

    _saveHomeGrid(grid) {
        if (!this._proxy?.g_name_owner) throw new Error(_('Home layout service is unavailable'));
        this._call('SetHomeLayout', new GLib.Variant('(ss)', [this._gridKey, JSON.stringify(grid)]),
            () => this._refresh(), error => {
                Main.notifyError(_('Unable to save Home layout'), error.message);
                this._queueRebuild();
            });
    }

    _appendHomePage() {
        const monitor = {width: this._home.width, height: this._home.height};
        const view = new St.Widget({width: monitor.width, height: monitor.height - (this._layout.compact ? this._layout.bottom : 120) * this._scale});
        this._strip.add_child(view);
        this._pageViews.push(view);
        this._layout.pages = this._pageViews.length;
        this._strip.width = monitor.width * this._layout.pages;
        this._buildPageIndicators(monitor);
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        this._pageViews.forEach((page, index) =>
            page.set_position((rtl ? this._layout.pages - 1 - index : index) * monitor.width, 0));
    }

    _buildDock(monitor, host = this._home, draggable = false) {
        const scale = this._scale;
        const ids = this._state?.favorites ?? [];
        const available = Math.max(46, monitor.width / scale - 40);
        // Keep targets at least 44px on narrow devices. Extra favorites remain
        // available on Home; every visible tile has the same surrounding gap.
        const slots = Math.max(1, Math.min(5, Math.floor((available - 8) / 52)));
        const favorites = ids.map(id => this._apps.find(info => info.get_id() === id)).filter(Boolean).slice(0, slots - 1);
        const count = favorites.length + 1;
        const compact = this._layout?.compact;
        const gap = compact ? 6 : Math.min(14, Math.max(0, (available - 44 * count) / (count + 1)));
        const size = compact ? 44 : Math.min(60, Math.floor((available - gap * (count + 1)) / count));
        const width = count * size + (count + 1) * gap;
        const height = size + 2 * gap;
        const dock = new St.BoxLayout({style_class: 'luma-converged-dock',
            x_align: Clutter.ActorAlign.CENTER, y_align: Clutter.ActorAlign.CENTER,
            style: `padding: ${Math.max(0, gap - 1)}px; spacing: ${gap}px;`});
        for (const info of favorites) {
            const item = this._appButton(info, true);
            item.get_child().get_first_child().icon_size = compact ? this._homeIconSize : size;
            item.set_size(size * scale, size * scale);
            dock.add_child(item);
            if (draggable) this._tasks.attachDrag(item, info);
        }
        const search = button(_('Search'), () => Main.lumaSearch?.open(), 'luma-converged-dock-search');
        search.set_style(`width: ${size}px; height: ${size}px; border-radius: ${size * 16 / 60}px;`);
        search.set_size(size * scale, size * scale);
        search.set_child(new St.Icon({icon_name: 'system-search-symbolic', icon_size: Math.round(size * 28 / 60)}));
        dock.add_child(search);
        host.add_child(dock);
        dock.set_size(width * scale, height * scale);
        dock.set_position((monitor.width - width * scale) / 2, monitor.height - (height + (compact ? this._layout.dockBottom : 20)) * scale);
    }

    _changePage(delta) {
        this._settlePage(this._page + delta, 180);
    }

    _pageOffset(progress) {
        const rtl = Clutter.get_default_text_direction() === Clutter.TextDirection.RTL;
        return (rtl ? progress - (this._layout.pages - 1) : -progress) *
            this._home.width;
    }

    _paintedPage() {
        return pageProgress(this._strip.translation_x, this._home.width, this._layout.pages,
            Clutter.get_default_text_direction() === Clutter.TextDirection.RTL);
    }

    _syncPageViews(progress) {
        const [first, last] = visiblePageRange(progress, this._pageViews.length);
        const key = `${first}:${last}`;
        if (key === this._pageVisibilityKey) return;
        this._pageVisibilityKey = key;
        this._pageViews.forEach((view, index) => {view.visible = index >= first && index <= last;});
    }

    _settlePage(page, duration) {
        if (!this._strip || !this._layout) return;
        this._page = Math.clamp(page, 0, this._layout.pages - 1);
        this._syncPageViews(this._paintedPage());
        this._strip.ease({translation_x: this._pageOffset(this._page),
            duration: St.Settings.get().enable_animations ? duration : 0,
            mode: Clutter.AnimationMode.EASE_OUT_CUBIC,
            onComplete: () => {
                this._syncPageViews(this._page);
            }});
        this._buildPageIndicators({width: this._home.width, height: this._home.height});
    }

    destroy() {
        this._destroyed = true;
        this._liveWidgets?.destroy();
        this._powerButton.destroy();
        this._motion.destroy();
        this._cancellable.cancel();
        this.mode = 'desktop';
        Main.shelf?.setPresentationMode?.('desktop');
        this._leaveHandheld();
        this._handheldOptions.destroy();
        this._gridDrag.destroy();
        this._tasks.destroy();
        this._tasks = null;
        if (this._rebuildSource) GLib.source_remove(this._rebuildSource);
        if (this._visibilitySource) GLib.source_remove(this._visibilitySource);
        for (const [object, id] of this._signals) object.disconnect(id);
        this._editor.destroy();
        this._pageSwipe.destroy();
        if (this._optionsInstallIdle) GLib.source_remove(this._optionsInstallIdle);
        if (this._modeTilingRow) {
            const row = this._modeTilingRow;
            const utilities = Main.panel.statusArea.quickSettings.menu._studioUtilities;
            const index = utilities.get_children().indexOf(this._modeUtilityRow);
            this._modeUtilityRow.remove_child(row);
            utilities.insert_child_at_index(row, index);
        }
        this._options.menu.destroy();
        this._options.destroy();
        this._modeUtilityRow?.destroy();
        Main.layoutManager.removeChrome(this._status);
        Main.layoutManager.removeChrome(this._gestureBar);
        Main.layoutManager.removeChrome(this._home);
        this._status.destroy();
        this._gestureBar.destroy();
        this._home.destroy();
        this._clock.run_dispose();
        if (this._stylesheet)
            St.ThemeContext.get_for_stage(global.stage).get_theme().unload_stylesheet(this._stylesheet);
    }
}
