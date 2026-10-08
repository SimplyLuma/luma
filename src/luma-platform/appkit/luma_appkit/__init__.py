"""Public Python surface for the Luma Application Kit.

The package is intentionally small and declarative.  Applications consume the
same components on desktop, tablet, and handheld; presentation is selected at
runtime rather than by shipping a second application.
"""

from .commands import Command, CommandGroup, CommandRegistry
from .context import AppContext, InputMode, PresentationMode


__all__ = [
    "DialKey",
    "DocumentCheck",
    "DocumentBlockLayout",
    "snapshot_document_quote",
    "FolderPath", "TrailCrumb", "PresenceChip",
    "snapshot_document_caret",
    "snapshot_document_check",
    "AudioWaveform",
    "TransactionCard",
    "AppContext",
    "AppWindow",
    "NumericField",
    "AlignCluster",
    "AVATAR_TONES",
    "AVATAR_GLYPHS",
    "Avatar",
    "avatar_initials",
    "avatar_kind",
    "avatar_tone",
    "ConnectedButtonGroup",
    "ColorSwatch",
    "Command",
    "CommandGroup",
    "CommandRegistry",
    "command_menu_model",
    "command_menu_model", "command_popover", "point_rectangle",
    "EmptyState",
    "ListEmptyState",
    "FieldRow",
    "IconButton",
    "InlineRenameField",
    "InputMode",
    "Lightbox",
    "LightboxItem",
    "Island",
    "IslandSplitView",
    "IslandOverlaySplitView",
    "MobileHeader",
    "motion_duration",
    "NavigationRow",
    "NavigationSidebar",
    "NavigationTrail",
    "Place",
    "bind_navigation_input",
    "PresentationMode",
    "SaveChoice",
    "SaveResponse",
    "SaveSheet",
    "SaveStatus",
    "UnsavedDocument",
    "confirm_save",
    "ScrollView",
    "SectionLabel",
    "StatusBar",
    "Toolbar",
    "add_style_sheet",
    "add_style_builder",
    "centre_controls",
    "install_appkit",
    "evaluate_number",
    "Menu",
    "accelerator",
    "attach_context_menu",
    "command_actions",
    "command_menu",
    "modifier_text",
    # ── LumaUI (API level in lumaui.LUMAUI_API_LEVEL). One block per family;
    # F2 adds structure names, F3 action and content names, each in its own
    # block here and in __getattr__ below.
    "LUMAUI_API_LEVEL",
    "lumaui_icon",
    "install_lumaui",
    "hue_class",
    "hue_tint",
    "media_context",
    "in_media_context",
    "person_hue",
    "oklch_rgba",
    "reduced_motion",
    # LumaUI window: the frame LumaUI draws itself (F0, ADR-052)
    "WindowIdentity",
    "WindowControls",
    "toolkit_carries_luma_patches",
    # LumaUI structure
    "LayerHost",
    "AddRow",
    "DetailsItem",
    "DetailsFacts",
    "DetailsPane",
    "DetailsPhotos",
    "DetailsRow",
    "FactRow",
    "FilterHeading",
    "SidebarFoot",
    "SidebarToggle",
    "Column",
    "Selection",
    "ChoiceList",
    "SORT_DIRECTIONS",
    "TableHeader",
    "NavigationTrailBar",
    "CornerPill",
    "ModeSwitch",
    "MenuDrawer",
    # LumaUI v71 navigation (K-NAV)
    "TitleIsland",
    "ListFirst",
    "TabBar",
    "PageHeader",
    "WidthWatch",
    "TIERS",
    "tier",
    # LumaUI action
    "DestructiveDialog",
    "StackedButton",
    "StackedButtons",
    "IconOnlyButton",
    "Switch",
    "TextButton",
    "Toast",
    "ToastHost",
    "TOAST_KINDS",
    "ActionCenter",
    "ActionEditor",
    "AC_STATES",
    "BarAction",
    "BarWidget",
    "BarChip",
    "BarContext",
    "BarPrompt",
    "SEPARATOR", "RULE",
    "SPACER",
    # LumaUI action bar, v71 (K-BAR)
    "BarModes",
    "PanelRow",
    "PanelHeading",
    "PanelField",
    "PanelConfirm",
    "panel_list",
    "BarFrame",
    "bar_menu",
    "BarTile",
    "BarTiles",
    "SharePanel",
    "PanelSwitch",
    "PanelChoices",
    "PanelKey",
    "BarProgress",
    "SelectionBubble",
    # LumaUI content
    "CATEGORIES",
    "CategoryPill",
    "CountBadge",
    "count_text",
    "TYPE_ROLES",
    "TypeLabel",
    "apply_type",
    "type_class",
    "AccountCard",
    "Card",
    "ContentLitCard",
    "PersonAvatar",
    "ContentLitHeader",
    "CONTACT_ACTIONS",
    "ContactActions",
    "ContactCard",
    "EventCard",
    "MiniCard",
    "Person",
    "PlaceCard",
    "SongCard",
    "StatusPill",
    "STATUS_KINDS",
    "FileCard",
    "OpenButton",
    "OpenInMenu",
    "split_name",
    "PlaceResult",
    "PlaceSearch",
    "rank_places",
    "TextField",
    "HeroTitleField",
    "ParagraphField",
    "MessageBubble",
    # ── LumaUI bar (KB-B): items for the action center, sharing and export
    "BarEntry",
    "ENTRY_KINDS",
    "ENTRY_SPANS",
    "ShareSheet",
    "ShareSubject",
    "ShareResult",
    "installed_targets",
    "ShareTarget",
    "Collaborator",
    "SHARE_CHOICES",
    "BarSearch",
    "BarThumbnail",
    "BarReadout",
    "SplitAction",
    "BarMenu",
    "ZoomControl",
    "ZOOM_KINDS",
    "ToolPalette",
    "FileSummaryRow",
    "SubjectAction",
    "DocumentHeader",
    "ExportSheet",
    "ExportChoice",
    # ── end LumaUI bar
    # ── LumaUI rows (KB-A): navigation rows and sidebar variants, type, progress, people, marks, menus
    "RowAction",
    "RowLead",
    "SIDEBAR_VARIANTS",
    "SIDEBAR_WIDTHS",
    "SidebarRow",
    "SidebarSection",
    "append_section",
    "apply_sidebar_variant",
    "type_font",
    "type_metrics",
    "PROGRESS_SIZES",
    "PROGRESS_TONES",
    "ProgressLine",
    "AvatarStack",
    "GroupFace",
    "PresenceFace",
    "Favourite",
    "FavouritesStrip",
    "MARK_HUES",
    "Mark",
    "MarkButton",
    "MarkPicker",
    "MarkValue",
    "MenuSection",
    "RichMenuItem",
    "APP_ICON_SIZES",
    "AppIcon",
    "NoteCard",
    # v71 (K-ROWS): swipe rows, the A-Z index, message runs
    "SWIPE_COLORS",
    "SwipeAction",
    "SwipeRow",
    "AZ_LETTERS",
    "AZIndex",
    "MessageRun",
    "run_corners",
    "FILE_KINDS",
    "FILE_PLACES",
    "FileRequest",
    "ask_for_file",
    "file_dialog",
    "outcome_text",
    # ── end LumaUI rows
    # ── LumaUI media (KB-C: media, image editing, charts and measures)
    "MEDIA_KINDS",
    "WeatherGlyph",
    "WEATHER_CONDITIONS",
    "MediaGrid",
    "MediaItem",
    "MediaTile",
    "MediaCollectionCard",
    "MediaMiniPlayer",
    "ImageViewport",
    "MediaReadout",
    "MediaTransport",
    "MediaReadout",
    "TRANSPORT_VARIANTS",
    "Timeline",
    "AdjustmentGroup",
    "AdjustmentPanel",
    "CoverArt",
    "VoiceClip",
    "COVER_SHAPES",
    "COVER_SIZES",
    "SLIDER_LAYOUTS",
    "ValueRange",
    "ValueSlider",
    "WAVEFORM_KINDS",
    # ── end LumaUI media
    "ToolBar", "Tool", "Flyout", "TOOL_SEPARATOR", "Layer", "LayerTree",
    "PropertySection", "PropertyNumber", "PropertyColor", "PropertyChoice", "AlignmentActions",
    "CreativeWorkspace",
    "FloatingPanel",
]


# Both this set and __all__ enumerate names explicitly, so a widget that is
# added to one and not the other is either invisible to `from luma_appkit
# import *` or unimportable by name. tests/unit/test_luma_appkit_api.py asserts
# the two agree with what the module actually defines.
def __getattr__(name: str):
    if name == "DialKey":
        from .dial import DialKey
        return DialKey
    if name == "AudioWaveform":
        from .waveform import AudioWaveform
        return AudioWaveform
    if name in {"NavigationTrail", "Place", "bind_navigation_input"}:
        from . import navigation

        return getattr(navigation, name)
    if name in {"Lightbox", "LightboxItem"}:
        from . import lightbox
        return getattr(lightbox, name)
    if name in {"SaveChoice", "SaveResponse", "SaveSheet", "UnsavedDocument", "confirm_save"}:
        from . import save_sheet
        return getattr(save_sheet, name)
    if name in {"TransactionCard"}:
        from .transaction import TransactionCard
        return TransactionCard
    if name in {
        "AppWindow", "add_style_sheet", "add_style_builder", "command_menu_model", "evaluate_number", "NumericField", "AlignCluster", "AVATAR_TONES", "AVATAR_GLYPHS", "Avatar", "avatar_initials", "avatar_kind", "avatar_tone", "ConnectedButtonGroup",
        "ColorSwatch", "EmptyState", "ListEmptyState", "FieldRow", "IconButton",
        "InlineRenameField", "Island", "MobileHeader", "IslandSplitView", "IslandOverlaySplitView", "motion_duration",
        "NavigationRow", "NavigationSidebar", "SaveStatus", "ScrollView", "SectionLabel",
        "StatusBar", "Toolbar", "centre_controls", "install_appkit",
    }:
        from . import widgets

        return getattr(widgets, name)
    if name in {"Menu", "accelerator", "attach_context_menu", "command_actions", "command_menu",
                "command_popover", "modifier_text", "point_rectangle"}:
        from . import menus

        return getattr(menus, name)
    # ── LumaUI core
    if name in {"LUMAUI_API_LEVEL", "install_lumaui", "reduced_motion", "hue_class", "person_hue", "oklch_rgba",
                "media_context", "in_media_context", "hue_tint"}:
        from . import lumaui
        return getattr(lumaui, name)
    if name in {"lumaui_icon"}:
        from . import icons
        return getattr(icons, name)
    # ── LumaUI window
    if name in {"WindowIdentity", "WindowControls", "toolkit_carries_luma_patches"}:
        from . import window_frame
        return getattr(window_frame, name)
    # ── LumaUI structure
    if name in {"LayerHost"}:
        from . import structure_layers
        return getattr(structure_layers, name)
    if name in {"DetailsFacts", "AddRow", "DetailsItem", "DetailsPane", "DetailsPhotos", "DetailsRow", "FactRow"}:
        from . import structure_details
        return getattr(structure_details, name)
    if name in {"FilterHeading", "SidebarFoot", "SidebarToggle"}:
        from . import structure_sidebar
        return getattr(structure_sidebar, name)
    if name == "ChoiceList":
        from .choice_list import ChoiceList
        return ChoiceList
    if name in {"Column", "Selection", "SORT_DIRECTIONS", "TableHeader"}:
        from . import structure_table
        return getattr(structure_table, name)
    if name in {"NavigationTrailBar", "PageHeader"}:
        from . import structure_trail
        return getattr(structure_trail, name)
    if name in {"CornerPill", "ModeSwitch"}:
        from . import structure_placement
        return getattr(structure_placement, name)
    if name in {"MenuDrawer"}:
        from . import structure_drawer
        return getattr(structure_drawer, name)
    # ── LumaUI v71 navigation (K-NAV)
    if name in {"TitleIsland"}:
        from . import structure_island
        return getattr(structure_island, name)
    if name in {"TabBar"}:
        from . import structure_tabs
        return getattr(structure_tabs, name)
    if name in {"ListFirst"}:
        from . import structure_listfirst
        return getattr(structure_listfirst, name)
    if name in {"WidthWatch", "TIERS", "tier"}:
        from . import structure_adapt
        return getattr(structure_adapt, name)
    # ── LumaUI action
    if name in {"DestructiveDialog"}:
        from . import action_dialog
        return getattr(action_dialog, name)
    if name in {"TextButton", "Switch", "IconOnlyButton"}:
        from . import content_controls
        return getattr(content_controls, name)
    if name in {"StackedButton", "StackedButtons"}:
        from . import action_stack
        return getattr(action_stack, name)
    if name in {"Toast", "ToastHost", "TOAST_KINDS"}:
        from . import action_toast
        return getattr(action_toast, name)
    if name in {"ActionCenter", "ActionEditor", "AC_STATES", "BarAction", "BarChip", "BarContext", "BarPrompt", "BarModes", "BarWidget",
                "SEPARATOR", "RULE", "SPACER"}:
        from . import action_center
        return getattr(action_center, name)
    if name in {"SelectionBubble"}:
        from . import action_bubble
        return getattr(action_bubble, name)
    # ── LumaUI action bar, v71 (K-BAR)
    if name in {"PanelRow", "PanelHeading", "PanelField", "PanelConfirm", "panel_list", "BarTile", "BarTiles",
                "PanelSwitch", "PanelChoices", "PanelKey"}:
        from . import bar_panel
        return getattr(bar_panel, name)
    if name in {"BarFrame"}:
        from . import bar_frame
        return getattr(bar_frame, name)
    if name in {"bar_menu"}:
        from . import menus
        return getattr(menus, name)
    # ── LumaUI content
    if name in {"WeatherGlyph", "WEATHER_CONDITIONS"}:
        from . import content_weather
        return getattr(content_weather, name)
    if name in {"CATEGORIES", "CategoryPill", "CountBadge", "count_text"}:
        from . import content_badges
        return getattr(content_badges, name)
    if name in {"TYPE_ROLES", "TypeLabel", "apply_type", "type_class"}:
        from . import content_type
        return getattr(content_type, name)
    if name in {"AccountCard", "Card", "ContentLitCard", "ContentLitHeader", "PersonAvatar"}:
        from . import content_cards
        return getattr(content_cards, name)
    if name in {"CONTACT_ACTIONS", "ContactActions", "ContactCard", "EventCard", "MiniCard", "Person", "PlaceCard",
                "SongCard", "StatusPill", "STATUS_KINDS"}:
        from . import content_contact
        return getattr(content_contact, name)
    if name in {"FileCard", "OpenButton", "OpenInMenu", "split_name"}:
        from . import content_file
        return getattr(content_file, name)
    if name in {"PlaceResult", "PlaceSearch", "rank_places"}:
        from . import content_place
        return getattr(content_place, name)
    if name in {"TextField", "HeroTitleField", "ParagraphField"}:
        from . import content_field
        return getattr(content_field, name)
    if name in {"MessageBubble"}:
        from . import content_message
        return getattr(content_message, name)
    # ── LumaUI bar (KB-B)
    if name in {"BarEntry", "ENTRY_KINDS", "ENTRY_SPANS"}:
        from . import bar_entry
        return getattr(bar_entry, name)
    if name in {"ShareSheet", "ShareSubject", "ShareTarget", "ShareResult", "installed_targets", "Collaborator", "SHARE_CHOICES", "SharePanel"}:
        from . import bar_share
        return getattr(bar_share, name)
    if name in {"BarSearch", "BarThumbnail", "BarReadout", "SplitAction", "BarMenu", "ZoomControl", "ZOOM_KINDS",
                "BarProgress"}:
        from . import bar_items
        return getattr(bar_items, name)
    if name == "ToolPalette":
        from .bar_palette import ToolPalette
        return ToolPalette
    if name in {"FileSummaryRow", "SubjectAction"}:
        from . import bar_file
        return getattr(bar_file, name)
    if name in {"DocumentHeader", "ExportSheet", "ExportChoice"}:
        from . import bar_document
        return getattr(bar_document, name)
    # ── end LumaUI bar
    # ── LumaUI rows (KB-A)
    if name in {"RowAction", "RowLead", "SIDEBAR_VARIANTS", "SIDEBAR_WIDTHS", "SidebarRow", "SidebarSection",
                "append_section", "apply_sidebar_variant"}:
        from . import rows_navigation
        return getattr(rows_navigation, name)
    if name in {"type_font", "type_metrics"}:
        from . import rows_type
        return getattr(rows_type, name)
    if name in {"PROGRESS_SIZES", "PROGRESS_TONES", "ProgressLine"}:
        from . import rows_progress
        return getattr(rows_progress, name)
    if name in {"AvatarStack", "GroupFace", "PresenceFace"}:
        from . import rows_people
        return getattr(rows_people, name)
    if name in {"Favourite", "FavouritesStrip"}:
        from . import rows_favourites
        return getattr(rows_favourites, name)
    if name in {"MARK_HUES", "Mark", "MarkButton", "MarkPicker", "MarkValue"}:
        from . import rows_mark
        return getattr(rows_mark, name)
    if name in {"MenuSection", "RichMenuItem"}:
        from . import rows_menu
        return getattr(rows_menu, name)
    if name in {"APP_ICON_SIZES", "AppIcon", "NoteCard"}:
        from . import rows_identity
        return getattr(rows_identity, name)
    if name in {"SWIPE_COLORS", "SwipeAction", "SwipeRow"}:
        from . import rows_swipe
        return getattr(rows_swipe, name)
    if name in {"AZ_LETTERS", "AZIndex"}:
        from . import rows_index
        return getattr(rows_index, name)
    if name in {"MessageRun", "run_corners"}:
        from . import content_message
        return getattr(content_message, name)
    if name in {"FILE_KINDS", "FILE_PLACES", "FileRequest", "ask_for_file", "file_dialog", "outcome_text"}:
        from . import file_request
        return getattr(file_request, name)
    # ── end LumaUI rows
    # ── LumaUI media (KB-C)
    if name in {"MEDIA_KINDS", "MediaGrid", "MediaItem", "MediaTile"}:
        from . import media_grid
        return getattr(media_grid, name)
    if name == "MediaMiniPlayer":
        from .media_mini import MediaMiniPlayer
        return MediaMiniPlayer
    if name == "MediaCollectionCard":
        from .media_collection import MediaCollectionCard
        return MediaCollectionCard
    if name == "ImageViewport":
        from .media_viewport import ImageViewport
        return ImageViewport
    if name in {"MediaTransport", "MediaReadout", "TRANSPORT_VARIANTS"}:
        from . import media_transport
        return getattr(media_transport, name)
    if name == "Timeline":
        from . import media_timeline
        return media_timeline.Timeline
    if name in {"AdjustmentGroup", "AdjustmentPanel", "SLIDER_LAYOUTS", "ValueRange", "ValueSlider"}:
        from . import media_adjust
        return getattr(media_adjust, name)
    if name in {"CoverArt", "COVER_SHAPES", "COVER_SIZES"}:
        from . import media_cover
        return getattr(media_cover, name)
    if name in {"VoiceClip"}:
        from . import media_voice
        return getattr(media_voice, name)
    if name in {"WAVEFORM_KINDS"}:
        from . import waveform
        return getattr(waveform, name)
    # ── end LumaUI media
    if name in {"ToolBar", "Tool", "Flyout", "TOOL_SEPARATOR", "Layer", "LayerTree", "PropertySection", "PropertyNumber", "PropertyColor", "PropertyChoice", "AlignmentActions"}:
        from . import creative_parts
        return getattr(creative_parts, name)
    if name in {"CreativeWorkspace", "FloatingPanel"}:
        from . import creative_workspace
        return getattr(creative_workspace, name)
    if name == "snapshot_document_caret":
        from .document_caret import snapshot_document_caret
        return snapshot_document_caret
    if name in {"FolderPath", "TrailCrumb", "PresenceChip"}:
        from . import document_path
        return getattr(document_path, name)
    if name in {"DocumentBlockLayout", "snapshot_document_quote"}:
        from . import document_layout
        return getattr(document_layout, name)
    if name in {"DocumentCheck", "snapshot_document_check"}:
        from . import document_check
        return getattr(document_check, name)
    raise AttributeError(name)
