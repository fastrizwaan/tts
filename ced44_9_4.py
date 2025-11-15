#!/usr/bin/env python3
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GtkSource', '5')
from gi.repository import Gtk, Adw, Gio, GObject, Pango, GtkSource, GLib
import os
import json

class SettingsManager:
    """Manages application settings with persistence"""
    
    def __init__(self):
        self.config_dir = os.path.join(GLib.get_user_config_dir(), 'chrome-text-editor')
        self.config_file = os.path.join(self.config_dir, 'settings.json')
        
        # Ensure config directory exists
        os.makedirs(self.config_dir, exist_ok=True)
        
        # Default settings
        self.defaults = {
            'show_line_numbers': True,
            'tab_width': 4,
            'insert_spaces_instead_of_tabs': True,
            'highlight_current_line': True,
            'auto_indent': True,
            'wrap_text': False,
            'syntax_theme': 'Atom One Dark',
            'font_family': 'system',  # 'system' means use system monospace font
            'font_size': 0,  # 0 means use system default size
            'show_right_margin': False,
            'right_margin_position': 80,
            'highlight_matching_brackets': True,
            'draw_spaces': False,
            'draw_tabs': False
        }
        
        self.settings = self.load_settings()
        
    def load_settings(self):
        """Load settings from file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    loaded = json.load(f)
                    # Merge with defaults to handle new settings
                    settings = self.defaults.copy()
                    settings.update(loaded)
                    return settings
        except Exception as e:
            print(f"Error loading settings: {e}")
        
        return self.defaults.copy()
    
    def save_settings(self):
        """Save settings to file"""
        try:
            with open(self.config_file, 'w') as f:
                json.dump(self.settings, f, indent=2)
        except Exception as e:
            print(f"Error saving settings: {e}")
    
    def get(self, key):
        """Get setting value"""
        return self.settings.get(key, self.defaults.get(key))
    
    def set(self, key, value):
        """Set setting value"""
        self.settings[key] = value
        self.save_settings()

class ChromeTab(Gtk.Box):
    """A custom tab widget that behaves like Chrome tabs"""
   
    __gsignals__ = {
        'close-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'activate-requested': (GObject.SignalFlags.RUN_FIRST, None, ()),
    }
   
    def __init__(self, title="Untitled", closeable=True):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.set_hexpand(False)
        self.set_halign(Gtk.Align.START)
        self.add_css_class("chrome-tab")
       
        # Tab content container
        content_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        content_box.set_margin_start(8)
        content_box.set_margin_end(4)
        content_box.set_margin_top(4)
        content_box.set_margin_bottom(4)
       
        # Title label
        self.label = Gtk.Label()
        self.label.set_text(title)
        self.label.set_ellipsize(Pango.EllipsizeMode.END)
        self.label.set_single_line_mode(True)
        self.label.set_hexpand(False)
        content_box.append(self.label)
       
        # Close button
        if closeable:
            self.close_button = Gtk.Button()
            self.close_button.set_icon_name("window-close-symbolic")
            self.close_button.add_css_class("flat")
            self.close_button.add_css_class("circular")
            self.close_button.set_size_request(20, 20)
            self.close_button.connect('clicked', self._on_close_clicked)
            content_box.append(self.close_button)
       
        self.append(content_box)
       
        # Make the entire tab clickable
        click_gesture = Gtk.GestureClick()
        click_gesture.connect('pressed', self._on_tab_clicked)
        self.add_controller(click_gesture)
       
        self._is_active = False
        self._original_title = title
       
    def _on_close_clicked(self, button):
        self.emit('close-requested')
       
    def _on_tab_clicked(self, gesture, n_press, x, y):
        self.emit('activate-requested')
       
    def set_title(self, title):
        self._original_title = title
        self.label.set_text(title)
       
    def get_title(self):
        return self._original_title
       
    def set_active(self, active):
        self._is_active = active
        if active:
            self.add_css_class("active")
        else:
            self.remove_css_class("active")
           
    def set_modified(self, modified):
        if modified:
            self.label.set_text(f"● {self._original_title}")
            self.add_css_class("modified")
        else:
            self.label.set_text(self._original_title)
            self.remove_css_class("modified")

class TextEditorPage(Gtk.Box):
    """A text editor page with syntax highlighting and advanced features"""
   
    def __init__(self, filename=None, settings_manager=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
       
        self.filename = filename
        self._modified = False
        self.settings_manager = settings_manager
       
        # Create scrolled window for text view
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
       
        # Create source view instead of regular text view
        self.text_view = GtkSource.View()
        self.buffer = GtkSource.Buffer()
        self.text_view.set_buffer(self.buffer)
        
        # Set basic properties
        self.text_view.set_monospace(True)
        self.text_view.set_vexpand(True)
        self.text_view.set_hexpand(True)
        
        # Set margins
        self.text_view.set_left_margin(12)
        self.text_view.set_right_margin(12)
        self.text_view.set_top_margin(12)
        self.text_view.set_bottom_margin(12)
        
        # Apply settings
        self.apply_settings()
        
        # Connect to buffer changes
        self.buffer.connect('changed', self._on_buffer_changed)
       
        # Add text view to scrolled window
        scrolled.set_child(self.text_view)
        self.append(scrolled)
       
        # Load file if provided
        if filename:
            self.load_file(filename)
            
    def apply_settings(self):
        """Apply settings to the text view"""
        if not self.settings_manager:
            return
            
        # Line numbers
        self.text_view.set_show_line_numbers(self.settings_manager.get('show_line_numbers'))
        
        # Tab settings
        self.text_view.set_tab_width(self.settings_manager.get('tab_width'))
        self.text_view.set_insert_spaces_instead_of_tabs(self.settings_manager.get('insert_spaces_instead_of_tabs'))
        
        # Text editing features
        self.text_view.set_highlight_current_line(self.settings_manager.get('highlight_current_line'))
        self.text_view.set_auto_indent(self.settings_manager.get('auto_indent'))
        
        # Text wrapping
        wrap_mode = Gtk.WrapMode.WORD_CHAR if self.settings_manager.get('wrap_text') else Gtk.WrapMode.NONE
        self.text_view.set_wrap_mode(wrap_mode)
        
        # Right margin
        self.text_view.set_show_right_margin(self.settings_manager.get('show_right_margin'))
        self.text_view.set_right_margin_position(self.settings_manager.get('right_margin_position'))
        
        # Bracket matching
        self.buffer.set_highlight_matching_brackets(self.settings_manager.get('highlight_matching_brackets'))
        
        # Space/tab drawing
        space_drawer = self.text_view.get_space_drawer()
        if self.settings_manager.get('draw_spaces'):
            space_drawer.set_types_for_locations(GtkSource.SpaceLocationFlags.ALL, GtkSource.SpaceTypeFlags.SPACE)
        if self.settings_manager.get('draw_tabs'):
            space_drawer.set_types_for_locations(GtkSource.SpaceLocationFlags.ALL, GtkSource.SpaceTypeFlags.TAB)
        
        # Font settings
        self._apply_font_settings()
        
        # Syntax highlighting theme
        self._apply_syntax_theme()
        
    def _apply_font_settings(self):
        """Apply font settings"""
        font_family = self.settings_manager.get('font_family')
        font_size = self.settings_manager.get('font_size')
        
        if font_family == 'system':
            # Use system monospace font
            font_desc = Pango.FontDescription()
            font_desc.set_family('monospace')
            if font_size > 0:
                font_desc.set_size(font_size * Pango.SCALE)
        else:
            font_desc = Pango.FontDescription()
            font_desc.set_family(font_family)
            if font_size > 0:
                font_desc.set_size(font_size * Pango.SCALE)
            else:
                font_desc.set_size(13 * Pango.SCALE)  # Default size
        
        # For GtkSource.View, we need to use CSS to set the font
        css_provider = Gtk.CssProvider()
        
        if font_family == 'system':
            font_css = f"""
            .view {{
                font-family: monospace;
                {f'font-size: {font_size}pt;' if font_size > 0 else ''}
            }}
            """
        else:
            font_css = f"""
            .view {{
                font-family: '{font_family}';
                font-size: {font_size if font_size > 0 else 13}pt;
            }}
            """
        
        css_provider.load_from_string(font_css)
        self.text_view.get_style_context().add_provider(
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
    def _apply_syntax_theme(self):
        """Apply syntax highlighting theme"""
        scheme_manager = GtkSource.StyleSchemeManager.get_default()
        
        theme_name = self.settings_manager.get('syntax_theme')
        if theme_name == 'Atom One Dark':
            # Try to find a dark theme - common dark schemes
            dark_schemes = ['Adwaita-dark', 'classic-dark', 'kate-dark', 'oblivion', 'solarized-dark']
            scheme = None
            for scheme_id in dark_schemes:
                scheme = scheme_manager.get_scheme(scheme_id)
                if scheme:
                    break
            
            # Fallback to first available dark scheme
            if not scheme:
                all_schemes = scheme_manager.get_scheme_ids()
                for scheme_id in all_schemes:
                    if 'dark' in scheme_id.lower():
                        scheme = scheme_manager.get_scheme(scheme_id)
                        break
        else:
            scheme = scheme_manager.get_scheme('Adwaita')  # Default light theme
            
        if scheme:
            self.buffer.set_style_scheme(scheme)
            
    def detect_language(self):
        """Detect and set syntax highlighting language based on filename"""
        if not self.filename:
            return
            
        language_manager = GtkSource.LanguageManager.get_default()
        language = language_manager.guess_language(self.filename, None)
        
        if language:
            self.buffer.set_language(language)
           
    def _on_buffer_changed(self, buffer):
        if not self._modified:
            self._modified = True
            if hasattr(self, 'tab'):
                self.tab.set_modified(True)
               
    def load_file(self, filename):
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read()
            self.buffer.set_text(content)
            self.filename = filename
            self._modified = False
            self.detect_language()
            return True
        except Exception as e:
            print(f"Error loading file: {e}")
            return False
           
    def save_file(self, filename=None):
        if filename:
            self.filename = filename
           
        if not self.filename:
            return False
           
        try:
            start = self.buffer.get_start_iter()
            end = self.buffer.get_end_iter()
            content = self.buffer.get_text(start, end, False)
           
            with open(self.filename, 'w', encoding='utf-8') as f:
                f.write(content)
           
            self._modified = False
            if hasattr(self, 'tab'):
                self.tab.set_modified(False)
            self.detect_language()
            return True
        except Exception as e:
            print(f"Error saving file: {e}")
            return False
           
    def get_display_name(self):
        if self.filename:
            return os.path.basename(self.filename)
        return "Untitled"
       
    def is_modified(self):
        return self._modified

class SettingsDialog(Adw.PreferencesWindow):
    """Settings dialog for the text editor"""
    
    def __init__(self, parent, settings_manager):
        super().__init__()
        self.set_transient_for(parent)
        self.set_modal(True)
        self.set_title("Preferences")
        self.settings_manager = settings_manager
        
        # Create preference groups
        self._create_editor_group()
        self._create_appearance_group()
        self._create_font_group()
        
    def _create_editor_group(self):
        """Create editor settings group"""
        page = Adw.PreferencesPage()
        page.set_title("Editor")
        page.set_icon_name("accessories-text-editor-symbolic")
        
        group = Adw.PreferencesGroup()
        group.set_title("Editor Settings")
        
        # Line numbers
        line_numbers_row = Adw.SwitchRow()
        line_numbers_row.set_title("Show Line Numbers")
        line_numbers_row.set_subtitle("Display line numbers in the editor")
        line_numbers_row.set_active(self.settings_manager.get('show_line_numbers'))
        line_numbers_row.connect('notify::active', self._on_line_numbers_changed)
        group.add(line_numbers_row)
        
        # Tab width
        tab_width_row = Adw.SpinRow()
        tab_width_row.set_title("Tab Width")
        tab_width_row.set_subtitle("Number of spaces for tab indentation")
        adjustment = Gtk.Adjustment(value=self.settings_manager.get('tab_width'), 
                                  lower=1, upper=16, step_increment=1)
        tab_width_row.set_adjustment(adjustment)
        tab_width_row.connect('notify::value', self._on_tab_width_changed)
        group.add(tab_width_row)
        
        # Use spaces instead of tabs
        spaces_row = Adw.SwitchRow()
        spaces_row.set_title("Insert Spaces Instead of Tabs")
        spaces_row.set_subtitle("Use spaces for indentation instead of tab characters")
        spaces_row.set_active(self.settings_manager.get('insert_spaces_instead_of_tabs'))
        spaces_row.connect('notify::active', self._on_spaces_changed)
        group.add(spaces_row)
        
        # Auto indent
        auto_indent_row = Adw.SwitchRow()
        auto_indent_row.set_title("Auto Indent")
        auto_indent_row.set_subtitle("Automatically indent new lines")
        auto_indent_row.set_active(self.settings_manager.get('auto_indent'))
        auto_indent_row.connect('notify::active', self._on_auto_indent_changed)
        group.add(auto_indent_row)
        
        # Text wrapping
        wrap_row = Adw.SwitchRow()
        wrap_row.set_title("Wrap Text")
        wrap_row.set_subtitle("Wrap long lines in the editor")
        wrap_row.set_active(self.settings_manager.get('wrap_text'))
        wrap_row.connect('notify::active', self._on_wrap_changed)
        group.add(wrap_row)
        
        page.add(group)
        
        # Advanced group
        advanced_group = Adw.PreferencesGroup()
        advanced_group.set_title("Advanced")
        
        # Right margin
        margin_row = Adw.SwitchRow()
        margin_row.set_title("Show Right Margin")
        margin_row.set_subtitle("Display a vertical line at the right margin")
        margin_row.set_active(self.settings_manager.get('show_right_margin'))
        margin_row.connect('notify::active', self._on_right_margin_changed)
        advanced_group.add(margin_row)
        
        # Right margin position
        margin_pos_row = Adw.SpinRow()
        margin_pos_row.set_title("Right Margin Position")
        margin_pos_row.set_subtitle("Column position for the right margin")
        adjustment = Gtk.Adjustment(value=self.settings_manager.get('right_margin_position'),
                                  lower=40, upper=200, step_increment=1)
        margin_pos_row.set_adjustment(adjustment)
        margin_pos_row.connect('notify::value', self._on_margin_position_changed)
        advanced_group.add(margin_pos_row)
        
        # Bracket matching
        bracket_row = Adw.SwitchRow()
        bracket_row.set_title("Highlight Matching Brackets")
        bracket_row.set_subtitle("Highlight matching brackets and parentheses")
        bracket_row.set_active(self.settings_manager.get('highlight_matching_brackets'))
        bracket_row.connect('notify::active', self._on_bracket_matching_changed)
        advanced_group.add(bracket_row)
        
        page.add(advanced_group)
        self.add(page)
        
    def _create_appearance_group(self):
        """Create appearance settings group"""
        page = Adw.PreferencesPage()
        page.set_title("Appearance")
        page.set_icon_name("preferences-desktop-theme-symbolic")
        
        group = Adw.PreferencesGroup()
        group.set_title("Theme")
        
        # Syntax theme
        theme_row = Adw.ComboRow()
        theme_row.set_title("Syntax Theme")
        theme_row.set_subtitle("Color scheme for syntax highlighting")
        
        themes = Gtk.StringList()
        themes.append("Default")
        themes.append("Atom One Dark")
        theme_row.set_model(themes)
        
        current_theme = self.settings_manager.get('syntax_theme')
        if current_theme == 'Atom One Dark':
            theme_row.set_selected(1)
        else:
            theme_row.set_selected(0)
            
        theme_row.connect('notify::selected', self._on_theme_changed)
        group.add(theme_row)
        
        # Highlight current line
        highlight_row = Adw.SwitchRow()
        highlight_row.set_title("Highlight Current Line")
        highlight_row.set_subtitle("Highlight the line containing the cursor")
        highlight_row.set_active(self.settings_manager.get('highlight_current_line'))
        highlight_row.connect('notify::active', self._on_highlight_line_changed)
        group.add(highlight_row)
        
        page.add(group)
        
        # Whitespace group
        whitespace_group = Adw.PreferencesGroup()
        whitespace_group.set_title("Whitespace")
        
        # Draw spaces
        spaces_visible_row = Adw.SwitchRow()
        spaces_visible_row.set_title("Show Spaces")
        spaces_visible_row.set_subtitle("Make space characters visible")
        spaces_visible_row.set_active(self.settings_manager.get('draw_spaces'))
        spaces_visible_row.connect('notify::active', self._on_draw_spaces_changed)
        whitespace_group.add(spaces_visible_row)
        
        # Draw tabs
        tabs_visible_row = Adw.SwitchRow()
        tabs_visible_row.set_title("Show Tabs")
        tabs_visible_row.set_subtitle("Make tab characters visible")
        tabs_visible_row.set_active(self.settings_manager.get('draw_tabs'))
        tabs_visible_row.connect('notify::active', self._on_draw_tabs_changed)
        whitespace_group.add(tabs_visible_row)
        
        page.add(whitespace_group)
        self.add(page)
        
    def _create_font_group(self):
        """Create font settings group"""
        page = Adw.PreferencesPage()
        page.set_title("Font")
        page.set_icon_name("font-x-generic-symbolic")
        
        group = Adw.PreferencesGroup()
        group.set_title("Font Settings")
        
        # Font family
        font_row = Adw.ActionRow()
        font_row.set_title("Font Family")
        font_row.set_subtitle("Choose editor font (leave blank for system monospace)")
        
        font_button = Gtk.FontDialogButton()
        font_dialog = Gtk.FontDialog()
        font_dialog.set_modal(True)
        
        # Set current font if not system
        current_font = self.settings_manager.get('font_family')
        current_size = self.settings_manager.get('font_size')
        
        if current_font != 'system':
            font_desc = Pango.FontDescription()
            font_desc.set_family(current_font)
            if current_size > 0:
                font_desc.set_size(current_size * Pango.SCALE)
            else:
                font_desc.set_size(13 * Pango.SCALE)
            font_button.set_font_desc(font_desc)
        
        font_button.set_dialog(font_dialog)
        font_button.connect('notify::font-desc', self._on_font_changed)
        font_row.add_suffix(font_button)
        
        group.add(font_row)
        
        # Reset to system font
        reset_font_row = Adw.ActionRow()
        reset_font_row.set_title("Use System Font")
        reset_font_row.set_subtitle("Reset to system monospace font")
        
        reset_button = Gtk.Button()
        reset_button.set_label("Reset")
        reset_button.set_valign(Gtk.Align.CENTER)
        reset_button.add_css_class("suggested-action")
        reset_button.connect('clicked', self._on_reset_font)
        reset_font_row.add_suffix(reset_button)
        
        group.add(reset_font_row)
        
        page.add(group)
        self.add(page)
    
    # Signal handlers
    def _on_line_numbers_changed(self, switch, pspec):
        self.settings_manager.set('show_line_numbers', switch.get_active())
    
    def _on_tab_width_changed(self, spin, pspec):
        self.settings_manager.set('tab_width', int(spin.get_value()))
    
    def _on_spaces_changed(self, switch, pspec):
        self.settings_manager.set('insert_spaces_instead_of_tabs', switch.get_active())
    
    def _on_auto_indent_changed(self, switch, pspec):
        self.settings_manager.set('auto_indent', switch.get_active())
    
    def _on_wrap_changed(self, switch, pspec):
        self.settings_manager.set('wrap_text', switch.get_active())
    
    def _on_theme_changed(self, combo, pspec):
        selected = combo.get_selected()
        if selected == 1:
            self.settings_manager.set('syntax_theme', 'Atom One Dark')
        else:
            self.settings_manager.set('syntax_theme', 'Default')
    
    def _on_highlight_line_changed(self, switch, pspec):
        self.settings_manager.set('highlight_current_line', switch.get_active())
    
    def _on_right_margin_changed(self, switch, pspec):
        self.settings_manager.set('show_right_margin', switch.get_active())
    
    def _on_margin_position_changed(self, spin, pspec):
        self.settings_manager.set('right_margin_position', int(spin.get_value()))
    
    def _on_bracket_matching_changed(self, switch, pspec):
        self.settings_manager.set('highlight_matching_brackets', switch.get_active())
    
    def _on_draw_spaces_changed(self, switch, pspec):
        self.settings_manager.set('draw_spaces', switch.get_active())
    
    def _on_draw_tabs_changed(self, switch, pspec):
        self.settings_manager.set('draw_tabs', switch.get_active())
    
    def _on_font_changed(self, button, pspec):
        font_desc = button.get_font_desc()
        if font_desc:
            family = font_desc.get_family()
            size = font_desc.get_size() // Pango.SCALE
            self.settings_manager.set('font_family', family)
            self.settings_manager.set('font_size', size)
    
    def _on_reset_font(self, button):
        self.settings_manager.set('font_family', 'system')
        self.settings_manager.set('font_size', 0)

class ChromeTabBar(Adw.WrapBox):
    """Container for Chrome-like tabs using WrapBox for multi-line layout"""
   
    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.set_child_spacing(8)
        self.set_justify(0)
        self.add_css_class("chrome-tab-bar")
       
        # Tab dropdown button (hidden by default)
        self.tab_dropdown = Gtk.MenuButton()
        self.tab_dropdown.set_icon_name("pan-down-symbolic")
        self.tab_dropdown.add_css_class("flat")
        self.tab_dropdown.add_css_class("tab-nav-button")
        self.tab_dropdown.set_size_request(24, 32)
        self.tab_dropdown.set_visible(False) # Hidden by default
       
        self.tabs = []
       
    def add_tab(self, tab):
        self.tabs.append(tab)
        self.append(tab)
        self._update_controls_visibility()
        self._update_tab_dropdown()
       
    def remove_tab(self, tab):
        if tab in self.tabs:
            self.tabs.remove(tab)
            self.remove(tab)
            self._update_controls_visibility()
            self._update_tab_dropdown()
           
    def _update_controls_visibility(self):
        """Show/hide dropdown based on tab count"""
        show_controls = len(self.tabs) >= 8
        self.tab_dropdown.set_visible(show_controls)
           
    def _update_tab_dropdown(self):
        """Update the tab dropdown menu with all open tabs"""
        if len(self.tabs) < 8:
            return
           
        menu = Gio.Menu()
        for i, tab in enumerate(self.tabs):
            title = tab.get_title()
            if len(title) > 30:
                title = title[:27] + "..."
            if tab.has_css_class("modified"):
                title = f"● {title}"
            menu.append(title, f"tab.activate_{i}")
        self.tab_dropdown.set_menu_model(menu)

class TextEditor(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.set_title("Chrome-like Text Editor")
        self.set_default_size(1000, 700)
        
        # Initialize settings manager
        self.settings_manager = SettingsManager()
       
        # Create headerbar
        self.headerbar = Adw.HeaderBar()
        self.headerbar.set_title_widget(Gtk.Label(label="Atom Text Editor"))
       
        # Add file operation buttons to headerbar (left side)
        # Open button
        self.open_button = Gtk.Button()
        self.open_button.set_icon_name("document-open-symbolic")
        self.open_button.add_css_class("flat")
        self.open_button.set_tooltip_text("Open File (Ctrl+O)")
        self.open_button.connect('clicked', lambda btn: self._on_open_file(None, None))
        self.headerbar.pack_start(self.open_button)
        
        # Save button
        self.save_button = Gtk.Button()
        self.save_button.set_icon_name("document-save-symbolic")
        self.save_button.add_css_class("flat")
        self.save_button.set_tooltip_text("Save (Ctrl+S)")
        self.save_button.connect('clicked', lambda btn: self._on_save_file(None, None))
        self.headerbar.pack_start(self.save_button)
        
        # Save As button
        self.save_as_button = Gtk.Button()
        self.save_as_button.set_icon_name("document-save-as-symbolic")
        self.save_as_button.add_css_class("flat")
        self.save_as_button.set_tooltip_text("Save As (Ctrl+Shift+S)")
        self.save_as_button.connect('clicked', lambda btn: self._on_save_as_file(None, None))
        self.headerbar.pack_start(self.save_as_button)
        
        # Add separator
        separator = Gtk.Separator()
        separator.set_orientation(Gtk.Orientation.VERTICAL)
        separator.set_margin_top(8)
        separator.set_margin_bottom(8)
        separator.add_css_class("toolbar-separator")
        self.headerbar.pack_start(separator)
       
        # Add new tab button to headerbar (left side, after other buttons)
        self.new_tab_button = Gtk.Button()
        self.new_tab_button.set_icon_name("list-add-symbolic")
        self.new_tab_button.add_css_class("flat")
        self.new_tab_button.set_tooltip_text("New Tab (Ctrl+T)")
        self.new_tab_button.connect('clicked', self._on_new_tab)
        self.headerbar.pack_start(self.new_tab_button)
       
        # Add menu button to headerbar (right side)
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_menu_model(self._create_menu())
        self.headerbar.pack_end(menu_button)
       
        # Create custom tab bar
        self.tab_bar = ChromeTabBar()
       
        # Add tab dropdown to headerbar
        self.headerbar.pack_end(self.tab_bar.tab_dropdown)
       
        # Create toolbar view with headerbar
        self.toolbar_view = Adw.ToolbarView()
        self.toolbar_view.add_top_bar(self.headerbar)
        self.toolbar_view.add_top_bar(self.tab_bar)
       
        # Create stack for pages
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.NONE)
        self.stack.set_hexpand(True)
       
        self.toolbar_view.set_content(self.stack)
        self.set_content(self.toolbar_view)
       
        # Keep track of pages and active tab
        self.pages = []
        self.active_tab = None
        self.activation_history = []
       
        # Setup file dialogs
        self._setup_file_dialogs()
       
        # Create initial tab
        self._create_new_tab()
       
        # Add CSS styling
        self._setup_css()
       
        # Setup keyboard shortcuts
        self._setup_shortcuts()
       
    def _create_menu(self):
        menu = Gio.Menu()
       
        # File section
        file_section = Gio.Menu()
        file_section.append("New", "win.new")
        file_section.append("Open", "win.open")
        file_section.append("Save", "win.save")
        file_section.append("Save As", "win.save_as")
        menu.append_section("File", file_section)
       
        # View section
        view_section = Gio.Menu()
        view_section.append("New Tab", "win.new_tab")
        menu.append_section("View", view_section)
        
        # Settings section
        settings_section = Gio.Menu()
        settings_section.append("Preferences", "win.preferences")
        menu.append_section("Settings", settings_section)
       
        return menu
       
    def _setup_file_dialogs(self):
        # Open file dialog
        self.open_dialog = Gtk.FileDialog()
        self.open_dialog.set_title("Open File")
       
        # Save file dialog
        self.save_dialog = Gtk.FileDialog()
        self.save_dialog.set_title("Save File")
       
    def _setup_shortcuts(self):
        # Create action group
        action_group = Gio.SimpleActionGroup()
       
        # New file action
        new_action = Gio.SimpleAction.new("new", None)
        new_action.connect("activate", self._on_new_file)
        action_group.add_action(new_action)
       
        # Open file action
        open_action = Gio.SimpleAction.new("open", None)
        open_action.connect("activate", self._on_open_file)
        action_group.add_action(open_action)
       
        # Save file action
        save_action = Gio.SimpleAction.new("save", None)
        save_action.connect("activate", self._on_save_file)
        action_group.add_action(save_action)
       
        # Save as action
        save_as_action = Gio.SimpleAction.new("save_as", None)
        save_as_action.connect("activate", self._on_save_as_file)
        action_group.add_action(save_as_action)
       
        # New tab action
        new_tab_action = Gio.SimpleAction.new("new_tab", None)
        new_tab_action.connect("activate", self._on_new_file)
        action_group.add_action(new_tab_action)
       
        # Close tab action
        close_tab_action = Gio.SimpleAction.new("close_tab", None)
        close_tab_action.connect("activate", self._on_close_current_tab)
        action_group.add_action(close_tab_action)
       
        # Next tab action
        next_tab_action = Gio.SimpleAction.new("next_tab", None)
        next_tab_action.connect("activate", self._on_next_tab)
        action_group.add_action(next_tab_action)
       
        # Prev tab action
        prev_tab_action = Gio.SimpleAction.new("prev_tab", None)
        prev_tab_action.connect("activate", self._on_prev_tab)
        action_group.add_action(prev_tab_action)
        
        # Preferences action
        preferences_action = Gio.SimpleAction.new("preferences", None)
        preferences_action.connect("activate", self._on_preferences)
        action_group.add_action(preferences_action)
       
        self.insert_action_group("win", action_group)
       
        # Set up keyboard shortcuts
        app = self.get_application()
        app.set_accels_for_action("win.new", ["<Control>n"])
        app.set_accels_for_action("win.open", ["<Control>o"])
        app.set_accels_for_action("win.save", ["<Control>s"])
        app.set_accels_for_action("win.save_as", ["<Control><Shift>s"])
        app.set_accels_for_action("win.new_tab", ["<Control>t"])
        app.set_accels_for_action("win.close_tab", ["<Control>w"])
        app.set_accels_for_action("win.next_tab", ["<Control>Tab"])
        app.set_accels_for_action("win.prev_tab", ["<Control><Shift>Tab"])
        app.set_accels_for_action("win.preferences", ["<Control>comma"])
       
    def _setup_css(self):
        css_provider = Gtk.CssProvider()
        css = """
        /* Atom One Dark Color Scheme */
        window {
            background: #282c34;
            color: #abb2bf;
        }
       
        headerbar {
            background: #21252b;
            color: #abb2bf;
        }
       
        headerbar button {
            color: #abb2bf;
        }
        
        /* Toolbar separator styling */
        .toolbar-separator {
            background: #5c6370;
            opacity: 0.3;
        }
       
        .chrome-tab-bar {
            background: #21252b;
            padding: 0px;
        }
       
        .chrome-tab {
            background: #21252b;
            border: none;
            border-radius: 12px 12px 0px 0;
            margin-right: 0px;
            margin-bottom: 0px;
            min-height: 32px;
            transition: all 200ms ease;
            color: #5c6370;
            padding: 0px;
        }
       
        .chrome-tab:hover {
            background: #2c313c;
            color: #abb2bf;
        }
       
        .chrome-tab.active {
            background: #282c34;
            color: #abb2bf;
            border: none;
        }
       
        .chrome-tab.modified {
            font-style: italic;
        }
       
        .chrome-tab button {
            min-width: 16px;
            min-height: 16px;
            padding: 2px;
            opacity: 0.6;
            background: none;
            border: none;
            box-shadow: none;
            color: #abb2bf;
        }
       
        .chrome-tab:hover button {
            opacity: 1;
        }
       
        .tab-nav-button {
            background: none;
            border: none;
            box-shadow: none;
            opacity: 0.7;
            color: #abb2bf;
        }
       
        .tab-nav-button:hover {
            opacity: 1;
            background: rgba(171, 178, 191, 0.1);
        }
       
        stack {
            background: #282c34;
        }
       
        /* Source view styling for Atom One Dark theme */
        .view {
            background: #282c34;
            color: #abb2bf;
            caret-color: #528bff;
        }
        
        .view text {
            background: #282c34;
            color: #abb2bf;
        }
        
        .view text selection {
            background: #3e4451;
            color: #abb2bf;
        }
        
        /* Line numbers */
        .view gutter {
            background: #21252b;
            color: #636d83;
            border-right: 1px solid #181a1f;
        }
        
        .view gutter.left {
            border-right: 1px solid #181a1f;
        }
        
        /* Current line highlight */
        .view .current-line {
            background: rgba(171, 178, 191, 0.05);
        }
        
        /* Right margin */
        .view .right-margin {
            background: #3e4451;
            color: #5c6370;
        }
       
        scrolledwindow {
            background: #282c34;
        }
       
        scrolledwindow > undershoot.top {
            background-image: none;
            box-shadow: none;
        }
       
        /* Enhanced scrollbar styling */
        scrollbar {
            background: transparent;
            border: none;
            padding: 0;
        }
        
        scrollbar.vertical {
            border-left: 1px solid #21252b;
        }
        
        scrollbar.horizontal {
            border-top: 1px solid #21252b;
        }
       
        scrollbar slider {
            background: #4b5263;
            border-radius: 6px;
            border: none;
            min-width: 12px;
            min-height: 32px;
            margin: 2px;
            transition: all 200ms ease;
        }
       
        scrollbar slider:hover {
            background: #5c6370;
        }
        
        scrollbar slider:active {
            background: #abb2bf;
        }
        
        scrollbar.vertical slider {
            min-width: 10px;
            margin: 2px 1px;
        }
        
        scrollbar.horizontal slider {
            min-height: 10px;
            margin: 1px 2px;
        }
        
        /* Scrollbar trough (track) */
        scrollbar trough {
            background: #21252b;
            border-radius: 6px;
            border: none;
        }
        
        /* Hide scrollbar buttons */
        scrollbar button {
            background: none;
            border: none;
            color: transparent;
            min-width: 0;
            min-height: 0;
        }
       
        /* Menu styling */
        popover.menu {
            background: none;
            color: #abb2bf;
        }
       
        popover.menu arrow {
            background: #21252b;
            color: #21252b;
        }
       
        popover.menu contents {
            background: #21252b;
        }
       
        modelbutton.flat {
            background: none;
            border: none;
            box-shadow: none;
            color: #abb2bf;
            min-height: 24px;
            padding: 4px 8px;
        }
       
        modelbutton.flat:hover {
            background: #3e4451;
        }
        
        /* Preferences dialog styling */
        preferencesdialog {
            background: #282c34;
            color: #abb2bf;
        }
        
        preferencespage {
            background: #282c34;
            color: #abb2bf;
        }
        
        preferencesgroup {
            background: #282c34;
            color: #abb2bf;
        }
        
        row {
            background: #282c34;
            color: #abb2bf;
        }
        
        row:hover {
            background: #2c313c;
        }
        """
        css_provider.load_from_string(css)
        Gtk.StyleContext.add_provider_for_display(
            self.get_display(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )
        
    def _update_tab_bar_visibility(self):
        if len(self.pages) > 1:
            self.tab_bar.set_visible(True)
        else:
            self.tab_bar.set_visible(False)
       
    def _create_new_tab(self, filename=None):
        # Create new page
        page = TextEditorPage(filename, self.settings_manager)
       
        # Create tab
        title = page.get_display_name()
        tab = ChromeTab(title, True)
       
        # Connect signals
        tab.connect('close-requested', self._on_tab_close, page)
        tab.connect('activate-requested', self._on_tab_activate, page)
       
        # Link tab and page
        page.tab = tab
       
        # Add to collections
        self.pages.append(page)
        self.tab_bar.add_tab(tab)
        self.stack.add_child(page)
       
        # Setup tab dropdown actions
        self._setup_tab_actions()
       
        # Activate the new tab and focus it
        self._activate_tab(tab, page)
       
        self._update_tab_bar_visibility()
       
        return page, tab
       
    def _setup_tab_actions(self):
        """Setup actions for tab dropdown menu"""
        action_group = Gio.SimpleActionGroup()
       
        for i, page in enumerate(self.pages):
            action = Gio.SimpleAction.new(f"activate_{i}", None)
            action.connect("activate", lambda a, p, idx=i: self._activate_tab_by_index(idx))
            action_group.add_action(action)
           
        self.tab_bar.tab_dropdown.insert_action_group("tab", action_group)
       
    def _activate_tab_by_index(self, index):
        """Activate tab by index from dropdown menu"""
        if 0 <= index < len(self.pages):
            tab = self.tab_bar.tabs[index]
            page = self.pages[index]
            self._activate_tab(tab, page)
       
    def _on_new_tab(self, button):
        self._create_new_tab()
       
    def _on_new_file(self, action, param):
        self._create_new_tab()
       
    def _on_open_file(self, action, param):
        def on_open_finish(dialog, result):
            try:
                file = dialog.open_finish(result)
                if file:
                    filename = file.get_path()
                    self._create_new_tab(filename)
            except Exception as e:
                print(f"Error opening file: {e}")
               
        self.open_dialog.open(self, None, on_open_finish)
       
    def _on_save_file(self, action, param):
        if self.active_tab and self.pages:
            current_page = self._get_current_page()
            if current_page:
                if current_page.filename:
                    current_page.save_file()
                    self._update_tab_title(current_page)
                else:
                    self._save_file_as(current_page)
                   
    def _on_save_as_file(self, action, param):
        current_page = self._get_current_page()
        if current_page:
            self._save_file_as(current_page)
            
    def _on_preferences(self, action, param):
        """Open preferences dialog"""
        dialog = SettingsDialog(self, self.settings_manager)
        dialog.connect('close-request', self._on_preferences_closed)
        dialog.present()
        
    def _on_preferences_closed(self, dialog):
        """Apply settings when preferences dialog is closed"""
        self._apply_settings_to_all_pages()
        return False  # Allow the dialog to close
        
    def _apply_settings_to_all_pages(self):
        """Apply current settings to all open pages"""
        for page in self.pages:
            page.apply_settings()
           
    def _save_file_as(self, page):
        def on_save_finish(dialog, result):
            try:
                file = dialog.save_finish(result)
                if file:
                    filename = file.get_path()
                    if page.save_file(filename):
                        self._update_tab_title(page)
            except Exception as e:
                print(f"Error saving file: {e}")
               
        self.save_dialog.save(self, None, on_save_finish)
       
    def _get_current_page(self):
        visible_child = self.stack.get_visible_child()
        return visible_child if visible_child in self.pages else None
       
    def _update_tab_title(self, page):
        if hasattr(page, 'tab'):
            title = page.get_display_name()
            page.tab.set_title(title)
       
    def _on_close_current_tab(self, action, param):
        current_page = self._get_current_page()
        if current_page:
            self._on_tab_close(current_page.tab, current_page)
       
    def _on_tab_close(self, tab, page):
        if page.is_modified():
            # Here you could show a save dialog
            pass
           
        if page in self.activation_history:
            self.activation_history.remove(page)
           
        # Remove from collections
        self.pages.remove(page)
        self.tab_bar.remove_tab(tab)
        self.stack.remove(page)
       
        # If this was the active tab, activate another
        if self.active_tab == tab:
            if self.activation_history:
                last_page = self.activation_history[-1]
                last_tab = last_page.tab
                self._activate_tab(last_tab, last_page)
            elif self.pages:
                self._activate_tab(self.tab_bar.tabs[0], self.pages[0])
            else:
                self._create_new_tab()
        else:
            if not self.pages:
                self._create_new_tab()
           
        self._setup_tab_actions()
        self._update_tab_bar_visibility()
           
    def _on_tab_activate(self, tab, page):
        self._activate_tab(tab, page)
       
    def _activate_tab(self, tab, page):
        # Deactivate current tab
        if self.active_tab:
            self.active_tab.set_active(False)
           
        # Activate new tab
        tab.set_active(True)
        self.active_tab = tab
       
        # Show corresponding page
        self.stack.set_visible_child(page)
       
        # Focus text view immediately
        page.text_view.grab_focus()
       
        # Update activation history
        if page in self.activation_history:
            self.activation_history.remove(page)
        self.activation_history.append(page)
       
    def _on_next_tab(self, action, param):
        if not self.pages:
            return
        current_page = self._get_current_page()
        if current_page:
            current_index = self.pages.index(current_page)
            next_index = (current_index + 1) % len(self.pages)
            next_tab = self.tab_bar.tabs[next_index]
            next_page = self.pages[next_index]
            self._activate_tab(next_tab, next_page)
       
    def _on_prev_tab(self, action, param):
        if not self.pages:
            return
        current_page = self._get_current_page()
        if current_page:
            current_index = self.pages.index(current_page)
            prev_index = (current_index - 1) % len(self.pages)
            prev_tab = self.tab_bar.tabs[prev_index]
            prev_page = self.pages[prev_index]
            self._activate_tab(prev_tab, prev_page)

class TextEditorApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="com.example.ChromeTextEditor")
       
    def do_activate(self):
        window = TextEditor(self)
        window.present()

if __name__ == "__main__":
    app = TextEditorApp()
    app.run()