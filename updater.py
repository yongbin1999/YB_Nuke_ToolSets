# -*- coding: utf-8 -*-
"""
YB Tools - Auto Update Module
"""

import os
import json
import threading
import zipfile
import shutil
import tempfile

# Version configuration file
VERSION_CONFIG_FILE = "version.json"

# GitHub configuration
GITHUB_USER = "yongbin1999"
GITHUB_REPO = "YB_Nuke_ToolSets"
GITHUB_API_URL = "https://api.github.com/repos/{}/{}/releases/latest".format(GITHUB_USER, GITHUB_REPO)

# Update configuration
UPDATE_CHECK_TIMEOUT = 10  # Check timeout in seconds


def _nuke_tprint(message):
    """
    Safely print message to Nuke terminal (Script Editor)
    Works in both main thread and background threads
    """
    try:
        import nuke
        
        def _print_in_main_thread():
            """Print message in main thread"""
            try:
                nuke.tprint(message)
            except Exception:
                pass
        
        # Try to use executeInMainThread for thread safety
        try:
            nuke.executeInMainThread(_print_in_main_thread)
        except Exception:
            # Fallback: try direct tprint (may work in main thread)
            try:
                nuke.tprint(message)
            except Exception:
                # Last resort: do nothing (silent failure)
                pass
    except ImportError:
        # Nuke not available, do nothing
        pass


def get_plugin_root():
    """Get plugin root directory"""
    return os.path.dirname(os.path.abspath(__file__))


def load_version_config():
    """
    Load version configuration from version.json
    
    Returns:
        dict: {"version": "2.2.1", "auto_update": true}
        Returns default config if file doesn't exist or format is invalid
    """
    config_path = os.path.join(get_plugin_root(), VERSION_CONFIG_FILE)
    
    try:
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
        except (UnicodeDecodeError, TypeError):
            # Fallback for Python 2 or old files
            with open(config_path, 'r') as f:
                config = json.load(f)
            
        # Validate required fields
        if 'version' not in config:
            return {"version": "0.0.0", "auto_update": True}
            
        # Enable auto-update by default
        if 'auto_update' not in config:
            config['auto_update'] = True
            
        return config
        
    except Exception:
        return {"version": "0.0.0", "auto_update": True}


def get_current_version():
    """Get current version number"""
    config = load_version_config()
    return config.get('version', '0.0.0')


def is_auto_update_enabled():
    """Check if auto-update is enabled"""
    config = load_version_config()
    return config.get('auto_update', True)


def parse_version(version_str):
    """
    Parse version string into comparable tuple
    """
    # Remove 'v' prefix
    version_str = version_str.lstrip('vV')
    try:
        parts = version_str.split('.')
        return tuple(int(p) for p in parts[:3])  # Only take first 3 parts
    except (ValueError, AttributeError):
        return (0, 0, 0)


def compare_versions(current, latest):
    """
    Compare two version numbers
    
    Returns:
    1: latest > current (new version available)
    0: latest == current (same version)
    -1: latest < current (local version is newer)
    """
    current_tuple = parse_version(current)
    latest_tuple = parse_version(latest)
    
    if latest_tuple > current_tuple:
        return 1
    elif latest_tuple == current_tuple:
        return 0
    else:
        return -1


def check_for_updates(return_error=False):
    """
    Check for new version (executed asynchronously)
    
    Args:
        return_error: If True, return error message instead of None on failure
    
    Returns:
        dict: Update info dict, or None/error dict on failure
    """
    try:
        # Python 2/3 compatible HTTP request
        try:
            # Python 3
            from urllib.request import urlopen, Request
            from urllib.error import URLError, HTTPError
        except ImportError:
            # Python 2
            from urllib2 import urlopen, Request, URLError, HTTPError
        
        # Set User-Agent (required by GitHub API)
        request = Request(GITHUB_API_URL)
        request.add_header('User-Agent', 'YB-Tools-Updater')
        
        # Request GitHub API
        try:
            response = urlopen(request, timeout=UPDATE_CHECK_TIMEOUT)
        except URLError as e:
            if return_error:
                error_msg = str(e)
                if 'timeout' in error_msg.lower() or 'timed out' in error_msg.lower():
                    return {'error': 'Connection timeout. Please check your network connection.'}
                elif 'Name or service not known' in error_msg or 'getaddrinfo failed' in error_msg:
                    return {'error': 'Cannot resolve GitHub domain. Please check your DNS settings.'}
                else:
                    return {'error': 'Network error: {}. Please check your network connection.'.format(error_msg)}
            return None
        except HTTPError as e:
            if return_error:
                return {'error': 'GitHub API error ({}): {}. Please try again later.'.format(e.code, e.reason)}
            return None
        
        try:
            data = json.loads(response.read().decode('utf-8'))
        except ValueError as e:
            if return_error:
                return {'error': 'Invalid response from GitHub API. Please try again later.'}
            return None
        
        latest_version = data.get('tag_name', '').lstrip('vV')
        if not latest_version:
            if return_error:
                return {'error': 'No version information found in GitHub release.'}
            return None
        
        current_version = get_current_version()
        
        # Compare versions
        compare_result = compare_versions(current_version, latest_version)
        
        if compare_result != 1:  # Only 1 means new version available
            return {
                'has_update': False,
                'latest_version': latest_version,
                'download_url': None,
                'release_notes': None
            }
        
        # Find zip download link
        download_url = None
        assets = data.get('assets', [])
        for asset in assets:
            if asset.get('name', '').endswith('.zip'):
                download_url = asset.get('browser_download_url')
                break
        
        # If no zip uploaded, use source zip
        if not download_url:
            download_url = data.get('zipball_url')
        
        return {
            'has_update': True,
            'latest_version': latest_version,
            'download_url': download_url,
            'release_notes': data.get('body', ''),
            'release_url': data.get('html_url', '')
        }
        
    except Exception as e:
        # Fail silently for background checks, return error for manual checks
        if return_error:
            return {'error': 'Unexpected error: {}. Please try again later.'.format(str(e))}
        return None


def download_update(download_url, target_path, progress_callback=None):
    """
    Download update package
    
    Args:
        download_url: URL to download from
        target_path: Local path to save the file
        progress_callback: Optional callback function(status, progress) for progress updates
    
    Returns:
    True: Download successful
    False: Download failed
    """
    try:
        try:
            from urllib.request import urlopen, Request
        except ImportError:
            from urllib2 import urlopen, Request
        
        if progress_callback:
            progress_callback("downloading", 0)
        
        request = Request(download_url)
        request.add_header('User-Agent', 'YB-Tools-Updater')
        
        response = urlopen(request)
        total_size = int(response.headers.get('Content-Length', 0))
        downloaded = 0
        chunk_size = 8192
        
        with open(target_path, 'wb') as f:
            while True:
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                
                if progress_callback and total_size > 0:
                    progress = int((downloaded / total_size) * 100)
                    progress_callback("downloading", progress)
        
        if progress_callback:
            progress_callback("downloading", 100)
        
        return True
        
    except Exception:
        return False


def find_plugin_root_in_dir(directory):
    """
    Find plugin root directory in extracted directory
    
    Plugin root directory should contain init.py and menu.py files
    """
    # Check if current directory is plugin root
    if os.path.exists(os.path.join(directory, 'init.py')) and \
       os.path.exists(os.path.join(directory, 'menu.py')):
        return directory
    
    # Recursively search subdirectories
    for root, dirs, files in os.walk(directory):
        if 'init.py' in files and 'menu.py' in files:
            return root
    
    return None


def apply_update(zip_path, new_version=None):
    """
    Apply update (extract to plugin directory)
    
    Args:
        zip_path: Path to the update ZIP file
        new_version: New version string to update in version.json (optional)
    
    Returns:
        bool: True if update was successful, False otherwise
    """
    plugin_root = get_plugin_root()
    temp_dir = None
    
    try:
        # Validate ZIP file
        if not os.path.exists(zip_path):
            return False
        
        # Check if ZIP file is valid
        try:
            # Python 2/3 compatible BadZipFile exception
            try:
                BadZipFile = zipfile.BadZipFile
            except AttributeError:
                # Python 2 uses zipfile.error
                BadZipFile = zipfile.error
            
            with zipfile.ZipFile(zip_path, 'r') as test_zip:
                test_zip.testzip()
        except (BadZipFile, zipfile.error):
            return False
        except Exception:
            return False
        
        # Create temporary directory
        temp_dir = tempfile.mkdtemp(prefix='yb_tools_update_')
        
        # Extract update package
        try:
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                # Extract all files
                zip_ref.extractall(temp_dir)
        except Exception:
            return False
        
        # Find plugin root directory
        source_dir = find_plugin_root_in_dir(temp_dir)
        
        if not source_dir:
            # If not found, try other methods
            extracted_items = os.listdir(temp_dir)
            
            # If only one directory, might be GitHub zipball format
            if len(extracted_items) == 1 and os.path.isdir(os.path.join(temp_dir, extracted_items[0])):
                nested_dir = os.path.join(temp_dir, extracted_items[0])
                source_dir = find_plugin_root_in_dir(nested_dir)
                if not source_dir:
                    source_dir = nested_dir
            else:
                source_dir = temp_dir
        
        if not source_dir:
            return False
        
        # Verify source directory contains necessary files
        if not os.path.exists(os.path.join(source_dir, 'init.py')):
            return False
        
        # Copy new files (exclude certain files)
        exclude_patterns = ['.git', '__pycache__', '.DS_Store']
        exclude_files = []
        
        copied_count = 0
        for item in os.listdir(source_dir):
            # Skip excluded files/directories
            if item in exclude_files:
                continue
            if any(pattern in item for pattern in exclude_patterns):
                continue
            
            source_item = os.path.join(source_dir, item)
            target_item = os.path.join(plugin_root, item)
            
            try:
                # Delete old files/directories
                if os.path.exists(target_item):
                    if os.path.isdir(target_item):
                        shutil.rmtree(target_item)
                    else:
                        os.remove(target_item)
                
                # Copy new files/directories
                if os.path.isdir(source_item):
                    shutil.copytree(source_item, target_item)
                else:
                    shutil.copy2(source_item, target_item)
                copied_count += 1
            except Exception:
                # Continue processing other files
                pass
        
        # Update successful, update version.json if new version is provided
        if new_version:
            try:
                config_path = os.path.join(plugin_root, VERSION_CONFIG_FILE)
                config = load_version_config()
                config['version'] = new_version
                # Preserve auto_update setting
                try:
                    with open(config_path, 'w', encoding='utf-8') as f:
                        json.dump(config, f, indent=4, ensure_ascii=False)
                except (TypeError, UnicodeEncodeError):
                    # Fallback for Python 2
                    with open(config_path, 'w') as f:
                        json.dump(config, f, indent=4)
            except Exception:
                # Continue even if version update fails
                pass
        
        # Delete ZIP file
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass
        
        return True
        
    except Exception:
        return False
        
    finally:
        # Clean up temporary directory
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass


def download_and_apply_update_async(update_info, is_auto_update=False, status_callback=None):
    """
    Download and apply update immediately in background
    
    Args:
        update_info: Update information dict
        is_auto_update: If True, this is an automatic update (silent download, show notification after)
                        If False, this is a manual update (show progress, show notification after)
        status_callback: Optional callback function(status, message, progress) for UI updates
                         status: 'checking', 'downloading', 'applying', 'completed', 'error'
                         message: Status message string
                         progress: Progress percentage (0-100) or None
    """
    def _download_and_apply_thread():
        # Note: os, sys, importlib are already imported at module level
        plugin_root = get_plugin_root()
        zip_path = os.path.join(tempfile.gettempdir(), 'yb_tools_update.zip')
        
        version = update_info.get('latest_version', '')
        release_notes = update_info.get('release_notes', '')
        
        def progress_callback(status, progress):
            """Internal progress callback that forwards to status_callback"""
            if status_callback:
                if status == "downloading":
                    status_callback("downloading", "Downloading update package... {}%".format(progress), progress)
                else:
                    status_callback(status, "", progress)
        
        # Show download message
        _nuke_tprint("[YB Tools] Downloading update package...")
        if status_callback:
            status_callback("downloading", "Downloading update package...", 0)
        
        # Download update with progress
        if not download_update(update_info['download_url'], zip_path, progress_callback=progress_callback):
            error_msg = "Failed to download update package. Please check your network connection."
            _nuke_tprint("[YB Tools] " + error_msg)
            if status_callback:
                status_callback("error", error_msg, None)
            return
        
        # Show applying message
        _nuke_tprint("[YB Tools] Applying update...")
        if status_callback:
            status_callback("applying", "Applying update...", None)
        
        # Apply update immediately
        if apply_update(zip_path, new_version=version):
            # Update successful - show notification
            _nuke_tprint("="*70)
            _nuke_tprint("[YB Tools] YB Tools has been updated to version {}.".format(version))
            _nuke_tprint("="*70)
            _nuke_tprint("[YB Tools] IMPORTANT: Please restart Nuke to use the new version.")
            
            restart_message = (
                "Update completed successfully!\n\n"
                "Version {} has been installed.\n\n"
                "Please restart Nuke to use the new version.\n\n"
                "Note: Nuke does not support hot reloading of plugins, "
                "so a restart is required for the changes to take effect."
            ).format(version)
            
            if status_callback:
                status_callback("completed", restart_message, 100)
        else:
            error_msg = "Failed to apply update. Please try again or update manually."
            _nuke_tprint("[YB Tools] " + error_msg)
            if status_callback:
                status_callback("error", error_msg, None)
    
    # Start background thread
    thread = threading.Thread(target=_download_and_apply_thread)
    thread.daemon = True  # Daemon thread, won't block Nuke exit
    thread.start()


def start_update_check():
    """
    Start update check (called after Nuke UI is loaded)
    
    Entire process doesn't block Nuke startup.
    If update is found, it will be downloaded and applied immediately.
    """
    # Check if auto-update is enabled
    if not is_auto_update_enabled():
        return
    
    # Check for new version in background
    def _check_thread():
        update_info = check_for_updates()
        
        if update_info and update_info['has_update']:
            # Download and apply update immediately (silent download, show notification after)
            download_and_apply_update_async(update_info, is_auto_update=True)
    
    # Start check thread
    thread = threading.Thread(target=_check_thread)
    thread.daemon = True
    thread.start()




# Manual update function (can be added to menu)
def manual_update_check(status_callback=None):
    """
    Manually check for updates (user-initiated)
    Shows results in terminal and returns update info for UI display
    
    Args:
        status_callback: Optional callback function(status, message, progress) for UI updates
    
    Returns:
        dict: Update info dict with 'has_update', 'latest_version', etc.
    """
    try:
        import nuke
        
        # Show checking status
        if status_callback:
            status_callback("checking", "Checking for updates...", None)
        
        # Check for updates with error details
        update_info = check_for_updates(return_error=True)
        
        if not update_info:
            error_msg = "Update check failed. Please check your network connection."
            _nuke_tprint("[YB Tools] " + error_msg)
            if status_callback:
                status_callback("error", error_msg, None)
            return {'error': error_msg}
        
        # Check if there's an error
        if 'error' in update_info:
            error_msg = "Update check failed: {}".format(update_info['error'])
            _nuke_tprint("[YB Tools] " + error_msg)
            if status_callback:
                status_callback("error", error_msg, None)
            return update_info
        
        if not update_info['has_update']:
            msg = ("You are already on the latest version!\n"
                   "Current version: v{}\n"
                   "Latest version: v{}").format(
                get_current_version(), update_info['latest_version']
            )
            _nuke_tprint("[YB Tools] " + msg)
            if status_callback:
                status_callback("no_update", msg, None)
            return update_info
        
        # Ask if user wants to download update
        result = nuke.ask(
            "New version found!\n\n"
            "Current version: v{}\n"
            "Latest version: v{}\n\n"
            "Download now?".format(
                get_current_version(),
                update_info['latest_version']
            )
        )
        
        if result:
            # Download and apply update immediately with status callback
            download_and_apply_update_async(update_info, is_auto_update=False, status_callback=status_callback)
        else:
            _nuke_tprint("[YB Tools] Update cancelled by user.")
            if status_callback:
                status_callback("cancelled", "Update cancelled by user.", None)
        
        return update_info
        
    except ImportError:
        return {'error': 'Nuke module not available'}
    except Exception as e:
        error_msg = "Update check failed. Unexpected error: {}".format(str(e))
        _nuke_tprint("[YB Tools] " + error_msg)
        if status_callback:
            status_callback("error", error_msg, None)
        return {'error': error_msg}

