# -*- coding: utf-8 -*-
"""
AEBridge - Nuke 到 After Effects 的桥接工具

"""

import nuke
import nukescripts
import os
import sys
import json
import subprocess
import platform
import re
from typing import List

try:
    from AEBridge import ae_jsx as _ae_jsx
except Exception:
    try:
        import ae_jsx as _ae_jsx
    except Exception:
        _ae_jsx = None


def normalize_path(path):
    """
    路径标准化
    - Windows 用反斜杠，Unix 用正斜杠
    - 网络路径（UNC）需要特殊处理
    - 统一成正斜杠，避免跨平台问题
    """
    if not path:
        return path
    p = path.replace('\\', '/')
    # 保留 UNC 前缀（网络路径的 // 开头）
    if p.startswith('//'):
        p = '//' + re.sub(r'/+', '/', p[2:])
    else:
        p = re.sub(r'/+', '/', p)
    return p


def _lower(s):
    """小写转换（避免异常）"""
    try:
        return s.lower()
    except Exception:
        return str(s)


def _normalize_colorspace_name(name):
    """
    色彩空间名称标准化
    不同版本的 Nuke 对同一个色彩空间可能有不同的叫法
    比如："linear", "scene_linear", "Linear", "default (linear)" 都指同一个东西
    统一成标准名称，方便后续处理
    """
    if not name:
        return 'linear'
    n = _lower(name).strip()
    # Common colorspace mappings
    if 'default' in n and 'linear' in n:
        return 'linear'
    if n in ('srgb', 's rgb', 's-rgb', 'srgb (~2.20)', 'srgb  ~2.20'):
        return 'sRGB'
    if 'acescg' in n:
        return 'ACEScg'
    if 'reference' in n:
        return 'linear'
    if 'color_picking' in n and 'srgb' in n:
        return 'sRGB'
    if n.startswith('rgb') and 'linear' in n:
        return 'linear'
    if n == 'linear':
        return 'linear'
    return name


def _set_enum_knob_safely(knob, desired_name_or_keywords):
    """
    智能设置枚举类型的属性

    """
    try:
        values = []
        try:
            values = list(knob.values())
        except Exception:
            pass
        if not values:
            knob.setValue(desired_name_or_keywords if isinstance(desired_name_or_keywords, str) else ' '.join(desired_name_or_keywords))
            return True

        def match_by_keywords(val, keywords):
            lv = _lower(val)
            return all(_lower(k) in lv for k in keywords)

        def tokenize_words(text):
            try:
                import re as _re
                return [t for t in _re.split(r"[^0-9a-zA-Z]+", text) if t]
            except Exception:
                return [text]

        candidates = []
        if isinstance(desired_name_or_keywords, str):
            candidates = [tokenize_words(desired_name_or_keywords)]
        else:
            candidates = [desired_name_or_keywords]

        # 策略1：精确匹配
        for cand in candidates:
            want = ' '.join(cand)
            for v in values:
                if _lower(v) == _lower(want):
                    knob.setValue(v)
                    return True

        # 策略2：关键词包含匹配
        for cand in candidates:
            for v in values:
                if match_by_keywords(v, cand):
                    knob.setValue(v)
                    return True

        # 策略3：前缀匹配
        for cand in candidates:
            if not cand:
                continue
            key = cand[0]
            for v in values:
                if _lower(key) in _lower(v):
                    knob.setValue(v)
                    return True

        return False
    except Exception:
        return False


def generate_unique_node_name(base_name):
    """Return a unique node name, falling back when nuke.uniqueName is unavailable."""
    try:
        existing_names = set(n.name() for n in nuke.allNodes())
    except Exception:
        existing_names = set()
    if base_name not in existing_names:
        return base_name
    index = 1
    while True:
        candidate = f"{base_name}{index}"
        if candidate not in existing_names:
            return candidate
        index += 1


def find_all_ae_versions():
    """Discover installed After Effects versions and return a sorted list."""
    system = platform.system()
    found_versions = []
    
    try:
        if system == 'Windows':
            # 常见安装根目录（覆盖 C/D 盘与 x86）
            base_dirs = [
                r'C:\\Program Files\\Adobe',
                r'C:\\Program Files (x86)\\Adobe',
                r'D:\\Program Files\\Adobe',
                r'D:\\Adobe',
                r'C:\\Adobe',
            ]
            for base_path in base_dirs:
                if not os.path.exists(base_path):
                    continue
                for folder in os.listdir(base_path):
                    if 'After Effects' in folder:
                        ae_exe = os.path.join(base_path, folder, 'Support Files', 'AfterFX.exe')
                        if os.path.exists(ae_exe):
                            found_versions.append({'name': folder, 'path': ae_exe})

            # 兜底：在上述目录内深度搜索 AfterFX.exe
            if not found_versions:
                for base_path in base_dirs:
                    if not os.path.exists(base_path):
                        continue
                    for root, _dirs, files in os.walk(base_path):
                        if 'AfterFX.exe' in files:
                            found_versions.append({
                                'name': os.path.basename(os.path.dirname(root)),
                                'path': os.path.join(root, 'AfterFX.exe')
                            })
        
        elif system == 'Darwin':  # macOS
            # Scan all possible versions on macOS
            base_path = '/Applications'
            if os.path.exists(base_path):
                for folder in os.listdir(base_path):
                    if 'After Effects' in folder:
                        ae_exe = os.path.join(base_path, folder, folder + '.app', 'Contents', 'MacOS', 'After Effects')
                        if os.path.exists(ae_exe):
                            found_versions.append({'name': folder, 'path': ae_exe})
        
        # Sort by version number descending (newest first)
        found_versions.sort(key=lambda x: x['name'], reverse=True)
    except Exception:
        pass
    return found_versions


def find_ae_executable_static():
    """Return the latest After Effects executable path using static discovery."""
    try:
        versions = find_all_ae_versions()
        if versions:
            return versions[0]['path']
    except Exception:
        pass
    return None


class AEBridgeNode(object):
    """
    AEBridge 节点控制器

    - 管理节点的所有交互逻辑
    - 协调 Nuke 和 AE 之间的数据流
    - 处理素材渲染、工程创建、输出管理等所有核心功能
    
    """
    
    def __init__(self, node):
        self.node = node
        self.plugin_path = os.path.dirname(os.path.dirname(__file__))
        
    def knobChanged(self, knob):
        """Handle property changes."""
        try:
            if knob.name() == 'auto_find_ae':
                self.auto_find_ae()
            elif knob.name() == 'browse_project_path':
                self.browse_project_path()
            elif knob.name() == 'create_or_edit_button':
                self.create_or_edit_ae()
            elif knob.name() == 'refresh_output':
                self.refresh_output()
            elif knob.name() == 'refresh_render_path':
                self.refresh_render_path()
            elif knob.name() == 'custom_project_path':
                # Update default output path when AE project path changes (if not manually set)
                self._update_default_output_path()
            elif knob.name() == 'ae_output_render_path':
                # Mark output path as manually set when user modifies it
                try:
                    self.node['_output_path_user_set'].setValue(True)
                except Exception:
                    pass
            elif knob.name() == 'write_aces_compliant':
                # When ACES compliant option changes, lock/unlock compression and bit depth
                self._update_aces_lock_state()
            elif knob.name() == 'write_ACES_compliant_EXR':
                # 当 ACES 选项改变时，显示/隐藏相关选项 (legacy support)
                self._update_aces_visibility()
        except Exception as e:
            nuke.message("Error: {}".format(str(e)))
    
    def browse_project_path(self):
        """Open the system file browser at the custom project path."""
        try:
            # Get current custom_project_path value
            current = self.node['custom_project_path'].value()
            
            # If empty, build default path
            if not current:
                script_path = nuke.root().name()
                if script_path:
                    node_name = self.node.name()
                    current = os.path.join(os.path.dirname(script_path), node_name, node_name + '.aep')
                    # Set as default so file browser opens here
                    self.node['custom_project_path'].setValue(normalize_path(current))
            
            # Normalize path (forward slashes)
            if current:
                current = normalize_path(current)
            
            system = platform.system()
            target_dir = None
            
            if current:
                # 始终获取文件夹：如果是文件路径则取父目录，如果是目录则使用它
                if os.path.isfile(current):
                    # 路径存在且是文件，取其父目录
                    target_dir = os.path.dirname(current)
                elif os.path.isdir(current):
                    # 路径存在且是目录，直接使用
                    target_dir = current
                else:
                    # Path does not exist: determine intent based on extension
                    if os.path.splitext(current)[1]:
                        # Has extension: treat as file path, use parent directory
                        target_dir = os.path.dirname(current)
                    else:
                        # No extension: treat as directory path
                        target_dir = current
                
                # If directory does not exist, search upward for nearest existing parent
                if target_dir and not os.path.exists(target_dir):
                    temp = target_dir
                    max_attempts = 10  # 防止无限循环
                    attempts = 0
                    while temp and attempts < max_attempts:
                        parent = os.path.dirname(temp)
                        if not parent or parent == temp:  # 到达根目录
                            break
                        if os.path.exists(parent) and os.path.isdir(parent):
                            target_dir = parent
                            break
                        temp = parent
                        attempts += 1
            
            # 最终回退方案
            if not target_dir or not os.path.exists(target_dir):
                if nuke.root().name():
                    script_dir = os.path.dirname(nuke.root().name())
                    if script_dir and os.path.exists(script_dir):
                        target_dir = script_dir
                    else:
                        target_dir = os.path.expanduser('~')
                else:
                    target_dir = os.path.expanduser('~')
            
            # 确保目标是目录
            if os.path.isfile(target_dir):
                target_dir = os.path.dirname(target_dir)
            
            # 转换为系统路径格式
            if system == 'Windows':
                target_dir = target_dir.replace('/', '\\')
            
            # 打开文件浏览器（仅打开文件夹，不选择文件）
            if system == 'Windows':
                subprocess.Popen(['explorer', target_dir])
            elif system == 'Darwin':
                subprocess.Popen(['open', target_dir])
            else:
                # Linux
                subprocess.Popen(['xdg-open', target_dir])
                
        except Exception as e:
            nuke.message("Failed to open the file browser:\n{}".format(str(e)))
            
    def _open_in_explorer(self, target_path, select_file=False):
        try:
            if not target_path:
                return
            norm_path = os.path.normpath(target_path)
            system = platform.system()
            if system == 'Windows':
                if select_file and os.path.isfile(norm_path):
                    subprocess.Popen(['explorer', '/select,', norm_path])
                else:
                    folder = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
                    subprocess.Popen(['explorer', folder])
            elif system == 'Darwin':
                if select_file and os.path.isfile(norm_path):
                    subprocess.Popen(['open', '-R', norm_path])
                else:
                    folder = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
                    subprocess.Popen(['open', folder])
            else:
                folder = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
                if folder:
                    subprocess.Popen(['xdg-open', folder])
        except Exception:
            pass

    def _build_clean_env(self):
        env = os.environ.copy()
        system = platform.system()
        if system == 'Windows':
            system_root = os.environ.get('SYSTEMROOT', r'C:\\Windows')
            path_candidates = [
                os.path.join(system_root, 'System32'),
                system_root,
                os.path.join(system_root, 'System32', 'Wbem')
            ]
            env['PATH'] = ';'.join([p for p in path_candidates if p])
        else:
            env['PATH'] = '/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin'
        for key in ['NUKE_PATH', 'PYTHONPATH', 'PYTHONHOME']:
            env.pop(key, None)
        return env

    def _apply_output_target(self, target, fallback_pattern=None, fallback_range=None):
        try:
            pattern_for_knob = fallback_pattern
            if not pattern_for_knob and target:
                if target.get('type') == 'sequence':
                    pattern_for_knob = target.get('pattern')
                else:
                    pattern_for_knob = target.get('path')
            if pattern_for_knob:
                self.node['output_sequence'].setValue(normalize_path(pattern_for_knob))

            self.node.begin()
            try:
                read_node = nuke.toNode('AE_Output')
                if not read_node:
                    return

                if target:
                    if target.get('type') == 'sequence' and target.get('pattern'):
                        read_node['file'].setValue(normalize_path(target['pattern']))
                        first = target.get('first')
                        last = target.get('last')
                        if (first is None or last is None) and fallback_range:
                            first, last = fallback_range
                        if first is not None and last is not None:
                            try:
                                read_node['first'].setValue(int(first))
                                read_node['last'].setValue(int(last))
                            except Exception:
                                pass
                    elif target.get('path'):
                        read_node['file'].setValue(normalize_path(target['path']))
                        if fallback_range:
                            try:
                                read_node['first'].setValue(int(fallback_range[0]))
                                read_node['last'].setValue(int(fallback_range[1]))
                            except Exception:
                                pass
                else:
                    if fallback_pattern:
                        read_node['file'].setValue(normalize_path(fallback_pattern))
                    if fallback_range:
                        try:
                            read_node['first'].setValue(int(fallback_range[0]))
                            read_node['last'].setValue(int(fallback_range[1]))
                        except Exception:
                            pass
                try:
                    read_node['reload'].execute()
                except Exception:
                    pass
            finally:
                self.node.end()
        except Exception:
            pass

    def _scan_output_media(self, folder, base_prefix):
        sequence_priority = ['png', 'exr', 'tif', 'tiff', 'jpg', 'jpeg']
        video_exts = ['mov', 'mp4', 'mxf', 'avi', 'webm', 'mkv']
        seq_by_ext = {}
        best_video = None
        if not os.path.exists(folder):
            return None, None
        for name in os.listdir(folder):
            full = os.path.join(folder, name)
            if os.path.isdir(full):
                continue
            seq_match = re.match(r'(.+)\.(\d+)\.(\w+)$', name)
            if seq_match:
                base_name, frame_str, ext = seq_match.groups()
                ext = ext.lower()
                digits = len(frame_str)
                frame_num = int(frame_str)
                info = seq_by_ext.setdefault((base_name, ext), {
                    'base': base_name,
                    'ext': ext,
                    'digits': digits,
                    'first': frame_num,
                    'last': frame_num
                })
                info['first'] = min(info['first'], frame_num)
                info['last'] = max(info['last'], frame_num)
                info['digits'] = max(info['digits'], digits)
                continue
            ext = os.path.splitext(name)[1][1:].lower()
            if ext in video_exts:
                best_video = {
                    'type': 'video',
                    'path': os.path.join(folder, name),
                    'ext': ext
                }
                if base_prefix and name.startswith(base_prefix):
                    break
        if seq_by_ext:
            sorted_keys = sorted(seq_by_ext.keys(), key=lambda k: (k[0] != base_prefix, sequence_priority.index(k[1]) if k[1] in sequence_priority else len(sequence_priority)))
            selected = seq_by_ext[sorted_keys[0]]
            pattern = os.path.join(folder, '{}.%0{}d.{}'.format(selected['base'], selected['digits'], selected['ext']))
            selected['pattern'] = pattern
            selected['type'] = 'sequence'
            return selected, best_video
        return None, best_video

    def _run_afterfx_inline(self, ae_exe, inline_script, aep_dir, auto_close=False):
        """Launch After Effects with an inline -s script that loads the installed JSX."""
        env = self._build_clean_env()
        try:
            proc = subprocess.Popen([ae_exe, '-s', inline_script], cwd=aep_dir, env=env)
        except Exception as exc:
            nuke.message("Failed to launch After Effects:\n{}".format(str(exc)))
            nuke.message("After Effects returned exit code {}; check the AE log.".format(proc.returncode))
            return False
        ret = proc.wait()
        if ret != 0:
            nuke.message("After Effects returned exit code {}; check the AE log.".format(ret))
            return False
        return True

    def _render_with_aerender(self, ae_exe, aep_path, comp_name, output_pattern):
        system = platform.system()
        if system == 'Windows':
            aerender_exe = ae_exe.replace('AfterFX.exe', 'aerender.exe')
            if not os.path.exists(aerender_exe):
                aerender_exe = os.path.join(os.path.dirname(ae_exe), 'aerender.exe')
        else:
            aerender_exe = ae_exe.replace('Adobe After Effects.app/Contents/MacOS/After Effects',
                                          'Adobe After Effects.app/Contents/MacOS/aerender')
        if not os.path.exists(aerender_exe):
            return False, 'aerender renderer not found.'

        env = self._build_clean_env()
        cmd_base = [aerender_exe,
                    '-project', os.path.normpath(aep_path),
                    '-comp', comp_name,
                    '-output', os.path.normpath(output_pattern)]

        logs = []
        for use_mp in [True, False]:
            cmd = list(cmd_base)
            if use_mp:
                cmd.append('-mp')
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=os.path.dirname(aep_path), env=env, text=True, encoding='utf-8', errors='replace')
            except Exception as exc:
                return False, str(exc)

            while True:
                line = proc.stdout.readline()
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                logs.append(line.rstrip())

            ret = proc.wait()
            if ret == 0:
                return True, '\n'.join(logs)
            logs.append('aerender exited with code {}'.format(ret))
        return False, '\n'.join(logs)

    def _open_in_explorer(self, target_path, select_file=False):
        try:
            if not target_path:
                return
            norm_path = os.path.normpath(target_path)
            system = platform.system()
            if system == 'Windows':
                if select_file and os.path.isfile(norm_path):
                    subprocess.Popen(['explorer', '/select,', norm_path])
                else:
                    folder = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
                    subprocess.Popen(['explorer', folder])
            elif system == 'Darwin':
                if select_file and os.path.isfile(norm_path):
                    subprocess.Popen(['open', '-R', norm_path])
                else:
                    folder = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
                    subprocess.Popen(['open', folder])
            else:
                folder = norm_path if os.path.isdir(norm_path) else os.path.dirname(norm_path)
                if folder:
                    subprocess.Popen(['xdg-open', folder])
        except Exception:
            pass

    def auto_find_ae(self):
        """Auto-locate the After Effects executable."""
        versions = find_all_ae_versions()
        
        if not versions:
            nuke.message("After Effects was not found. Set the executable path manually.")
            return
        
        # 如果只有一个版本，直接设置
        if len(versions) == 1:
            self.node['ae_executable'].setValue(normalize_path(versions[0]['path']))
            return
        
        # Multiple versions found: present selection dialog
        version_keys = []
        key_map = {}
        for idx, info in enumerate(versions):
            safe_name = ''.join(ch if ch.isalnum() else '_' for ch in info['name']) or 'AE'
            key = '{}_{}'.format(idx + 1, safe_name)
            version_keys.append(key)
            key_map[key] = info
        
        # Create version selection panel
        panel = nuke.Panel('Select After Effects Version')
        panel.addEnumerationPulldown('Version', ' '.join(version_keys))
        if not panel.show():
            return None
        selected_key = panel.value('Version')
        chosen = key_map.get(selected_key)
        if chosen:
            self.node['ae_executable'].setValue(normalize_path(chosen['path']))
    
    def create_or_edit_ae(self):
        """Create or update the AE project without triggering rendering."""
        if not self.validate_inputs():
            return

        project_info = self.create_directory_structure()

        enabled_inputs = self._collect_enabled_inputs()
        if not enabled_inputs:
            nuke.message("Enable at least one input and connect it!")
            return

        layer_ranges, global_first, global_last, outputs, _user_skipped, rendered_any = self._build_exrs_and_maybe_render(project_info, enabled_inputs)

        ae_project_path = project_info['ae_project_path']
        project_exists = os.path.exists(ae_project_path)

        if rendered_any or not project_exists:
            config_json = self._generate_jsx_for_exr_list(project_info, outputs, layer_ranges, global_first, global_last, should_render=False)
            success = self.execute_ae_script(config_json, project_info)
            if not success and project_exists:
                self._open_in_explorer(ae_project_path, select_file=True)
        else:
            if project_exists:
                self._open_in_explorer(ae_project_path, select_file=True)
                #nuke.message('所有素材已是最新，未进行重新渲染。')
            else:
                config_json = self._generate_jsx_for_exr_list(project_info, outputs, layer_ranges, global_first, global_last, should_render=False)
                if self.execute_ae_script(config_json, project_info):
                    nuke.message('AE render batch created. Run 2_Render_AE_Output in the _scripts directory to finish rendering.')
    
    def get_ae_project_path(self):
        """Return the AE project path, preferring the custom path when provided."""
        # 检查是否有自定义路径
        custom_path = self.node['custom_project_path'].value()
        if custom_path:
            # 更新隐藏的路径属性
            self.node['ae_project_path'].setValue(normalize_path(custom_path))
            project_dir = os.path.dirname(custom_path)
            node_name = os.path.splitext(os.path.basename(custom_path))[0]
            self.node['output_path'].setValue(normalize_path(os.path.join(project_dir, node_name + '_output')))
            return custom_path
        
        # 使用默认路径（基于节点名）
        script_path = nuke.root().name()
        if not script_path:
            return None
        
        node_name = self.node.name()
        project_dir = os.path.join(os.path.dirname(script_path), node_name)
        ae_project_path = os.path.join(project_dir, node_name + '.aep')
        
        # 更新隐藏的路径属性
        self.node['ae_project_path'].setValue(normalize_path(ae_project_path))
        self.node['output_path'].setValue(normalize_path(os.path.join(project_dir, node_name + '_output')))
        
        return ae_project_path
    
    def open_ae_project(self, ae_project_path):
        """Open an existing After Effects project."""
        ae_exe = self.node['ae_executable'].value()

        if not ae_exe or not os.path.exists(ae_exe):
            nuke.message("Set the After Effects executable before opening the project.")
            return

        try:
            subprocess.Popen([ae_exe, ae_project_path])
            nuke.message("Opening the After Effects project...")
        except Exception as e:
            nuke.message("Failed to open the After Effects project:\n{}".format(str(e)))

    def execute(self):
        """Legacy entry point that matches create_or_edit_ae without triggering rendering."""
        try:
            self.create_or_edit_ae()
        except Exception as e:
            import traceback
            error_msg = "Execution failed:\n{}\n\n{}".format(str(e), traceback.format_exc())
            nuke.message(error_msg)

    def validate_inputs(self):
        """Validate node inputs before proceeding."""
        has_input = any(self.node.input(i) for i in range(self.node.inputs()))
        if not has_input:
            nuke.message("Connect at least one input before running AE Bridge.")
            return False

        script_path = nuke.root().name()
        if not script_path:
            nuke.message("Save the Nuke script before running AE Bridge.")
            return False

        ae_exe = self.node['ae_executable'].value()
        if not ae_exe or not os.path.exists(ae_exe):
            nuke.message("Set the After Effects executable or use 'Auto Find AE'.")
            return False

        return True
        
    def create_directory_structure(self):
        """Create the After Effects project folder structure."""
        ae_project_path = self.get_ae_project_path()

        if not ae_project_path:
            raise Exception("Save the Nuke script first.")
        
        project_dir = os.path.dirname(ae_project_path)
        node_name = self.node.name()
        
        # 创建标准 AE 目录结构
        footage_dir = os.path.join(project_dir, node_name + '_footage')
        output_dir = os.path.join(project_dir, node_name + '_output')
        
        # 创建目录
        for d in [project_dir, footage_dir, output_dir]:
            if not os.path.exists(d):
                os.makedirs(d)
        
        # 获取 Nuke 合成设置
        # Collect color management settings from Nuke
        try:
            # Get Nuke's working colorspace
            nuke_working_space = nuke.root()['workingSpaceLUT'].value() if 'workingSpaceLUT' in nuke.root().knobs() else 'linear'
        except Exception:
            nuke_working_space = 'linear'
        
        try:
            # Get output transform setting from the node
            nuke_output_transform = self.node['output_transform'].value() if self.node.knob('output_transform') else ''
        except Exception:
            nuke_output_transform = ''
        
        try:
            # Get ACES compliant setting
            aces_compliant = self.node['write_aces_compliant'].value() if self.node.knob('write_aces_compliant') else False
        except Exception:
            aces_compliant = False
        
        # Determine the colorspace for EXR files
        # For linear workflow, EXR files are typically in scene_linear
        nuke_colorspace = 'scene_linear'
        if nuke_output_transform:
            nuke_colorspace = nuke_output_transform
        
        project_info = {
            'ae_project_path': ae_project_path,
            'project_dir': project_dir,
            'project_name': node_name,
            'footage_dir': footage_dir,
            'output_dir': output_dir,
            'comp_name': node_name,
            'frame_rate': nuke.root()['fps'].value(),
            'width': int(self.node.format().width()),
            'height': int(self.node.format().height()),
            # Color management settings
            'nuke_colorspace': nuke_colorspace,
            'nuke_working_space': nuke_working_space,
            'nuke_output_transform': nuke_output_transform,
            'aces_compliant': aces_compliant,
        }
        
        return project_info
        
    # ---------------- 渲染与合并多层 EXR（Nuke 侧） ----------------
    def _collect_enabled_inputs(self):
        items = []
        total = self.node.inputs()
        for i in range(total):
            src = self.node.input(i)
            if src is None:
                continue
            en_kn = self.node.knob('in{}_enable'.format(i))
            ch_kn = self.node.knob('in{}_channels'.format(i))
            nm_kn = self.node.knob('in{}_layer'.format(i))
            enabled = (en_kn is None) or bool(en_kn.value())
            if not enabled:
                continue
            layer_name = _sanitize_layer_name(nm_kn.value() if nm_kn else src.name())
            # 解析空格分隔的通道基础名称 (如果为空则默认为 rgba)
            ch_str = (ch_kn.value() if ch_kn else '').strip()
            if not ch_str:
                bases = ['rgba']
            else:
                bases = [c for c in ch_str.split() if c]
            # 扩展到分量通道: find all channels with base prefix from source node
            try:
                all_ch = list(src.channels())
            except Exception:
                all_ch = []
            expanded = []
            ac_set = set(all_ch)
            seen_channels = set()  # 跟踪已添加的通道以避免重复
            
            for base in bases:
                # 方法1：优先尝试常见的通道分量名称
                found_any = False
                for comp in ['red', 'green', 'blue', 'alpha', 'R', 'G', 'B', 'A', 'X', 'Y', 'Z']:
                    name = base + '.' + comp
                    if name in ac_set and name not in seen_channels:
                        expanded.append(name)
                        seen_channels.add(name)
                        found_any = True
                
                # 方法2：包含所有具有此基础前缀的通道
                for ch in all_ch:
                    if ch.startswith(base + '.') and ch not in seen_channels:
                        expanded.append(ch)
                        seen_channels.add(ch)
                        found_any = True
                
                # 方法3：如果未找到带前缀的通道,检查基础名称本身是否为通道
                if not found_any and base in ac_set:
                    expanded.append(base)
                    seen_channels.add(base)
            
            # 回退策略：如果没有匹配到任何通道,仅使用 rgba
            if not expanded:
                # 尝试获取 rgba(如果存在)
                rgba_channels = ['rgba.red', 'rgba.green', 'rgba.blue', 'rgba.alpha']
                for ch in rgba_channels:
                    if ch in ac_set:
                        expanded.append(ch)
                
                # 如果 rgba 不存在,使用第一个可用图层
                if not expanded and all_ch:
                    # 仅获取第一个图层
                    first_layer = None
                    for ch in all_ch:
                        if '.' in ch:
                            first_layer = ch.split('.')[0]
                            break
                    if first_layer:
                        for ch in all_ch:
                            if ch.startswith(first_layer + '.'):
                                expanded.append(ch)
            
            items.append({'index': i, 'node': src, 'layer': layer_name, 'channels': expanded, 'node_name': src.name()})
        return items

    def _get_input_frame_range(self, input_node):
        root_first = int(nuke.root()['first_frame'].value())
        root_last = int(nuke.root()['last_frame'].value())

        node_first = root_first
        node_last = root_last

        # Use node-provided frame range if available
        try:
            fr = input_node.frameRange()
            if fr:
                node_first = int(fr.first())
                node_last = int(fr.last())
        except Exception:
            pass

        # Read nodes can provide first/last directly
        try:
            if input_node.Class() == 'Read' and input_node.knob('first') and input_node.knob('last'):
                node_first = int(input_node['first'].value())
                node_last = int(input_node['last'].value())
        except Exception:
            pass

        # Clamp to global range
        node_first = max(root_first, node_first)
        node_last = min(root_last, node_last)
        if node_first > node_last:
            node_first, node_last = root_first, root_last

        return node_first, node_last

    def _compute_missing_ranges(self, file_pattern, first_frame, last_frame):
        def exists_at(frame):
            path = file_pattern.replace('%04d', str(frame).zfill(4))
            return os.path.exists(path)
        missing = []
        in_range = False
        start = None
        for f in range(first_frame, last_frame + 1):
            if not exists_at(f):
                if not in_range:
                    in_range = True
                    start = f
            else:
                if in_range:
                    missing.append((start, f - 1))
                    in_range = False
        if in_range:
            missing.append((start, last_frame))
        return missing

    def _compute_extra_frames(self, file_pattern, first_frame, last_frame):
        """
        检测文件序列中多余的帧（超出指定帧范围的帧）

        """
        import re
        
        extra_frames = []
        
        try:
            # 从 file_pattern 提取目录和文件名模式
            # 例如: "D:/footage/layer/layer.%04d.exr"
            directory = os.path.dirname(file_pattern)
            basename = os.path.basename(file_pattern)
            
            # 将 %04d 转换为正则表达式模式来匹配帧号
            # layer.%04d.exr -> layer\.(\d{4})\.exr
            pattern_for_regex = basename.replace('.', r'\.').replace('%04d', r'(\d{4})')
            regex = re.compile(pattern_for_regex)
            
            # 检查目录是否存在
            if not os.path.exists(directory):
                return [], ""
            
            # 扫描目录中的所有文件
            for filename in os.listdir(directory):
                match = regex.match(filename)
                if match:
                    # 提取帧号
                    frame_num = int(match.group(1))
                    # 检查帧号是否在有效范围外
                    if frame_num < first_frame or frame_num > last_frame:
                        extra_frames.append(frame_num)
        
        except Exception as e:
            # 如果出错，返回空列表
            return [], ""
        
        # 将多余的帧号转换为范围字符串
        if not extra_frames:
            return [], ""
        
        # 排序
        extra_frames.sort()
        
        # 构建范围列表
        ranges = []
        start = extra_frames[0]
        prev = extra_frames[0]
        
        for frame in extra_frames[1:]:
            if frame == prev + 1:
                # 连续帧
                prev = frame
            else:
                # 范围断开
                if start == prev:
                    ranges.append(str(start))
                else:
                    ranges.append('{}-{}'.format(start, prev))
                start = frame
                prev = frame
        
        # 添加最后一个范围
        if start == prev:
            ranges.append(str(start))
        else:
            ranges.append('{}-{}'.format(start, prev))
        
        ranges_str = ', '.join(ranges)
        
        return extra_frames, ranges_str

    def _delete_extra_frames(self, file_pattern, extra_frames):
        """
        删除多余的帧文件
        
        """
        success_count = 0
        failed_count = 0
        
        for frame in extra_frames:
            try:
                file_path = file_pattern.replace('%04d', str(frame).zfill(4))
                if os.path.exists(file_path):
                    os.remove(file_path)
                    success_count += 1
            except Exception as e:
                failed_count += 1
        
        return success_count, failed_count

    def _sequence_any_exists(self, file_pattern, first_frame, last_frame):
        try:
            for f in range(first_frame, last_frame + 1):
                path = file_pattern.replace('%04d', str(f).zfill(4))
                if os.path.exists(path):
                    return True
        except Exception:
            pass
        return False

    def _detect_input_colorspace(self, input_node):
        try:
            if input_node.Class() == 'Read' and input_node.knob('colorspace'):
                return _normalize_colorspace_name(input_node['colorspace'].value())
        except Exception:
            pass
        try:
            # Root 工作色彩空间（尽力而为）
            ws = nuke.root()['workingSpaceLUT'].value()
            return _normalize_colorspace_name(ws)
        except Exception:
            return 'linear'

    def _insert_colorspace(self, src_node, target_space):
        """Insert a colorspace conversion node when possible."""
        try:
            # Skip colorspace conversion if target is empty or 'linear' (already linear)
            if not target_space or target_space.lower() == 'linear':
                return src_node
                
            if hasattr(nuke, 'nodes'):
                cs_node = nuke.nodes.Colorspace(name='AEBridge_TEMP_Colorspace')
                # Use safe enum setting for colorspaces
                try:
                    # Try to detect available colorspace values
                    available_in = list(cs_node['colorspace_in'].values()) if hasattr(cs_node['colorspace_in'], 'values') else []
                    available_out = list(cs_node['colorspace_out'].values()) if hasattr(cs_node['colorspace_out'], 'values') else []

                    # Set input colorspace safely
                    if available_in:
                        # Look for linear variants
                        for variant in ['scene_linear', 'linear', 'default', 'default (linear)']:
                            if variant in available_in:
                                cs_node['colorspace_in'].setValue(variant)
                                break

                    # Set output colorspace safely
                    if target_space in available_out:
                        cs_node['colorspace_out'].setValue(target_space)
                    else:
                        # Target not available, skip conversion
                        try:
                            nuke.delete(cs_node)
                        except Exception:
                            pass
                        return src_node
                except Exception:
                    # If setting fails, skip conversion
                    try:
                        nuke.delete(cs_node)
                    except Exception:
                        pass
                    return src_node
                
                cs_node.setInput(0, src_node)
                return cs_node
        except Exception:
            pass
        return src_node

    def _remap_layer_to_rgba(self, src_node, layer_base):
        """Map an arbitrary layer's components onto RGBA using Copy nodes.
        Handles common cases: rgba/RGB, XY(Z), UV, Z/depth, single-channel masks.
        Returns the tail node producing correct RGBA."""
        try:
            try:
                all_channels = list(src_node.channels())
            except Exception:
                all_channels = []
            # Build component lookup: suffix(lower) -> full channel name
            comp_lookup = {}
            prefix = layer_base + '.'
            plen = len(prefix)
            for ch in all_channels:
                if ch.startswith(prefix) and len(ch) > plen:
                    comp_lookup[ch[plen:].lower()] = ch
            
            def pick(*candidates):
                for key in candidates:
                    val = comp_lookup.get(key)
                    if val:
                        return val
                return None
            
            # Preferred direct RGB(A)
            r = pick('red', 'r')
            g = pick('green', 'g')
            b = pick('blue', 'b')
            a = pick('alpha', 'a')
            
            # Vector-style X/Y/Z mapping when RGB does not exist
            if not (r and g and b):
                rx = pick('x')
                gy = pick('y')
                bz = pick('z')
                if rx or gy or bz:
                    r = r or rx
                    g = g or gy or rx or bz
                    b = b or bz or rx or gy
            
            # Depth-style single channel (Z/depth/distance)
            if not (r and g and b):
                depth_ch = pick('z', 'depth', 'distance')
                if depth_ch:
                    r = r or depth_ch
                    g = g or depth_ch
                    b = b or depth_ch
            
            # Single channel layer: replicate to RGB
            if not (r and g and b):
                # Try any remaining single component
                single = None
                for k, v in comp_lookup.items():
                    single = v
                    break
                if single:
                    r = r or single
                    g = g or single
                    b = b or single
                    if a is None and (single.endswith('.alpha') or single.endswith('.a') or 'mask' in single.lower()):
                        a = single
            
            # If nothing could be mapped, just return the source to avoid breaking
            if not (r or g or b or a):
                return src_node
            
            # Chain Copy nodes to map channels onto RGBA
            out = src_node
            def apply_copy(from_ch, to_ch, _in):
                if not from_ch or not to_ch:
                    return _in
                cp = nuke.nodes.Copy(name='AEBridge_TEMP_Copy')
                cp.setInput(0, _in)
                cp.setInput(1, src_node)
                try:
                    cp['from0'].setValue(from_ch)
                    cp['to0'].setValue(to_ch)
                except Exception:
                    pass
                return cp
            
            if r:
                out = apply_copy(r, 'rgba.red', out)
            if g:
                out = apply_copy(g, 'rgba.green', out)
            if b:
                out = apply_copy(b, 'rgba.blue', out)
            if a:
                out = apply_copy(a, 'rgba.alpha', out)
            
            return out
        except Exception:
            return src_node

    def _build_exrs_and_maybe_render(self, project_info, enabled_inputs):
        """
        导出 EXR 序列
    
        . 扫描每个输入，检测哪些帧缺失、哪些帧多余
        . 询问用户是否需要修补（渲染缺失帧、删除多余帧）
        . 渲染所需的 EXR 文件，支持多通道、色彩管理等

        - 智能检测文件状态，避免重复渲染（节省时间）
        - 支持多通道输出，需要用 Remove 节点过滤不需要的图层
        - 处理 ACES 工作流、输出转换等色彩管理问题
        - 提供强制渲染选项，跳过所有检查
        
        返回：
        (layer_ranges, global_first, global_last, outputs, user_skipped, rendered_any)
        """
        root_first = int(nuke.root()['first_frame'].value())
        root_last = int(nuke.root()['last_frame'].value())

        layer_ranges = []
        outputs = []
        global_first = root_first
        global_last = root_last
        rendered_any = False
        for item in enabled_inputs:
            f, l = self._get_input_frame_range(item['node'])
            f = max(root_first, f)
            l = min(root_last, l)
            layer_ranges.append({
                'layer': item['layer'],
                'channels': item['channels'],
                'first': f,
                'last': l,
                'index': item.get('index', -1),
                'node_name': item.get('node_name'),
                'pattern': None
            })
        # 对每个输入分别处理（每个输入导出一个多通道 EXR）
        user_skipped = False
        for item, rng in zip(enabled_inputs, layer_ranges):
            layer_name = item['layer']
            src = item['node']
            channels = item['channels']
            first, last = rng['first'], rng['last']
            # 每个输入独立素材文件夹：<footage_dir>/<layer>/<layer>.%04d.exr
            layer_dir = os.path.join(project_info['footage_dir'], layer_name)
            try:
                if not os.path.exists(layer_dir):
                    os.makedirs(layer_dir)
            except Exception:
                pass
            exr_pattern = normalize_path(os.path.join(layer_dir, '{}.%04d.exr'.format(layer_name)))
            
            # 检查是否启用强制渲染
            force_render = False
            try:
                force_render = self.node['force_render'].value() if self.node.knob('force_render') else False
            except Exception:
                pass
            
            if not force_render:
                # 正常模式：检查文件是否存在
                missing_ranges = self._compute_missing_ranges(exr_pattern, first, last)
                exists_any = self._sequence_any_exists(exr_pattern, first, last)
                # 同时检测多余的帧
                extra_frames_list, extra_ranges_str = self._compute_extra_frames(exr_pattern, first, last)
            else:
                # 强制渲染模式：跳过文件检查，假设所有帧都缺失
                missing_ranges = [(first, last)]
                exists_any = False
                extra_frames_list, extra_ranges_str = [], ""
            
            rng['pattern'] = exr_pattern

            # 如果文件齐全且没有多余帧，直接使用现有文件
            if exists_any and not missing_ranges and not extra_frames_list:
                outputs.append({'layer': layer_name, 'pattern': exr_pattern, 'first': first, 'last': last, 'index': rng.get('index', -1), 'channels': channels, 'node_name': rng.get('node_name')})
                continue

            # 如果有缺失或多余的帧，询问用户是否修补
            if exists_any and (missing_ranges or extra_frames_list):
                # 构建提示信息
                message_parts = []
                if missing_ranges:
                    missing_desc = ', '.join(['{}-{}'.format(lo, hi) if lo != hi else str(lo) for lo, hi in missing_ranges])
                    message_parts.append('Missing frames: {}'.format(missing_desc))
                if extra_frames_list:
                    message_parts.append('Extra frames: {}'.format(extra_ranges_str))
                
                full_message = 'Footage "{}":\n{}\n\nPatch missing frames and delete extra frames?'.format(
                    layer_name, 
                    '\n'.join(message_parts)
                )
                
                if not nuke.ask(full_message):
                    user_skipped = True
                    outputs.append({'layer': layer_name, 'pattern': exr_pattern, 'first': first, 'last': last, 'index': rng.get('index', -1), 'channels': channels, 'node_name': rng.get('node_name')})
                    continue
                else:
                    # 用户点击了 Yes，删除多余的帧
                    if extra_frames_list:
                        success, failed = self._delete_extra_frames(exr_pattern, extra_frames_list)
                        #if success > 0:
                            #nuke.message('Deleted {} extra frame(s) for footage "{}".'.format(success, layer_name))
                        if failed > 0:
                            nuke.warning('Failed to delete {} frame(s) for footage "{}".'.format(failed, layer_name))

            # 从用户选择中提取唯一的图层名称
            selected_layers = []
            if channels:
                seen = set()
                for ch in channels:
                    if '.' in ch:
                        layer_key = ch.split('.')[0]
                        if layer_key not in seen:
                            selected_layers.append(layer_key)
                            seen.add(layer_key)
            
            # 关键回退策略：如果没有从通道中提取到图层,仅使用 rgba 作为回退
            # 注意：不要回退到所有图层,这会违背用户选择的目的
            if not selected_layers:
                selected_layers = ['rgba']
            
            # 多通道 EXR 渲染模式：保留用户选择的图层并写入所有通道
            sub_layer_dir = os.path.join(project_info['footage_dir'], layer_name)
            try:
                if not os.path.exists(sub_layer_dir):
                    os.makedirs(sub_layer_dir)
            except Exception:
                pass
            sub_exr_pattern = normalize_path(os.path.join(sub_layer_dir, '{}.%04d.exr'.format(layer_name)))
            
            if not force_render:
                # 正常模式：检查文件是否存在
                sub_missing_ranges = self._compute_missing_ranges(sub_exr_pattern, first, last)
                sub_exists_any = self._sequence_any_exists(sub_exr_pattern, first, last)
                # 同时检测多余的帧
                sub_extra_frames_list, sub_extra_ranges_str = self._compute_extra_frames(sub_exr_pattern, first, last)
            else:
                # 强制渲染模式：跳过文件检查，假设所有帧都缺失
                sub_missing_ranges = [(first, last)]
                sub_exists_any = False
                sub_extra_frames_list, sub_extra_ranges_str = [], ""

            # 如果文件齐全且没有多余帧，直接使用现有文件
            if sub_exists_any and not sub_missing_ranges and not sub_extra_frames_list:
                outputs.append({'layer': layer_name, 'pattern': sub_exr_pattern, 'first': first, 'last': last, 'index': rng.get('index', -1), 'channels': list(selected_layers), 'node_name': rng.get('node_name')})
                continue

            # 如果有缺失或多余的帧，询问用户是否修补
            # 重要：如果sub_exr_pattern和exr_pattern相同,说明已经在上面处理过了,避免重复弹窗
            if sub_exists_any and (sub_missing_ranges or sub_extra_frames_list):
                # 检查是否和上面的路径相同(避免重复弹窗)
                if normalize_path(sub_exr_pattern) != normalize_path(exr_pattern):
                    # 构建提示信息
                    sub_message_parts = []
                    if sub_missing_ranges:
                        sub_missing_desc = ', '.join(['{}-{}'.format(lo, hi) if lo != hi else str(lo) for lo, hi in sub_missing_ranges])
                        sub_message_parts.append('Missing frames: {}'.format(sub_missing_desc))
                    if sub_extra_frames_list:
                        sub_message_parts.append('Extra frames: {}'.format(sub_extra_ranges_str))
                    
                    sub_full_message = 'Footage "{}":\n{}\n\nPatch missing frames and delete extra frames?'.format(
                        layer_name, 
                        '\n'.join(sub_message_parts)
                    )
                    
                    if not nuke.ask(sub_full_message):
                        user_skipped = True
                        outputs.append({'layer': layer_name, 'pattern': sub_exr_pattern, 'first': first, 'last': last, 'index': rng.get('index', -1), 'channels': list(selected_layers), 'node_name': rng.get('node_name')})
                        continue
                    else:
                        # 用户点击了 Yes，删除多余的帧
                        if sub_extra_frames_list:
                            sub_success, sub_failed = self._delete_extra_frames(sub_exr_pattern, sub_extra_frames_list)
                            if sub_success > 0:
                                nuke.message('Deleted {} extra frame(s) for footage "{}".'.format(sub_success, layer_name))
                            if sub_failed > 0:
                                nuke.warning('Failed to delete {} frame(s) for footage "{}".'.format(sub_failed, layer_name))

            try:
                # Convert colorspace
                target_cs = self.node['exr_colorspace'].value() if self.node.knob('exr_colorspace') else None
                if target_cs and target_cs.lower() != 'linear':
                    stream = self._insert_colorspace(src, target_cs)
                else:
                    stream = src

                # 枚举上游流中的可用图层和通道 (保留顺序).
                available_layers = []
                seen_available = set()
                layer_to_channels = {}
                try:
                    all_channels = stream.channels()
                    
                    for ch_name in all_channels:
                        if '.' not in ch_name:
                            # 处理没有图层前缀的通道 (e.g., "alpha", "depth")
                            # 映射到 "rgba" layer if they're standard channels
                            if ch_name in ['red', 'green', 'blue', 'alpha']:
                                layer_label = 'rgba'
                                full_ch_name = 'rgba.{}'.format(ch_name)
                            else:
                                # 跳过没有正确图层前缀的通道
                                continue
                        else:
                            layer_label = ch_name.split('.')[0]
                            full_ch_name = ch_name
                        
                        if layer_label not in seen_available:
                            seen_available.add(layer_label)
                            available_layers.append(layer_label)
                            layer_to_channels[layer_label] = []
                        
                        layer_to_channels[layer_label].append(full_ch_name)
                    
                except Exception as e:
                    pass

                # 将用户选择与可用图层求交集
                keep_layers = [ly for ly in selected_layers if ly in seen_available]

                # 如果没有选择有效的图层,跳过此组合
                if not keep_layers:
                    continue

                # 收集我们要保留的图层的所有通道
                all_keep_channels: List[str] = []
                for ly in keep_layers:
                    channels_for_layer = layer_to_channels.get(ly, [])
                    if channels_for_layer:
                        all_keep_channels.extend(channels_for_layer)
                

                if not all_keep_channels:
                    continue

                current_node = stream
                
                # Get all layers in the stream
                all_layers_in_stream = available_layers
                layers_to_remove = [ly for ly in all_layers_in_stream if ly not in keep_layers]
                
                
                # 链接 Remove 节点,每个要删除的图层一个
                for layer_to_remove in layers_to_remove:
                    remove_node = nuke.nodes.Remove(name='AEBridge_TEMP_Remove_{}'.format(layer_to_remove))
                    remove_node.setInput(0, current_node)
                    
                    # 设置操作为 'remove'
                    try:
                        _set_enum_knob_safely(remove_node['operation'], ['remove'])
                    except Exception as e:
                        pass
                    
                    # 设置要删除的单个图层
                    try:
                        remove_node['channels'].setValue(layer_to_remove)
                        actual_value = remove_node['channels'].value()
                    except Exception as e:
                        pass
                    
                    current_node = remove_node
                
                
                # 创建连接到 the final Remove node
                write_node = nuke.nodes.Write(name='AEBridge_TEMP_Write')
                write_node.setInput(0, current_node)
                write_node['file'].setValue(sub_exr_pattern)
                write_node['file_type'].setValue('exr')
                
                # 设置 Write 节点写入 'all' remaining channels
                try:
                    if hasattr(write_node['channels'], 'fromScript'):
                        write_node['channels'].fromScript('all')
                    else:
                        write_node['channels'].setValue('all')
                except Exception as e:
                    pass

                # 配置 EXR 设置
                try:
                    comp_ui = self.node['exr_compression'].value() if self.node.knob('exr_compression') else 'zip'
                    if comp_ui == 'none':
                        _set_enum_knob_safely(write_node['compression'], ['none', 'None', 'no compression', 'No compression'])
                    elif comp_ui == 'rle':
                        _set_enum_knob_safely(write_node['compression'], ['RLE', 'rle'])
                    elif comp_ui == 'zip':
                        _set_enum_knob_safely(write_node['compression'], ['Zip (16 scanlines)', 'zip (16 scanlines)', 'Zip', 'zip'])
                    elif comp_ui == 'zips':
                        _set_enum_knob_safely(write_node['compression'], ['Zip (1 scanline)', 'zip (1 scanline)', 'Zips', 'zips'])
                    elif comp_ui == 'piz':
                        _set_enum_knob_safely(write_node['compression'], ['Piz', 'piz', 'PIZ'])
                    elif comp_ui == 'pxr24':
                        _set_enum_knob_safely(write_node['compression'], ['PXR24', 'pxr24'])
                    elif comp_ui == 'b44':
                        _set_enum_knob_safely(write_node['compression'], ['B44', 'b44'])
                    elif comp_ui == 'b44a':
                        _set_enum_knob_safely(write_node['compression'], ['B44A', 'b44a'])
                    elif comp_ui == 'dwaa':
                        _set_enum_knob_safely(write_node['compression'], ['DWAA', 'dwaa'])
                    elif comp_ui == 'dwab':
                        _set_enum_knob_safely(write_node['compression'], ['DWAB', 'dwab'])
                    else:
                        _set_enum_knob_safely(write_node['compression'], [comp_ui])
                except Exception:
                    pass

                try:
                    depth_ui = self.node['exr_bitdepth'].value() if self.node.knob('exr_bitdepth') else '16-bit'
                    if depth_ui == '16-bit':
                        _set_enum_knob_safely(write_node['datatype'], ['16 bit half', '16-bit half', 'half', '16'])
                    else:
                        _set_enum_knob_safely(write_node['datatype'], ['32 bit float', '32-bit float', 'float', '32'])
                except Exception:
                    pass

                # Apply Output Transform (colorspace)
                try:
                    if 'colorspace' in write_node.knobs():
                        # Check if user has specified an Output Transform
                        output_transform = None
                        if self.node.knob('output_transform'):
                            output_transform = self.node['output_transform'].value()
                        
                        if output_transform:
                            # User specified colorspace
                            available = list(write_node['colorspace'].values()) if hasattr(write_node['colorspace'], 'values') else []
                            if output_transform in available:
                                write_node['colorspace'].setValue(output_transform)
                        else:
                            # Default to linear (exclude 'default')
                            available = list(write_node['colorspace'].values()) if hasattr(write_node['colorspace'], 'values') else []
                            for variant in ['scene_linear', 'linear', 'Linear']:
                                if variant in available:
                                    write_node['colorspace'].setValue(variant)
                                    break
                except Exception:
                    pass
                
                # Apply ACES compliant EXR settings
                try:
                    aces_compliant = False
                    if self.node.knob('write_aces_compliant'):
                        aces_compliant = self.node['write_aces_compliant'].value()
                    
                    if aces_compliant:
                        # When ACES compliant is enabled, override compression and bit depth
                        # Set compression to dwaa (ACES standard)
                        if 'compression' in write_node.knobs():
                            _set_enum_knob_safely(write_node['compression'], ['DWAA', 'dwaa'])
                        
                        # Set bit depth to 16-bit (ACES standard)
                        if 'datatype' in write_node.knobs():
                            _set_enum_knob_safely(write_node['datatype'], ['16 bit half', '16-bit half', 'half', '16'])
                        
                        # Set ACES metadata if available
                        if 'standard_layer_name_format' in write_node.knobs():
                            try:
                                write_node['standard_layer_name_format'].setValue(True)
                            except Exception:
                                pass
                except Exception:
                    pass

                try:
                    if 'metadata' in write_node.knobs():
                        _set_enum_knob_safely(write_node['metadata'], ['all', 'metadata'])
                except Exception:
                    pass

                try:
                    if 'interleave' in write_node.knobs():
                        _set_enum_knob_safely(write_node['interleave'], ['channels', 'layers', 'views'])
                except Exception:
                    pass

                try:
                    if 'create_directories' in write_node.knobs():
                        write_node['create_directories'].setValue(True)
                except Exception:
                    pass

                render_ranges = sub_missing_ranges or [(first, last)]
                
                for lo, hi in render_ranges:
                    try:
                        # 检查通道 the final node output before rendering
                        try:
                            final_output_channels = list(current_node.channels())
                        except Exception as ex:
                            pass
                        
                        # Check Write node channels setting
                        write_channels_value = write_node['channels'].value()
                        
                        # Clean up any existing .tmp files and target files to prevent "Can't rename .tmp to final" error
                        # This can happen when using certain Output Transform colorspaces
                        try:
                            for frame_num in range(int(lo), int(hi) + 1):
                                # Get the actual file path for this frame
                                frame_path = sub_exr_pattern.replace('[#####]', str(frame_num).zfill(5))
                                frame_path = frame_path.replace('%05d', str(frame_num).zfill(5))
                                
                                # Remove .tmp file if exists
                                tmp_path = frame_path + '.tmp'
                                if os.path.exists(tmp_path):
                                    try:
                                        os.remove(tmp_path)
                                    except Exception:
                                        pass
                                
                                # Remove final file if exists and force render is enabled
                                # OR if the file seems corrupted (very small size)
                                if os.path.exists(frame_path):
                                    try:
                                        file_size = os.path.getsize(frame_path)
                                        force_render = False
                                        if self.node.knob('force_render'):
                                            force_render = self.node['force_render'].value()
                                        
                                        # Remove if force render OR file is suspiciously small (< 1KB = likely corrupt)
                                        if force_render or file_size < 1024:
                                            os.remove(frame_path)
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        
                        nuke.execute(write_node, int(lo), int(hi))
                        rendered_any = True
                    except Exception as e:
                        raise
            finally:
                for temp_node in list(nuke.allNodes()):
                    try:
                        if temp_node.name().startswith('AEBridge_TEMP_'):
                            nuke.delete(temp_node)
                    except Exception:
                        pass

            outputs.append({'layer': layer_name, 'pattern': sub_exr_pattern, 'first': first, 'last': last, 'index': rng.get('index', -1), 'channels': list(keep_layers), 'node_name': rng.get('node_name')})
        # 检测并提示删除未使用的素材文件夹
        try:
            # 构建预期的文件夹名称 (包括按图层拆分的)
            expected_folders = set()
            for out in outputs:
                folder_name = os.path.basename(os.path.dirname(out['pattern']))
                expected_folders.add(folder_name)
            
            exr_dir = project_info['footage_dir']
            if os.path.exists(exr_dir):
                extra_dirs = []
                for name in os.listdir(exr_dir):
                    layer_path = os.path.join(exr_dir, name)
                    if os.path.isdir(layer_path) and name not in expected_folders:
                        extra_dirs.append(layer_path)
                if extra_dirs:
                    if nuke.ask('Unused footage folders detected:\n{}\n\nDelete them?'.format('\n'.join([os.path.basename(d) for d in extra_dirs]))):
                        import shutil
                        for d in extra_dirs:
                            try:
                                shutil.rmtree(d)
                            except Exception:
                                pass
        except Exception:
            pass

        return layer_ranges, global_first, global_last, outputs, user_skipped, rendered_any
        
    def _generate_jsx_for_exr_list(self, project_info, outputs, layer_ranges, global_first, global_last, should_render):
        """Delegate to the ae_jsx module to build the JSX file."""
        if _ae_jsx is None:
            raise RuntimeError('Missing module AEBridge/ae_jsx.py; cannot generate JSX.')
        return _ae_jsx.generate_jsx_for_exr_list(project_info, outputs, layer_ranges, global_first, global_last, should_render)
    
    def execute_ae_script(self, config_json_content, project_info, auto_run=False, auto_close=False):
        """Launch After Effects with an inline script that loads the startup JSX."""
        ae_exe = self.find_ae_executable()
        if not ae_exe:
            nuke.message("After Effects executable not found. Ensure After Effects is installed.")
            return False

        target_aep = project_info.get('ae_project_path') if isinstance(project_info, dict) else None
        if not target_aep:
            nuke.message("Unable to determine the AEP file path.")
            return False

        aep_dir = os.path.dirname(target_aep)
        scripts_dir = os.path.join(aep_dir, '_scripts')
        if not os.path.exists(scripts_dir):
            os.makedirs(scripts_dir)

        auto_close = bool(auto_close or auto_run)

        try:
            success, jsx_path, msg = _ae_jsx.install_startup_jsx(ae_exe)
            if not success:
                nuke.message("Error: " + msg)
                return False
        except Exception as e:
            nuke.message("Failed to install AE startup script:\n{}".format(str(e)))
            return False

        config_path = os.path.join(scripts_dir, 'AEBridge_config.json')
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                f.write(config_json_content)
        except Exception as e:
            nuke.message("Failed to write configuration file:\n{}".format(str(e)))
            return False

        config_path_normalized = normalize_path(config_path)
        exit_flag = 'true' if auto_close else 'false'
        auto_flag = 'true' if auto_close else 'false'
        inline_script_base = 'app.exitAfterLaunchAndEval = {exit}; var AEBRIDGE_AUTO_CLOSE = {auto}; var AEBRIDGE_CONFIG_PATH="{config}"; $.evalFile("{jsx}");'
        if not auto_close:
            inline_script_base += ' app.activate();'
        inline_script_win = inline_script_base.format(
            exit=exit_flag,
            auto=auto_flag,
            config=config_path_normalized.replace('"', '\"'),
            jsx=normalize_path(jsx_path).replace('\\', '/').replace('"', '\"')
        )
        inline_script_mac = inline_script_base.format(
            exit=exit_flag,
            auto=auto_flag,
            config=config_path_normalized.replace('"', '\"'),
            jsx=normalize_path(jsx_path).replace('\\', '/')
        )

        try:
            if platform.system() == 'Windows':
                launcher_script = os.path.join(scripts_dir, '1_Create_AE_Project.bat')

                ae_exe_win = os.path.normpath(ae_exe)
                aep_dir_win = os.path.normpath(aep_dir)

                bat_content = '''@echo off
chcp 65001 >nul

echo ==================================
echo   AEBridge - Create or Update Project
echo ==================================

echo.
set "PATH=%SYSTEMROOT%\System32;%SYSTEMROOT%;%SYSTEMROOT%\System32\Wbem"
set NUKE_PATH=
set PYTHONPATH=
set PYTHONHOME=

cd /d "{aep_dir}"

echo Launching After Effects...
echo.

start /min "" "{ae_exe}" -s "{inline_script}"

timeout /t 2 /nobreak >nul

exit
'''.format(
                    ae_exe=ae_exe_win,
                    inline_script=inline_script_win,
                    aep_dir=aep_dir_win
                )

                with open(launcher_script, 'w', encoding='utf-8-sig') as f:
                    f.write(bat_content)

                if not auto_run:
                    subprocess.Popen(['explorer', '/select,', os.path.normpath(launcher_script)])
                    #nuke.message("✅ 请运行选中的批处理文件以创建 AE 工程")
                else:
                    if not self._run_afterfx_inline(ae_exe_win, inline_script_win, aep_dir_win, auto_close):
                        return False

            else:  # macOS / Linux
                launcher_script = os.path.join(scripts_dir, '1_Create_AE_Project.sh')

                sh_content = '''#!/bin/bash

export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"
unset NUKE_PATH PYTHONPATH PYTHONHOME

cd "{aep_dir}"

echo "Starting After Effects..."
echo ""

nohup "{ae_exe}" -s '{inline_script}' >/dev/null 2>&1 &

sleep 2

exit 0
'''.format(
                    ae_exe=ae_exe,
                    inline_script=inline_script_mac,
                    aep_dir=aep_dir
                )

                with open(launcher_script, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(sh_content)

                os.chmod(launcher_script, 0o755)

                if not auto_run:
                    if platform.system() == 'Darwin':
                        subprocess.Popen(['open', '-R', launcher_script])
                    else:
                        subprocess.Popen(['xdg-open', scripts_dir])
                    #nuke.message("✅ 请运行选中的脚本以创建 AE 工程")
                else:
                    if not self._run_afterfx_inline(ae_exe, inline_script_mac, aep_dir, auto_close):
                        return False

            return True

        except Exception as e:
            nuke.message("Failed to create batch script:\n{}\n\nJSX path:\n{}\nConfig path:\n{}".format(str(e), jsx_path, config_path))
            return False
    
        
    def find_ae_executable(self):
        """Locate the After Effects executable dynamically."""
        system = platform.system()
        
        if system == 'Windows':
            # Windows 常见路径
            possible_paths = [
                r'C:\Program Files\Adobe\Adobe After Effects 2024\Support Files\AfterFX.exe',
                r'C:\Program Files\Adobe\Adobe After Effects 2023\Support Files\AfterFX.exe',
                r'C:\Program Files\Adobe\Adobe After Effects 2022\Support Files\AfterFX.exe',
                r'C:\Program Files\Adobe\Adobe After Effects CC 2021\Support Files\AfterFX.exe',
                r'C:\Program Files\Adobe\Adobe After Effects CC 2020\Support Files\AfterFX.exe',
            ]
            
            # 检查用户自定义路径
            custom_path = self.node['ae_executable'].value()
            if custom_path and os.path.exists(custom_path):
                return custom_path
                
            for path in possible_paths:
                if os.path.exists(path):
                    return path
                    
        elif system == 'Darwin':  # macOS
            possible_paths = [
                '/Applications/Adobe After Effects 2024/Adobe After Effects 2024.app/Contents/MacOS/After Effects',
                '/Applications/Adobe After Effects 2023/Adobe After Effects 2023.app/Contents/MacOS/After Effects',
                '/Applications/Adobe After Effects 2022/Adobe After Effects 2022.app/Contents/MacOS/After Effects',
            ]
            
            custom_path = self.node['ae_executable'].value()
            if custom_path and os.path.exists(custom_path):
                return custom_path
                
            for path in possible_paths:
                if os.path.exists(path):
                    return path
                    
        return None
        
    def refresh_and_render(self):
        """Refresh the output metadata and queue rendering in After Effects."""
        try:
            project_info = self.create_directory_structure()

            enabled_inputs = self._collect_enabled_inputs()
            if not enabled_inputs:
                nuke.message('Enable and connect at least one input!')
                return

            layer_ranges, global_first, global_last, outputs, _user_skipped, rendered_any = self._build_exrs_and_maybe_render(project_info, enabled_inputs)

            ae_project_path = project_info['ae_project_path']
            project_exists = os.path.exists(ae_project_path)

            if rendered_any or not project_exists:
                config_json = self._generate_jsx_for_exr_list(project_info, outputs, layer_ranges, global_first, global_last, should_render=True)
                if self.execute_ae_script(config_json, project_info):
                    nuke.message('AE render batch created. Run 2_Render_AE_Output in the _scripts directory to finish rendering.')
            else:
                self._open_in_explorer(ae_project_path, select_file=True)
                nuke.message('All footage is up to date; no render script generated.')
            
        except Exception as e:
            import traceback
            error_msg = "Refresh output failed:\n{}\n\n{}".format(str(e), traceback.format_exc())
            nuke.message(error_msg)
    
    def refresh_output(self):
        """Inspect the output folder and generate a semi-automatic AE render script."""
        try:
            ae_exe = self.find_ae_executable()
            if not ae_exe:
                nuke.message("After Effects executable not found. Ensure After Effects is installed.")
                return

            aep_path = self.node['custom_project_path'].value()
            if not aep_path or not os.path.exists(aep_path):
                placeholder = aep_path or '<Unconfigured>'
                nuke.message("After Effects project file not found.\nUse \"Create AE Bridge / Edit AE Content\" first.\n\nPath:\n{}".format(placeholder))
                return

            output_path = self.node['output_path'].value()
            if not output_path:
                nuke.message("Output directory is not configured. Create or update the AE project first.")
                return

            node_name = self.node.name()
            output_sequence_pattern = os.path.join(output_path, node_name + '.%05d.png')

            try:
                global_first = int(nuke.root()['first_frame'].value()) if nuke.root() else 0
                global_last = int(nuke.root()['last_frame'].value()) if nuke.root() else 0
            except Exception:
                global_first, global_last = 0, 0

            default_target = {
                'type': 'sequence',
                'pattern': normalize_path(output_sequence_pattern),
                'first': global_first,
                'last': global_last
            }
            self._apply_output_target(default_target, fallback_pattern=default_target['pattern'], fallback_range=(global_first, global_last))

            seq_info, video_info = self._scan_output_media(output_path, node_name)
            if seq_info:
                digits = seq_info.get('digits', 4)
                first_file = seq_info['pattern'].replace('%0{}d'.format(digits), str(seq_info['first']).zfill(digits))
                if os.path.exists(first_file):
                    if not nuke.ask('检测到已存在序列 ({ext}):\n{path}\n\n是否覆盖重新渲染？'.format(ext=seq_info['ext'], path=first_file)):
                        seq_target = {
                            'type': 'sequence',
                            'pattern': normalize_path(seq_info['pattern']),
                            'first': seq_info.get('first'),
                            'last': seq_info.get('last')
                        }
                        self._apply_output_target(seq_target, fallback_pattern=default_target['pattern'], fallback_range=(global_first, global_last))
                        return

            scripts_dir = os.path.join(os.path.dirname(aep_path), '_scripts')
            if not os.path.exists(scripts_dir):
                os.makedirs(scripts_dir)

            # 安装渲染 JSX
            try:
                success, render_jsx_path, msg = _ae_jsx.install_render_jsx(ae_exe)
                if not success:
                    nuke.message('Error: ' + msg)
                    return
            except Exception as e:
                nuke.message('Failed to install render script:\n{}'.format(str(e)))
                return

            # 生成渲染 JSON（无需触发 Nuke 渲染）
            project_info = self.create_directory_structure()
            
            # 获取用户自定义的输出路径（如果有）
            user_output_path = self.node['ae_output_render_path'].value()
            
            render_config = _ae_jsx.generate_render_config(project_info, global_first, global_last, user_output_path)
            render_config_path = os.path.join(scripts_dir, 'AEBridge_render.json')
            with open(render_config_path, 'w', encoding='utf-8') as f:
                f.write(render_config)

            render_config_norm = normalize_path(render_config_path)
            render_jsx_norm = normalize_path(render_jsx_path)

            inline_base = 'app.exitAfterLaunchAndEval = true; var AEBRIDGE_RENDER_CONFIG_PATH="{config}"; $.evalFile("{jsx}");'
            inline_script_win = inline_base.format(
                config=render_config_norm.replace('"', '\"'),
                jsx=render_jsx_norm.replace('"', '\"')
            )
            inline_script_unix = inline_base.format(
                config=render_config_norm,
                jsx=render_jsx_norm
            )

            # 输出文件名使用节点名（不添加 _output 后缀）
            output_pattern_render = os.path.join(output_path, node_name + '.[#####].png')
            system = platform.system()
            if system == 'Windows':
                render_script = os.path.join(scripts_dir, '2_Render_AE_Output.bat')
                bat_content = '''@echo off
chcp 65001 >nul

echo ==================================
echo   AEBridge - Render PNG Sequence
echo ==================================

echo.
set "PATH=%SYSTEMROOT%\System32;%SYSTEMROOT%;%SYSTEMROOT%\System32\Wbem"
set NUKE_PATH=
set PYTHONPATH=
set PYTHONHOME=

cd /d "{aep_dir}"

echo Launching After Effects to render the PNG sequence...

start /min "" "{afterfx}" -s "{inline_script}"

timeout /t 2 /nobreak >nul

exit
'''.format(
                    afterfx=os.path.normpath(ae_exe),
                    inline_script=inline_script_win,
                    aep_dir=os.path.normpath(os.path.dirname(aep_path))
                )

                with open(render_script, 'w', encoding='utf-8-sig') as f:
                    f.write(bat_content)

                subprocess.Popen(['explorer', '/select,', os.path.normpath(render_script)])
                #nuke.message('✅ 已生成 AE 渲染脚本，请在资源管理器中执行以导出序列。')

            else:
                render_script = os.path.join(scripts_dir, '2_Render_AE_Output.sh')
                sh_content = '''#!/bin/bash

echo "=================================="
echo "  AEBridge - Render PNG Sequence"
echo "=================================="
echo ""

export PATH="/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin"
unset NUKE_PATH PYTHONPATH PYTHONHOME

cd "{aep_dir}"

echo "Launching After Effects to render the PNG sequence..."

nohup "{afterfx}" -s '{inline_script}' >/dev/null 2>&1 &

sleep 2

exit 0
'''.format(
                    afterfx=ae_exe,
                    inline_script=inline_script_unix,
                    aep_dir=os.path.dirname(aep_path)
                )

                with open(render_script, 'w', encoding='utf-8', newline='\n') as f:
                    f.write(sh_content)
                os.chmod(render_script, 0o755)

                if system == 'Darwin':
                    subprocess.Popen(['open', '-R', render_script])
                else:
                    subprocess.Popen(['xdg-open', scripts_dir])

                #nuke.message('✅ 已生成 AE 渲染脚本，请在终端中执行以导出 PNG 序列。')

        except Exception as e:
            import traceback
            nuke.message("Failed to generate render script:\n{}\n\n{}".format(str(e), traceback.format_exc()))
    
    def _update_default_output_path(self, force=False):
        """Update the default output path based on the current AE project."""
        try:
            # 检查用户是否手动设置过输出路径
            try:
                user_set = self.node['_output_path_user_set'].value()
                if user_set and not force:
                    return
            except Exception:
                pass  # 旧节点可能没有这个 knob
            
            # 检查是否应该跳过自动填充
            current_output = self.node['ae_output_render_path'].value()
            if current_output and not force:
                # 用户已设置，且不是强制更新，则跳过
                return
            
            # 获取 AE 工程路径
            aep_path = self.node['custom_project_path'].value()
            if not aep_path:
                # 如果未设置，使用默认路径规则
                script_path = nuke.root().name()
                if script_path:
                    node_name = self.node.name()
                    aep_path = os.path.join(os.path.dirname(script_path), node_name, node_name + '.aep')
            
            if aep_path:
                # 计算默认输出路径：AE工程目录/<节点名称>_output/<节点名>.[#####].png
                # 注意：统一使用 5 位数字格式 [#####]，与 AE 渲染输出一致
                aep_dir = os.path.dirname(aep_path)
                node_name = self.node.name()
                output_dir = os.path.join(aep_dir, node_name + '_output')
                output_path = os.path.join(output_dir, node_name + '.[#####].png')
                output_path_normalized = normalize_path(output_path)
                
                self.node['ae_output_render_path'].setValue(output_path_normalized)
                # 同步内部 Read 节点（静默模式）
                self._update_output_read_node(output_path_normalized, show_message=False)
        except Exception as e:
            pass  # 静默失败，不影响主流程
    
    def _update_output_read_node(self, user_path, show_message=True):
        """Sync the internal AE_Output Read node with the user-specified path."""
        try:
            if not user_path:
                if show_message:
                    nuke.message("Please specify the output file path in 'AE Output Path'.")
                return None

            import re

            # Nuke 路径规范化：统一使用正斜杠
            normalized = user_path.strip().replace('\\', '/')
            is_sequence = False
            printf_pattern = normalized  # printf 格式用于 Read 节点
            scan_pattern = normalized    # 用于文件扫描
            digits = 4

            # 检测方括号格式：[####] 或 [#####]
            bracket_match = re.search(r'\[#+\]', normalized)
            if bracket_match:
                is_sequence = True
                digits = len(bracket_match.group(0)) - 2  # 计算 # 的数量
                # 转换为 printf 格式：[####] -> %04d, [#####] -> %05d
                printf_format = '%0{}d'.format(digits)
                printf_pattern = normalized.replace(bracket_match.group(0), printf_format)
                scan_pattern = printf_pattern
            else:
                # 检测 printf 风格：%04d 或 %05d
                printf_match = re.search(r'%0(\d+)d', normalized)
                if printf_match:
                    is_sequence = True
                    digits = int(printf_match.group(1))
                    printf_pattern = normalized
                    scan_pattern = normalized
                else:
                    # 检测简单 printf：%d
                    simple_printf = re.search(r'%d', normalized)
                    if simple_printf:
                        is_sequence = True
                        digits = 4
                        printf_pattern = normalized.replace('%d', '%04d')
                        scan_pattern = printf_pattern

            # 更新外部属性（使用 printf 格式，保持显示一致）
            try:
                self.node['output_sequence'].setValue(normalize_path(printf_pattern))
            except Exception:
                pass

            # 扫描帧（在进入 Group 之前）
            frame_info = None
            first_frame = None
            last_frame = None
            
            if is_sequence:
                try:
                    global_first = int(nuke.root()['first_frame'].value()) if nuke.root() else 0
                    global_last = int(nuke.root()['last_frame'].value()) if nuke.root() else 0
                except Exception:
                    global_first, global_last = 0, 100

                # 扫描存在的帧
                existing_frames = []
                for frame in range(global_first, global_last + 1):
                    try:
                        file_path = scan_pattern % frame
                    except Exception:
                        continue
                    if os.path.exists(file_path):
                        existing_frames.append(frame)

                if existing_frames:
                    first_frame = existing_frames[0]
                    last_frame = existing_frames[-1]
                    frame_info = (first_frame, last_frame, len(existing_frames))
                else:
                    first_frame = global_first
                    last_frame = global_last
                    frame_info = None

            # 进入 Group 内部（完全模仿 _apply_output_target 的顺序）
            self.node.begin()
            try:
                read_node = nuke.toNode('AE_Output')
                
                if not read_node:
                    # 备用查找
                    for n in nuke.allNodes('Read'):
                        if n.name().startswith('AE_Output'):
                            read_node = n
                            break
                
                if not read_node:
                    if show_message:
                        nuke.message("AEBridge error: internal AE_Output Read node not found.")
                    return None

                read_node['file'].setValue(normalize_path(printf_pattern))

                if is_sequence and first_frame is not None and last_frame is not None:
                    try:
                        read_node['first'].setValue(int(first_frame))
                        read_node['last'].setValue(int(last_frame))
                    except Exception:
                        pass

                try:
                    read_node['reload'].execute()
                except Exception:
                    pass
                
                # 返回结果
                if is_sequence:
                    return {
                        'is_sequence': True,
                        'printf_path': printf_pattern,
                        'scan_pattern': scan_pattern,
                        'digits': digits,
                        'frames': frame_info
                    }
                else:
                    exists_flag = os.path.exists(normalized)
                    return {
                        'is_sequence': False,
                        'printf_path': printf_pattern,
                        'exists': exists_flag
                    }
                    
            finally:
                self.node.end()

        except Exception as exc:
            if show_message:
                import traceback
                nuke.message("Refresh failed:\n{}\n\n{}".format(str(exc), traceback.format_exc()))
            return None
    
    def _update_aces_visibility(self):
        """更新 ACES 相关选项的可见性"""
        try:
            aces_enabled = self.node['write_ACES_compliant_EXR'].value()
            
            # 当启用 ACES 时，某些选项会被禁用或隐藏
            if 'exr_heroView' in self.node.knobs():
                self.node['exr_heroView'].setVisible(aces_enabled)
            
            # ACES 模式下，某些压缩格式可能不可用
            # 根据 Nuke 文档，ACES EXR 通常使用特定的压缩格式
            if aces_enabled:
                # ACES 模式下推荐使用 PIZ 或 Zip 压缩
                try:
                    current_comp = self.node['exr_compression'].value()
                    # 如果当前是 lossy 压缩，切换到 Zip
                    if 'lossy' in current_comp.lower():
                        self.node['exr_compression'].setValue('zip')
                except Exception:
                    pass
        except Exception:
            pass
    
    def _update_aces_lock_state(self):
        """Update lock state of compression and bit depth when ACES compliant option changes.
        
        Mimics Nuke Write node behavior:
        - When ACES compliant is enabled: lock compression to 'dwaa' and bit depth to '16-bit'
        - When disabled: unlock both controls
        """
        try:
            if 'write_aces_compliant' not in self.node.knobs():
                return
            
            aces_enabled = self.node['write_aces_compliant'].value()
            
            # Lock/unlock compression knob
            if 'exr_compression' in self.node.knobs():
                comp_knob = self.node['exr_compression']
                if aces_enabled:
                    # Lock and set to dwaa (ACES standard)
                    try:
                        comp_knob.setValue('dwaa')
                    except Exception:
                        pass
                    try:
                        comp_knob.setEnabled(False)
                    except Exception:
                        pass
                else:
                    # Unlock
                    try:
                        comp_knob.setEnabled(True)
                    except Exception:
                        pass
            
            # Lock/unlock bit depth knob
            if 'exr_bitdepth' in self.node.knobs():
                depth_knob = self.node['exr_bitdepth']
                if aces_enabled:
                    # Lock and set to 16-bit (ACES standard)
                    try:
                        depth_knob.setValue('16-bit')
                    except Exception:
                        pass
                    try:
                        depth_knob.setEnabled(False)
                    except Exception:
                        pass
                else:
                    # Unlock
                    try:
                        depth_knob.setEnabled(True)
                    except Exception:
                        pass
        except Exception:
            pass
    
    def refresh_render_path(self):
        """Refresh AE output path and update the internal Read node (silent mode)."""
        try:
            user_path = (self.node['ae_output_render_path'].value() or '').strip()
            if not user_path:
                # Silent return if no path set
                return None

            # Silent update without showing messages
            info = self._update_output_read_node(user_path, show_message=False)
            if not info:
                return

            try:
                self.node.forceValidate()
            except Exception:
                pass

            try:
                nuke.updateUI()
            except Exception:
                pass

            try:
                self.node.begin()
                try:
                    read_node = nuke.toNode('AE_Output')
                    if not read_node:
                        for candidate in nuke.allNodes('Read'):
                            if candidate.name().startswith('AE_Output'):
                                read_node = candidate
                                break
                    if read_node:
                        # Just reload the Read node without changing colorspace
                        try:
                            read_node['reload'].execute()
                        except Exception:
                            pass
                finally:
                    self.node.end()
            except Exception:
                pass

            for viewer in nuke.allNodes('Viewer'):
                try:
                    viewer['gl_reload'].execute()
                except Exception:
                    pass

            # Silent mode: no message popups on success

        except Exception as exc:
            # Only show error messages in critical failures
            import traceback
            nuke.message("Refresh failed:\n{}\n\n{}".format(str(exc), traceback.format_exc()))


def create_ae_bridge_node():
    """Create the AE Bridge group node with intelligent connection handling."""
    # Get currently selected nodes
    selected_nodes = nuke.selectedNodes()
    
    # Filter renderable nodes (exclude Viewer, Dot, etc.)
    excluded_classes = set(['Viewer', 'Dot', 'BackdropNode', 'StickyNote'])
    renderable_nodes = []
    for n in selected_nodes:
        cls = n.Class()
        # Exclude non-image nodes (allow AEBridge as input source)
        if cls in excluded_classes:
            continue
        # Treat all other nodes as valid input sources (Read, Merge, Transform, Grade, AEBridge, etc.)
        renderable_nodes.append(n)
    
    # Create Group node as container
    if renderable_nodes:
        # Selected nodes: create with default placement, connect, then autoplace for tight layout
        try:
            node = nuke.createNode('Group', inpanel=False)
        except Exception:
            node = nuke.nodes.Group()
        try:
            node.setName(generate_unique_node_name('AEBridge'))
        except Exception:
            pass
    else:
        # No selection: default creation (Nuke places at visible center)
        try:
            node = nuke.createNode('Group', inpanel=False)
        except Exception:
            node = nuke.nodes.Group()
        try:
            node.setName(generate_unique_node_name('AEBridge'))
        except Exception:
            pass
    if hasattr(node, 'setMinInputs'):
        node.setMinInputs(0)
    if hasattr(node, 'setMaxInputs'):
        node.setMaxInputs(64)
    # Place additional inputs on left side when API is available
    try:
        if hasattr(node, 'setLeftRightInputs'):
            node.setLeftRightInputs(True)
    except Exception:
        pass
    
    # Enter Group
    node.begin()
    
    # Create single Input node initially (others added automatically via drag)
    inp = nuke.nodes.Input(name='Input')
    try:
        inp['label'].setValue('')  # Remove input label/numbering
    except Exception:
        pass
    
    # Create Read node for AE output display
    read_node = nuke.nodes.Read(name='AE_Output')
    
    # Create output
    group_output = nuke.nodes.Output(name='Output')
    group_output.setInput(0, read_node)
    
    node.end()
    
    # Add custom properties
    node.addKnob(nuke.Tab_Knob('ae_bridge_tab', 'AE Bridge'))
    
    # Hidden string knob for tracking input connection state (JSON)
    try:
        sig_knob = nuke.String_Knob('_inputs_signature', '')
        sig_knob.setVisible(False)
        node.addKnob(sig_knob)
    except Exception:
        pass
    
    # Hidden boolean knob to track if user manually set output path
    try:
        user_set_knob = nuke.Boolean_Knob('_output_path_user_set', '')
        user_set_knob.setVisible(False)
        user_set_knob.setValue(False)
        node.addKnob(user_set_knob)
    except Exception:
        pass
    
    # AE executable path
    ae_exe_knob = nuke.File_Knob('ae_executable', 'AE Executable')
    try:
        ae_exe_knob.setFlag(nuke.STARTLINE)
    except Exception:
        pass
    # Auto-detect and set default AE path
    try:
        auto_ae_path = find_ae_executable_static()
        if auto_ae_path:
            ae_exe_knob.setValue(normalize_path(auto_ae_path))
    except Exception:
        pass
    node.addKnob(ae_exe_knob)
    
    # Auto find button
    node.addKnob(nuke.PyScript_Knob('auto_find_ae', 'Auto Find AE'))
    
    # Custom project path (optional)
    custom_path_knob = nuke.File_Knob('custom_project_path', 'AE Project Path (optional)')
    custom_path_knob.setTooltip('Leave empty to use <script directory>/<node name>/<node name>.aep')
    try:
        custom_path_knob.setFlag(nuke.STARTLINE)
    except Exception:
        pass
    # Enable directory browsing flag
    try:
        custom_path_knob.setFlag(0x00000001)  # Allow directory selection
    except Exception:
        pass
    node.addKnob(custom_path_knob)
    
    # Browse button
    node.addKnob(nuke.PyScript_Knob('browse_project_path', 'Browse...'))
    
    # AE output path (user customizable)
    output_render_path_knob = nuke.File_Knob('ae_output_render_path', 'AE Output Path')
    output_render_path_knob.setTooltip('Set the AE render PNG sequence path.\nDefault: <AE project>/<node name>_output/<node name>.[#####].png\n\nAccepted formats:\n- [#####]\n- %05d')
    try:
        output_render_path_knob.setFlag(nuke.STARTLINE)
    except Exception:
        pass
    node.addKnob(output_render_path_knob)
    
    # Refresh output button (positioned to right of output path)
    node.addKnob(nuke.PyScript_Knob('refresh_render_path', 'Refresh'))
    
    # Hidden internal path properties
    ae_path_knob = nuke.String_Knob('ae_project_path', 'AE Project Path')
    ae_path_knob.setVisible(False)
    node.addKnob(ae_path_knob)
    
    output_path_knob = nuke.String_Knob('output_path', 'Output Path')
    output_path_knob.setVisible(False)
    node.addKnob(output_path_knob)
    
    output_seq_knob = nuke.File_Knob('output_sequence', 'Output Sequence')
    output_seq_knob.setVisible(False)
    node.addKnob(output_seq_knob)
    
    # EXR Options 分隔符
    node.addKnob(nuke.Text_Knob('exr_options_divider', 'EXR Options'))

    # EXR global settings (all compression formats support multi-channel)
    try:
        exr_comp = nuke.Enumeration_Knob('exr_compression', 'compression', 
            ['rle', 'zip', 'zips', 'piz', 'pxr24', 'b44', 'b44a', 'dwaa', 'dwab'])
        try:
            exr_comp.setFlag(nuke.STARTLINE)
        except Exception:
            pass
        node.addKnob(exr_comp)
        try:
            node['exr_compression'].setValue('zip')
        except Exception:
            pass
    except Exception:
        pass
    try:
        exr_depth = nuke.Enumeration_Knob('exr_bitdepth', 'bit depth', ['16-bit', '32-bit'])
        node.addKnob(exr_depth)
        try:
            node['exr_bitdepth'].setValue('16-bit')
        except Exception:
            pass
    except Exception:
        pass

    # Output Transform (color management)
    try:
        # Get available colorspaces from a temporary Write node
        temp_write = nuke.nodes.Write(file='dummy.exr', file_type='exr')
        try:
            if 'colorspace' in temp_write.knobs():
                available_colorspaces = list(temp_write['colorspace'].values()) if hasattr(temp_write['colorspace'], 'values') else []
            else:
                available_colorspaces = []
            nuke.delete(temp_write)
        except Exception:
            available_colorspaces = []
            try:
                nuke.delete(temp_write)
            except Exception:
                pass
        
        # Remove 'default' from available colorspaces if present
        # User wants linear as the default, not 'default'
        if 'default' in available_colorspaces:
            available_colorspaces.remove('default')
        
        # Create Output Transform knob
        if available_colorspaces:
            output_transform = nuke.Enumeration_Knob('output_transform', 'Output Transform', available_colorspaces)
        else:
            # Fallback if we can't get colorspaces
            output_transform = nuke.String_Knob('output_transform', 'Output Transform')
        
        try:
            output_transform.setFlag(nuke.STARTLINE)
        except Exception:
            pass
        node.addKnob(output_transform)
        
        # Set default to linear/scene_linear (exclude 'default')
        try:
            if available_colorspaces:
                for variant in ['scene_linear', 'linear', 'Linear']:
                    if variant in available_colorspaces:
                        node['output_transform'].setValue(variant)
                        break
        except Exception:
            pass
    except Exception:
        pass

    # write ACES compliant EXR (locks compression and bit depth when enabled)
    try:
        aces_compliant = nuke.Boolean_Knob('write_aces_compliant', 'write ACES compliant EXR')
        aces_compliant.setTooltip(
            'When enabled, writes ACES-compliant EXR files.\n'
            'Locks compression to "dwaa" and bit depth to "16-bit" (matches Nuke Write behavior).\n\n'
            'OCIO Color Engine:\n'
            '- Script will TRY to auto-enable OCIO engine in AE (experimental)\n'
            '- If OCIO config is set up in AE Preferences, it will be used\n'
            '- If not configured, falls back to linear working space (still works!)\n\n'
            'Manual OCIO Setup (for full ACES):\n'
            '1. Download ACES OCIO config from opencolorio.org\n'
            '2. AE: Edit > Preferences > Color Management\n'
            '3. Color Engine: "OCIO Color Management"\n'
            '4. Select OCIO config file (.ocio)\n'
            '5. Working Space: "ACEScg"\n'
            '6. Re-run AE Bridge\n\n'
            'Note: Linear blending ensures correct compositing even without full OCIO setup.'
        )
        node.addKnob(aces_compliant)
        node['write_aces_compliant'].setValue(False)
    except Exception:
        pass

    # EXR colorspace (fixed to Linear, hidden to avoid sRGB output inconsistencies)
    try:
        exr_csp = nuke.String_Knob('exr_colorspace', 'EXR Colorspace')
        exr_csp.setVisible(False)
        node.addKnob(exr_csp)
        node['exr_colorspace'].setValue('linear')
    except Exception:
        pass
    
    # 强制渲染选项（跳过文件检查）
    try:
        force_render = nuke.Boolean_Knob('force_render', 'Force Render (Skip File Check)')
        force_render.setTooltip('When enabled, skip file existence check and render all frames.\nUseful for forcing a complete re-render.')
        try:
            force_render.setFlag(nuke.STARTLINE)
        except Exception:
            pass
        node.addKnob(force_render)
        node['force_render'].setValue(False)
    except Exception:
        pass

    # Create/Edit button (force new line)
    _btn_create = nuke.PyScript_Knob('create_or_edit_button', 'Create AE Bridge / Edit AE Content')
    try:
        _btn_create.setFlag(nuke.STARTLINE)
    except Exception:
        pass
    node.addKnob(_btn_create)
    # Refresh output button
    node.addKnob(nuke.PyScript_Knob('refresh_output', 'Refresh Output'))
    
    # Per-input settings header
    node.addKnob(nuke.Text_Knob('inputs_header', '<b>Per-Input Settings</b>'))
    
    # Set button commands (avoid using setKnobChanged which is not supported by Group nodes)
    try:
        node['auto_find_ae'].setCommand('import AEBridge.ae_bridge as ae_bridge; ae_bridge._ae_bridge_auto_find_ae()')
        node['browse_project_path'].setCommand('import AEBridge.ae_bridge as ae_bridge; ae_bridge._ae_bridge_browse_project_path()')
        node['create_or_edit_button'].setCommand('import AEBridge.ae_bridge as ae_bridge; ae_bridge._ae_bridge_create_or_edit()')
        node['refresh_output'].setCommand('import AEBridge.ae_bridge as ae_bridge; ae_bridge._ae_bridge_refresh_output()')
        node['refresh_render_path'].setCommand('import AEBridge.ae_bridge as ae_bridge; ae_bridge._ae_bridge_refresh_render_path()')
    except Exception:
        pass
    
    # Set default AE project path (if Nuke script is saved)
    try:
        script_path = nuke.root().name()
        if script_path:
            node_name = node.name()
            project_dir = os.path.join(os.path.dirname(script_path), node_name)
            default_ae_path = normalize_path(os.path.join(project_dir, node_name + '.aep'))
            node['custom_project_path'].setValue(default_ae_path)
            
            # Also set default output path
            default_output_dir = os.path.join(project_dir, node_name + '_output')
            default_output_path = normalize_path(os.path.join(default_output_dir, node_name + '.[#####].png'))
            node['ae_output_render_path'].setValue(default_output_path)
    except Exception:
        pass
    
    # Add temporary initialization marker to prevent callback triggers
    node.addKnob(nuke.Text_Knob('_initializing', ''))
    node['_initializing'].setVisible(False)
    
    # Auto-connect selected renderable nodes
    if renderable_nodes:
        for n in nuke.allNodes():
            n.setSelected(False)
        
        # Connect first node to existing input
        node.setInput(0, renderable_nodes[0])
        
        # If multiple nodes selected, dynamically add Inputs and connect
        if len(renderable_nodes) > 1:
            for i, source_node in enumerate(renderable_nodes[1:], start=1):
                # Enter group and add Input node
                node.begin()
                try:
                    inp = nuke.nodes.Input(name='Input')
                    try:
                        inp['label'].setValue('')
                    except Exception:
                        pass
                finally:
                    node.end()
                
                # Connect to new input
                node.setInput(i, source_node)
        
        # Rewire AEBridge output to first downstream consumer (replace original connection)
        try:
            rewired = False
            all_nodes = nuke.allNodes()
            for src in renderable_nodes:
                for consumer in all_nodes:
                    try:
                        for k in range(consumer.inputs()):
                            if consumer.input(k) is src:
                                consumer.setInput(k, node)
                                rewired = True
                                break
                        if rewired:
                            break
                    except Exception:
                        continue
                if rewired:
                    break
        except Exception:
            pass
        # Position AEBridge directly below selected nodes with collision avoidance
        try:
            def get_bbox(nn):
                try:
                    w = nn.screenWidth()
                    h = nn.screenHeight()
                except Exception:
                    w = 100
                    h = 32
                return nn.xpos(), nn.ypos(), w, h

            # Calculate average center X and maximum bottom Y of selected inputs
            centers_x = []
            max_bottom_y = None
            for src in renderable_nodes:
                x, y, w, h = get_bbox(src)
                centers_x.append(x + int(w / 2))
                bottom = y + h
                if max_bottom_y is None or bottom > max_bottom_y:
                    max_bottom_y = bottom

            avg_center_x = int(sum(centers_x) / len(centers_x)) if centers_x else node.xpos()
            try:
                node_w = node.screenWidth()
                node_h = node.screenHeight()
            except Exception:
                node_w = 120
                node_h = 32

            vertical_spacing = max(60, int(node_h * 1.5))
            target_x = avg_center_x - int(node_w / 2)
            target_y = (max_bottom_y or node.ypos()) + vertical_spacing

            # Simple collision avoidance: move down until no overlap with existing nodes
            def intersects(ax, ay, aw, ah, bx, by, bw, bh):
                return not (ax + aw <= bx or bx + bw <= ax or ay + ah <= by or by + bh <= ay)

            all_nodes = [n for n in nuke.allNodes() if n is not node]
            step = max(20, int(node_h / 2))
            max_iterations = 200
            iters = 0
            while iters < max_iterations:
                overlap = False
                for other in all_nodes:
                    ox, oy, ow, oh = get_bbox(other)
                    if intersects(target_x, target_y, node_w, node_h, ox, oy, ow, oh):
                        overlap = True
                        break
                if not overlap:
                    break
                target_y += step
                iters += 1

            node.setXYpos(target_x, target_y)
        except Exception:
            pass
        node.setSelected(True)

    # Post-creation check: if all inputs are occupied, add an empty input slot
    try:
        cur_inputs = node.inputs()
        if cur_inputs > 0:
            has_empty = False
            for i in range(cur_inputs):
                if node.input(i) is None:
                    has_empty = True
                    break
            if not has_empty:
                node.begin()
                try:
                    _new = nuke.nodes.Input(name='Input')
                    try:
                        _new['label'].setValue('')
                    except Exception:
                        pass
                finally:
                    node.end()
    except Exception:
        pass
    
    # Initialize input state signature
    try:
        current_inputs = node.inputs()
        signature = [1 if node.input(i) is not None else 0 for i in range(current_inputs)]
        import json as _json
        node['_inputs_signature'].setValue(_json.dumps(signature))
    except Exception:
        pass

    # Remove initialization marker
    try:
        node.removeKnob(node['_initializing'])
    except Exception:
        pass
    # Initial per-input knob synchronization
    try:
        _sync_per_input_knobs(node)
    except Exception:
        pass
    
    # Initialize default output path
    try:
        bridge = AEBridgeNode(node)
        bridge._update_default_output_path()
    except Exception:
        pass
    
    return node


# Independent button callbacks (avoid using Group.setKnobChanged)
def _ae_bridge_auto_find_ae():
    node = nuke.thisNode()
    AEBridgeNode(node).auto_find_ae()

def _ae_bridge_browse_project_path():
    node = nuke.thisNode()
    AEBridgeNode(node).browse_project_path()

def _ae_bridge_create_or_edit():
    node = nuke.thisNode()
    AEBridgeNode(node).create_or_edit_ae()

def _ae_bridge_refresh_output():
    node = nuke.thisNode()
    AEBridgeNode(node).refresh_output()

def _ae_bridge_refresh_render_path():
    node = nuke.thisNode()
    AEBridgeNode(node).refresh_render_path()


    


def _auto_expand_inputs():
    """Global callback that adds an extra input when last one is used (Merge-like behavior)."""
    try:
        node = nuke.thisNode()
        knob = nuke.thisKnob()
        # Only trigger on input connection changes
        if knob is None or knob.name() != 'inputChange':
            return
        # Only handle AEBridge nodes
        if not node or node.Class() != 'Group' or not node.name().startswith('AEBridge'):
            return
        
        # Ignore during creation phase
        try:
            if node.knob('_initializing') is not None:
                return
        except Exception:
            pass
        
        current_inputs = node.inputs()
        if current_inputs == 0:
            return

        # Collect connected external sources (sorted by input index)
        connected_pairs = []
        for i in range(current_inputs):
            src = node.input(i)
            if src is not None:
                connected_pairs.append((i, src))
        connected_pairs.sort(key=lambda t: t[0])
        connected_sources = [src for _, src in connected_pairs]

        # Count empty inputs and calculate desired Input count (connected + 1 empty)
        empty_count = sum(1 for i in range(current_inputs) if node.input(i) is None)
        desired_inputs = len(connected_sources) + 1

        # Reentry lock to prevent repeated triggers during rebuild
        global _AEBRIDGE_MAINTAIN_LOCK
        try:
            _AEBRIDGE_MAINTAIN_LOCK
        except NameError:
            _AEBRIDGE_MAINTAIN_LOCK = set()
        node_id = node.name()
        if node_id in _AEBRIDGE_MAINTAIN_LOCK:
            return

        # Execute strict rebuild only when not exactly one empty input (simulate Merge behavior)
        if empty_count != 1 or current_inputs != desired_inputs:
            _AEBRIDGE_MAINTAIN_LOCK.add(node_id)
            try:
                # 1) Rebuild Input nodes inside group: delete all, create new (connected count + 1)
                node.begin()
                try:
                    for inner in [n for n in nuke.allNodes() if n.Class() == 'Input']:
                        try:
                            nuke.delete(inner)
                        except Exception:
                            pass
                    for _ in range(desired_inputs):
                        _inp = nuke.nodes.Input(name='Input')
                        try:
                            _inp['label'].setValue('')
                        except Exception:
                            pass
                finally:
                    node.end()

                # 2) Restore external connections: reconnect sources in order to inputs 0..n-1
                try:
                    # Clear existing connections first to avoid residuals
                    for i in range(node.inputs()):
                        node.setInput(i, None)
                except Exception:
                    pass
                for idx, src in enumerate(connected_sources):
                    try:
                        node.setInput(idx, src)
                    except Exception:
                        pass
            finally:
                _AEBRIDGE_MAINTAIN_LOCK.discard(node_id)
        # Synchronize per-input dynamic UI (enable/layer/channels)
        try:
            _sync_per_input_knobs(node)
        except Exception:
            pass
    except Exception:
        pass


# Track registration state to avoid duplicate callback registration
_callbacks_registered = False

def register_aebridge_callbacks():
    """Register global callbacks for AE Bridge nodes."""
    global _callbacks_registered
    if _callbacks_registered:
        return
    
    try:
        # Use knobChanged callback instead of UpdateUI (avoid excessive triggers)
        nuke.addKnobChanged(_auto_expand_inputs, nodeClass='Group')
        _callbacks_registered = True
    except Exception:
        pass


def _sanitize_layer_name(name):
    try:
        s = re.sub(r'[^0-9A-Za-z_]+', '_', name or '')
        s = s.strip('_')
        return s or 'Layer'
    except Exception:
        return 'Layer'


def _sync_per_input_knobs(node):
    """Synchronize per-input controls (enable, layer name, channels)."""
    try:
        total = node.inputs()
        # 新增或更新每输入控件
        for i in range(total):
            enable_key = 'in{}_enable'.format(i)
            layer_key = 'in{}_layer'.format(i)
            chan_key = 'in{}_channels'.format(i)  # 存储选择结果（空格分隔）
            chan_btn = 'in{}_channels_btn'.format(i)

            # Input X Enabled (新行开始)
            if enable_key not in node.knobs():
                enable_knob = nuke.Boolean_Knob(enable_key, 'Input {} Enabled'.format(i + 1))
                try:
                    enable_knob.setFlag(nuke.STARTLINE)
                except Exception:
                    pass
                node.addKnob(enable_knob)
                node[enable_key].setValue(True)

            # Input X Layer (同一行)
            src = node.input(i)
            default_layer = _sanitize_layer_name(src.name() if src else 'Input{}'.format(i + 1))
            if layer_key not in node.knobs():
                layer_knob = nuke.String_Knob(layer_key, 'Layer')
                layer_knob.setTooltip('Layer name for this input in After Effects')
                node.addKnob(layer_knob)
            try:
                node[layer_key].setValue(default_layer)
            except Exception:
                pass

            # Input X Channels (新行开始，显示当前选择)
            if chan_key not in node.knobs():
                chan_knob = nuke.String_Knob(chan_key, 'Channels')
                chan_knob.setTooltip('Selected channel layers for this input.\nClick "Select Channels..." to modify.')
                try:
                    chan_knob.setFlag(nuke.STARTLINE)
                except Exception:
                    pass
                node.addKnob(chan_knob)
                src = node.input(i)
                try:
                    all_ch = src.channels() if src else []
                except Exception:
                    all_ch = []
                # Default to ONLY rgba (do not select all layers automatically)
                has_rgba = set(['rgba.red','rgba.green','rgba.blue','rgba.alpha']).issubset(set(all_ch))
                if has_rgba:
                    node[chan_key].setValue('rgba')
                else:
                    # If no rgba, use first available layer only
                    first_layer = None
                    for ch in all_ch:
                        if '.' in ch:
                            first_layer = ch.split('.')[0]
                            break
                    node[chan_key].setValue(first_layer if first_layer else 'rgba')
            
            # Select Channels... 按钮 (同一行)
            if chan_btn not in node.knobs():
                btn = nuke.PyScript_Knob(chan_btn, 'Select Channels...')
                btn.setTooltip('Open channel selector dialog.\nSelect which channel layers to export for this input.\nExample: Select "rgba depth P" to export only those layers.')
                node.addKnob(btn)
                try:
                    node[chan_btn].setCommand('import AEBridge.ae_bridge as ae_bridge; ae_bridge._select_input_channels()')
                except Exception:
                    pass

        # 移除多余控件
        for k in list(node.knobs().keys()):
            m = re.match(r'in(\d+)_(enable|layer|channels|channels_btn)$', k)
            if m:
                idx = int(m.group(1))
                if idx >= total:
                    try:
                        node.removeKnob(node[k])
                    except Exception:
                        pass
    except Exception:
        pass


def _select_input_channels():
    """Show a channel picker dialog and write back the selection."""
    try:
        node = nuke.thisNode()
        knob = nuke.thisKnob()
        kname = knob.name() if knob else ''
        m = re.match(r'in(\d+)_channels_btn$', kname)
        if not m:
            return
        idx = int(m.group(1))
        src = node.input(idx)
        if src is None:
            nuke.message('This input is not connected to any node.')
            return
        try:
            all_channels = list(src.channels())
        except Exception:
            all_channels = []
        if not all_channels:
            nuke.message('No channels were found for this input.')
            return
        def to_bases(chs):
            bases = []
            seen = set()
            for c in chs:
                base = c.split('.', 1)[0]
                if base not in seen:
                    seen.add(base)
                    bases.append(base)
            return bases
        base_list = to_bases(all_channels)
        sel_kn = 'in{}_channels'.format(idx)
        cur = set((node[sel_kn].value() if sel_kn in node.knobs() else '').split())
        try:
            from PySide2 import QtWidgets, QtCore
            dlg = QtWidgets.QDialog()
            dlg.setWindowTitle('Select Channels - Input {}'.format(idx + 1))
            dlg.resize(420, 580)
            layout = QtWidgets.QVBoxLayout(dlg)
            
            # Add search box instead of info label
            search_box = QtWidgets.QLineEdit()
            search_box.setPlaceholderText('Search channels...')
            search_box.setStyleSheet('QLineEdit { padding: 6px; font-size: 12px; border: 2px solid #4a86e8; border-radius: 4px; }')
            layout.addWidget(search_box)

            quick = QtWidgets.QHBoxLayout()
            btn_all = QtWidgets.QPushButton('Select All')
            btn_inv = QtWidgets.QPushButton('Invert')
            quick.addWidget(btn_all); quick.addWidget(btn_inv)
            layout.addLayout(quick)

            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            inner = QtWidgets.QWidget()
            v = QtWidgets.QVBoxLayout(inner)
            checks = []
            for base in base_list:
                cb = QtWidgets.QCheckBox(base)
                cb.setChecked(base in cur)
                v.addWidget(cb)
                checks.append(cb)
            v.addStretch(1)
            scroll.setWidget(inner)
            layout.addWidget(scroll)
            
            # Search filter functionality
            def filter_channels():
                search_text = search_box.text().lower()
                for cb in checks:
                    if search_text in cb.text().lower() or not search_text:
                        cb.setVisible(True)
                    else:
                        cb.setVisible(False)
            
            search_box.textChanged.connect(filter_channels)

            btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
            layout.addWidget(btns)

            def set_all():
                for cb in checks:
                    cb.setChecked(True)
            def set_inv():
                for cb in checks:
                    cb.setChecked(not cb.isChecked())
            btn_all.clicked.connect(set_all)
            btn_inv.clicked.connect(set_inv)
            btns.accepted.connect(dlg.accept)
            btns.rejected.connect(dlg.reject)

            if dlg.exec_() != QtWidgets.QDialog.Accepted:
                return
            chosen = [cb.text() for cb in checks if cb.isChecked()]
            if not chosen:
                chosen = ['rgba'] if 'rgba' in base_list else ([base_list[0]] if base_list else [])
            node[sel_kn].setValue(' '.join(chosen))
            return
        except Exception:
            pass

        p = nuke.Panel('Select Channels - Input {}'.format(idx + 1))
        p.setWidth(480)
        for base in base_list:
            p.addBooleanCheckBox(base, base in cur)
        if not p.show():
            return
        chosen = [base for base in base_list if p.value(base)]
        if not chosen:
            chosen = ['rgba'] if 'rgba' in base_list else ([base_list[0]] if base_list else [])
        node[sel_kn].setValue(' '.join(chosen))
    except Exception:
        pass
