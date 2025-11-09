# -*- coding: utf-8 -*-
"""
YB Tools - Menu Registration

This file executes after Nuke UI is loaded
Responsible for adding our tools to the node menu, allowing users to create AEBridge nodes
"""

import nuke
import os
import sys

plugin_path = os.path.dirname(__file__)

# Ensure Python can find AEBridge module
aebridge_path = os.path.join(plugin_path, 'AEBridge')
if aebridge_path not in sys.path:
    sys.path.insert(0, aebridge_path)

# Create YB submenu under Nodes menu (with our Logo)
yb_menu = nuke.menu('Nodes').addMenu('YB', icon='yb_logo.png')

# Track failed tools (for troubleshooting)
failed_tools = []

# Register AE Bridge tool
try:
    from AEBridge import ae_bridge
    yb_menu.addCommand(
        'AE Bridge',
        lambda: ae_bridge.create_ae_bridge_node()
    )
    # Register callbacks: automatically manage input ports (similar to Merge node behavior)
    ae_bridge.register_aebridge_callbacks()
except Exception as e:
    failed_tools.append('AE Bridge')
    import traceback
    traceback.print_exc()


# Add menu separator and utility tools
yb_menu.addSeparator()

# Add About window with update check feature
try:
    import updater
    
    def show_about_window():
        """Show About window with update check functionality"""
        try:
            current_version = updater.get_current_version()
            auto_update_status = "Enabled" if updater.is_auto_update_enabled() else "Disabled"
            
            import sys
            if sys.version_info[0] >= 3:
                from PySide2 import QtWidgets, QtCore, QtGui
            else:
                try:
                    from PySide import QtGui as QtWidgets, QtCore, QtGui
                except ImportError:
                    try:
                        from PySide2 import QtWidgets, QtCore, QtGui
                    except ImportError:
                        # Fallback to simple message
                        nuke.message(
                            "YB Tools v{}\n\n"
                            "Nuke Node Toolset\n"
                            "Includes AE Bridge and other utility tools\n\n"
                            "Author: YB_\n"
                            "Project Homepage:\n"
                            "github.com/yongbin1999/YB_Nuke_ToolSets\n\n"
                            "Auto-Update: {}".format(current_version, auto_update_status)
                        )
                        updater.manual_update_check()
                        return
            
            class AboutDialog(QtWidgets.QDialog):
                def __init__(self, parent=None):
                    super(AboutDialog, self).__init__(parent)
                    self.setWindowTitle("About YB Tools")
                    self.setMinimumWidth(600)
                    self.setMinimumHeight(500)
                    self.setMaximumWidth(700)
                    self.setMaximumHeight(600)
                    
                    # Set window style
                    self.setStyleSheet("""
                        QDialog {
                            background-color: #2b2b2b;
                            color: #e0e0e0;
                        }
                        QLabel {
                            color: #e0e0e0;
                        }
                        QPushButton {
                            background-color: #3d3d3d;
                            border: 1px solid #555555;
                            border-radius: 4px;
                            padding: 8px 16px;
                            min-height: 24px;
                            color: #e0e0e0;
                        }
                        QPushButton:hover {
                            background-color: #4a4a4a;
                            border: 1px solid #666666;
                        }
                        QPushButton:pressed {
                            background-color: #2a2a2a;
                        }
                        QPushButton:disabled {
                            background-color: #252525;
                            color: #666666;
                            border: 1px solid #333333;
                        }
                        QProgressBar {
                            border: 1px solid #555555;
                            border-radius: 4px;
                            background-color: #1e1e1e;
                            text-align: center;
                            color: #e0e0e0;
                            height: 20px;
                        }
                        QProgressBar::chunk {
                            background-color: #4a9eff;
                            border-radius: 3px;
                        }
                    """)
                    
                    main_layout = QtWidgets.QVBoxLayout()
                    main_layout.setSpacing(20)
                    main_layout.setContentsMargins(30, 30, 30, 30)
                    
                    # Title section
                    title_layout = QtWidgets.QVBoxLayout()
                    title_layout.setSpacing(8)
                    
                    title_label = QtWidgets.QLabel("YB Tools")
                    title_font = QtGui.QFont()
                    title_font.setPointSize(24)
                    title_font.setBold(True)
                    title_label.setFont(title_font)
                    title_label.setStyleSheet("color: #4a9eff;")
                    title_layout.addWidget(title_label)
                    
                    version_label = QtWidgets.QLabel("Version {}".format(current_version))
                    version_font = QtGui.QFont()
                    version_font.setPointSize(12)
                    version_label.setFont(version_font)
                    version_label.setStyleSheet("color: #999999;")
                    title_layout.addWidget(version_label)
                    
                    main_layout.addLayout(title_layout)
                    
                    # Separator
                    separator = QtWidgets.QFrame()
                    separator.setFrameShape(QtWidgets.QFrame.HLine)
                    separator.setFrameShadow(QtWidgets.QFrame.Sunken)
                    separator.setStyleSheet("color: #555555;")
                    main_layout.addWidget(separator)
                    
                    # Info section
                    info_layout = QtWidgets.QVBoxLayout()
                    info_layout.setSpacing(12)
                    
                    description_label = QtWidgets.QLabel(
                        "Nuke Node Toolset\n"
                        "Includes AE Bridge and other utility tools"
                    )
                    description_label.setWordWrap(True)
                    description_label.setStyleSheet("color: #cccccc; padding: 10px 0;")
                    info_layout.addWidget(description_label)
                    
                    author_label = QtWidgets.QLabel("Author: <span style='color: #4a9eff;'>YB_</span>")
                    author_label.setTextFormat(QtCore.Qt.RichText)
                    author_label.setWordWrap(True)
                    info_layout.addWidget(author_label)
                    
                    homepage_label = QtWidgets.QLabel(
                        "Project Homepage: <a href='https://github.com/yongbin1999/YB_Nuke_ToolSets' style='color: #4a9eff;'>github.com/yongbin1999/YB_Nuke_ToolSets</a>"
                    )
                    homepage_label.setOpenExternalLinks(True)
                    homepage_label.setTextFormat(QtCore.Qt.RichText)
                    homepage_label.setWordWrap(True)
                    info_layout.addWidget(homepage_label)
                    
                    auto_update_label = QtWidgets.QLabel(
                        "Auto-Update: <span style='color: {};'>{}</span>".format(
                            "#4a9eff" if auto_update_status == "Enabled" else "#ff6b6b",
                            auto_update_status
                        )
                    )
                    auto_update_label.setTextFormat(QtCore.Qt.RichText)
                    info_layout.addWidget(auto_update_label)
                    
                    main_layout.addLayout(info_layout)
                    
                    # Separator
                    separator2 = QtWidgets.QFrame()
                    separator2.setFrameShape(QtWidgets.QFrame.HLine)
                    separator2.setFrameShadow(QtWidgets.QFrame.Sunken)
                    separator2.setStyleSheet("color: #555555;")
                    main_layout.addWidget(separator2)
                    
                    # Update section
                    update_layout = QtWidgets.QVBoxLayout()
                    update_layout.setSpacing(12)
                    
                    # Update check button
                    self.check_button = QtWidgets.QPushButton("Check for Updates")
                    self.check_button.setStyleSheet("""
                        QPushButton {
                            background-color: #4a9eff;
                            border: 1px solid #3a8eef;
                            font-weight: bold;
                        }
                        QPushButton:hover {
                            background-color: #5aaeff;
                            border: 1px solid #4a9eff;
                        }
                        QPushButton:pressed {
                            background-color: #3a8eef;
                        }
                    """)
                    self.check_button.clicked.connect(self.check_updates)
                    update_layout.addWidget(self.check_button)
                    
                    main_layout.addLayout(update_layout)
                    
                    # Spacer
                    main_layout.addStretch()
                    
                    # Close button
                    button_layout = QtWidgets.QHBoxLayout()
                    button_layout.addStretch()
                    close_button = QtWidgets.QPushButton("Close")
                    close_button.clicked.connect(self.accept)
                    close_button.setMinimumWidth(100)
                    button_layout.addWidget(close_button)
                    main_layout.addLayout(button_layout)
                    
                    self.setLayout(main_layout)
                
                def check_updates(self):
                    """Check for updates"""
                    # Disable button during check to prevent multiple clicks
                    self.check_button.setEnabled(False)
                    
                    # Run update check in thread to avoid blocking UI
                    import threading
                    def _check():
                        try:
                            updater.manual_update_check()
                        finally:
                            # Re-enable button after check completes
                            try:
                                QtCore.QTimer.singleShot(0, lambda: self.check_button.setEnabled(True))
                            except Exception:
                                try:
                                    nuke.executeInMainThread(lambda: self.check_button.setEnabled(True))
                                except Exception:
                                    self.check_button.setEnabled(True)
                    
                    thread = threading.Thread(target=_check)
                    thread.daemon = True
                    thread.start()
            
            # Show dialog
            dialog = AboutDialog()
            dialog.exec_()
            
        except Exception as e:
            # Fallback to simple message and update check
            try:
                nuke.message(
                    "YB Tools v{}\n\n"
                    "Nuke Node Toolset\n"
                    "Includes AE Bridge and other utility tools\n\n"
                    "Author: YB_\n"
                    "Project Homepage:\n"
                    "github.com/yongbin1999/YB_Nuke_ToolSets\n\n"
                    "Auto-Update: {}".format(
                        updater.get_current_version(),
                        "Enabled" if updater.is_auto_update_enabled() else "Disabled"
                    )
                )
                updater.manual_update_check()
            except Exception:
                pass
    
    yb_menu.addCommand(
        'About YB Tools',
        show_about_window,
        icon=''
    )
except Exception:
    pass


# Start update check after Nuke UI is loaded (delayed, non-blocking)
def _delayed_update_check():
    try:
        import time
        import threading
        
        def _check_after_delay():
            # Wait a few seconds to ensure Nuke is fully loaded
            time.sleep(3)
            try:
                import updater
                updater.start_update_check()
            except Exception:
                pass
        
        thread = threading.Thread(target=_check_after_delay)
        thread.daemon = True
        thread.start()
    except Exception:
        pass

# Execute update check after Nuke UI is loaded
try:
    _delayed_update_check()
except Exception:
    pass

