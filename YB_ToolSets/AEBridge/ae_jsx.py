# -*- coding: utf-8 -*-
"""
AEBridge JSX 模块 - AE 脚本生成和安装

- 生成 AE 需要的 JSON 配置文件
- 安装和更新 JSX 脚本到 AE 的 Scripts 文件夹
- 处理跨平台路径和版本管理

"""

import os
import json
import re
import platform
import shutil
import hashlib


def _normalize_path(path):
    """路径标准化（同 ae_bridge.py 中的版本）"""
    if not path:
        return path
    p = path.replace('\\', '/')
    # Preserve UNC prefix (// at start)
    if p.startswith('//'):
        p = '//' + re.sub(r'/+', '/', p[2:])
    else:
        p = re.sub(r'/+', '/', p)
    return p


def _get_file_hash(file_path):
    """
    计算文件的 MD5 哈希值
    
    """
    if not os.path.exists(file_path):
        return None
    hasher = hashlib.md5()
    with open(file_path, 'rb') as f:
        hasher.update(f.read())
    return hasher.hexdigest()


def get_ae_scripts_folder(ae_exe_path):
    """
    获取 AE 的 Scripts 文件夹路径
    
    不同平台的路径规则：
    - Windows: Adobe After Effects 20XX/Support Files/Scripts
    - macOS: Adobe After Effects.app/Contents/Resources/Scripts
    - Linux: Adobe After Effects/Scripts（较少见）
    """
    if not ae_exe_path or not os.path.exists(ae_exe_path):
        return None
    
    # Navigate from AE executable to Scripts folder (varies by platform)
    ae_dir = os.path.dirname(os.path.dirname(ae_exe_path))
    
    if platform.system() == 'Windows':
        # Windows: Adobe After Effects 20XX/Support Files/Scripts
        scripts_dir = os.path.join(os.path.dirname(ae_exe_path), 'Scripts')
    elif platform.system() == 'Darwin':  # macOS
        # macOS: Adobe After Effects.app/Contents/Resources/Scripts
        scripts_dir = os.path.join(ae_dir, 'Resources', 'Scripts')
    else:  # Linux
        scripts_dir = os.path.join(ae_dir, 'Scripts')
    
    return scripts_dir if os.path.exists(scripts_dir) else None


def _install_jsx(ae_exe_path, source_rel_path, target_name):
    """
    安装或更新 JSX 脚本到 AE

    """
    current_dir = os.path.dirname(os.path.abspath(__file__))
    source_jsx = os.path.join(current_dir, source_rel_path)

    if not os.path.exists(source_jsx):
        return (False, None, "Source JSX file not found:\n{}".format(source_jsx))

    scripts_dir = get_ae_scripts_folder(ae_exe_path)
    if not scripts_dir:
        return (False, None, "Cannot find After Effects Scripts folder!\n\nAE path: {}".format(ae_exe_path))

    target_jsx = os.path.join(scripts_dir, target_name)

    # 智能更新：只有内容变化时才复制
    source_hash = _get_file_hash(source_jsx)
    target_hash = _get_file_hash(target_jsx) if os.path.exists(target_jsx) else None

    if source_hash == target_hash:
        return (True, target_jsx, "JSX script is up to date")

    try:
        shutil.copy2(source_jsx, target_jsx)
        action = "Updated" if target_hash else "Installed"
        return (True, target_jsx, "{} JSX script successfully!\n\nLocation:\n{}".format(action, target_jsx))
    except Exception as e:
        return (False, None, "Failed to copy JSX file:\n{}\n\nError: {}".format(target_jsx, str(e)))


def install_startup_jsx(ae_exe_path):
    """安装启动脚本（负责创建/更新 AE 工程）"""
    return _install_jsx(ae_exe_path, os.path.join('ae', 'scripts', 'AEBridge_Startup.jsx'), 'AEBridge_Startup.jsx')


def install_render_jsx(ae_exe_path):
    """安装渲染脚本（负责从 AE 导出 PNG 序列）"""
    return _install_jsx(ae_exe_path, os.path.join('ae', 'scripts', 'AEBridge_Render.jsx'), 'AEBridge_Render.jsx')


def generate_config_json(project_info, outputs, layer_ranges, global_first, global_last, should_render):
    """
    生成 AE 配置 JSON
    
    AE 的 JSX 脚本会读取这个 JSON 文件来执行相应操作
    """
    # Prepare data with normalized paths (forward slashes for cross-platform compatibility)
    output_map = {out.get('layer'): out for out in outputs}
    items_payload = []
    for rng in layer_ranges:
        layer_name = rng.get('layer')
        out_info = output_map.get(layer_name, {})
        pattern = rng.get('pattern') or out_info.get('pattern')
        pattern_norm = _normalize_path(pattern).replace('\\', '/') if pattern else ''
        first = rng.get('first')
        last = rng.get('last')
        # Generate first file path for sequence detection
        if pattern and '%04d' in pattern and first is not None:
            first_file = pattern.replace('%04d', str(first).zfill(4))
        else:
            first_file = pattern
        items_payload.append({
            'name': layer_name,
            'path': pattern_norm,
            'pattern': pattern_norm,
            'first_file': _normalize_path(first_file).replace('\\', '/') if first_file else '',
            'first': first,
            'last': last,
            'channels': rng.get('channels', []),
            'index': rng.get('index', -1),
            'node_name': rng.get('node_name', '')
        })

    project_path = _normalize_path(project_info['ae_project_path']).replace('\\', '/')
    # Use project name (node name) directly without _output suffix
    output_path = _normalize_path(os.path.join(project_info['output_dir'], project_info['project_name'] + '.[#####].png')).replace('\\', '/')
    
    # Get Nuke color management settings
    # Extract colorspace information from project_info if available
    nuke_colorspace = project_info.get('nuke_colorspace', 'scene_linear')
    nuke_working_space = project_info.get('nuke_working_space', 'linear')
    nuke_output_transform = project_info.get('nuke_output_transform', '')
    aces_compliant = project_info.get('aces_compliant', False)
    
    # Build JSON configuration
    config_data = {
        'frame_rate': project_info['frame_rate'],
        'global_first': global_first,
        'global_last': global_last,
        'comp_name': project_info['comp_name'],
        'width': project_info['width'],
        'height': project_info['height'],
        'items': items_payload,
        'project_path': project_path,
        'output_path': output_path,
        'should_render': should_render,
        # Color management settings
        'nuke_colorspace': nuke_colorspace,
        'nuke_working_space': nuke_working_space,
        'nuke_output_transform': nuke_output_transform,
        'aces_compliant': aces_compliant
    }
    
    return json.dumps(config_data, indent=2, ensure_ascii=False)


def generate_jsx_for_exr_list(project_info, outputs, layer_ranges, global_first, global_last, should_render):
    """Legacy compatibility wrapper for generate_config_json."""
    return generate_config_json(project_info, outputs, layer_ranges, global_first, global_last, should_render)


def generate_render_config(project_info, global_first, global_last, output_path_override=None):
    """Generate JSON configuration for AE rendering with PNG output settings."""
    import re
    
    output_dir = project_info.get('output_dir')
    project_name = project_info.get('project_name')

    def convert_to_ae_sequence_format(path):
        """Convert Nuke sequence format to AE sequence format [#####]."""
        if not path:
            return path
        
        # Ensure PNG extension
        root, ext = os.path.splitext(path)
        if ext.lower() not in ['.png']:
            path = root + '.png'
            root, ext = os.path.splitext(path)
        
        # Detect and convert sequence format
        # Nuke format [####] -> AE format [#####] (5-digit padding)
        if '[' in path and ']' in path:
            # Replace any number of # with [#####]
            path = re.sub(r'\[#+\]', '[#####]', path)
        elif '%' in path:
            # printf format %04d -> [#####]
            path = re.sub(r'%0?\d*d', '[#####]', path)
        else:
            # Add sequence marker if none present
            path = root + '.[#####]' + ext
        
        return path

    if output_path_override:
        output_path = convert_to_ae_sequence_format(output_path_override)
        output_path = _normalize_path(output_path).replace('\\', '/')
    else:
        # Default path: <node_name>.[#####].png (AE requires 5-digit padding)
        # Use project_name (node name) without _output suffix
        default_name = project_name + '.[#####].png'
        output_path = _normalize_path(os.path.join(output_dir, default_name)).replace('\\', '/')

    output_settings = {
        'format_type': 'png',
        'template_name': '',
        'channels': 'RGB + Alpha',
        'depth': '16 Bits/Channel',
        'color': 'Straight (Unmatted)',
        'quality': '100',
        'premultiplied': False
    }

    data = {
        'project_path': _normalize_path(project_info['ae_project_path']).replace('\\', '/'),
        'comp_name': project_info['comp_name'],
        'frame_rate': project_info['frame_rate'],
        'global_first': global_first,
        'global_last': global_last,
        'output_path': output_path,
        'output_settings': output_settings
    }
    return json.dumps(data, indent=2, ensure_ascii=False)
