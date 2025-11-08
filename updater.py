# -*- coding: utf-8 -*-
"""
YB Tools - 自动更新模块
"""

import os
import json
import threading
import zipfile
import shutil
import tempfile

# 版本配置文件
VERSION_CONFIG_FILE = "version.json"

# GitHub 配置
GITHUB_USER = "yongbin1999"
GITHUB_REPO = "YB_Nuke_ToolSets"
GITHUB_API_URL = "https://api.github.com/repos/{}/{}/releases/latest".format(GITHUB_USER, GITHUB_REPO)

# 更新配置
UPDATE_CHECK_TIMEOUT = 5  # 检测超时时间（秒）
UPDATE_MARKER_FILE = ".update_pending"  # 标记文件


def get_plugin_root():
    """获取插件根目录"""
    return os.path.dirname(os.path.abspath(__file__))


def load_version_config():
    """
    从 version.json 读取版本配置
    
    返回：
        dict: {"version": "2.2.1", "auto_update": true}
        如果文件不存在或格式错误，返回默认配置
    """
    config_path = os.path.join(get_plugin_root(), VERSION_CONFIG_FILE)
    
    try:
        with open(config_path, 'r') as f:
            config = json.load(f)
            
        # 验证必需字段
        if 'version' not in config:
            print("[YB Tools] 警告: version.json 缺少 'version' 字段")
            return {"version": "0.0.0", "auto_update": True}
            
        # 默认启用自动更新
        if 'auto_update' not in config:
            config['auto_update'] = True
            
        return config
        
    except Exception as e:
        print("[YB Tools] 读取 version.json 失败: {}".format(e))
        return {"version": "0.0.0", "auto_update": True}


def get_current_version():
    """获取当前版本号"""
    config = load_version_config()
    return config.get('version', '0.0.0')


def is_auto_update_enabled():
    """检查是否启用自动更新"""
    config = load_version_config()
    return config.get('auto_update', True)


def parse_version(version_str):
    """
    解析版本号为可比较的元组

    """
    # 移除 'v' 前缀
    version_str = version_str.lstrip('vV')
    try:
        parts = version_str.split('.')
        return tuple(int(p) for p in parts[:3])  # 只取前三位
    except (ValueError, AttributeError):
        return (0, 0, 0)


def compare_versions(current, latest):
    """
    比较两个版本号
    
    返回：
    1: latest > current (有新版本)
    0: latest == current (版本相同)
    -1: latest < current (本地版本更新)
    """
    current_tuple = parse_version(current)
    latest_tuple = parse_version(latest)
    
    if latest_tuple > current_tuple:
        return 1
    elif latest_tuple == current_tuple:
        return 0
    else:
        return -1


def check_for_updates():
    """
    检查是否有新版本（异步执行）
    """
    try:
        # Python 2/3 兼容的 HTTP 请求
        try:
            # Python 3
            from urllib.request import urlopen, Request
            from urllib.error import URLError
        except ImportError:
            # Python 2
            from urllib2 import urlopen, Request, URLError
        
        # 设置 User-Agent（GitHub API 要求）
        request = Request(GITHUB_API_URL)
        request.add_header('User-Agent', 'YB-Tools-Updater')
        
        # 请求 GitHub API
        response = urlopen(request, timeout=UPDATE_CHECK_TIMEOUT)
        data = json.loads(response.read().decode('utf-8'))
        
        latest_version = data.get('tag_name', '').lstrip('vV')
        
        # 比较版本
        if compare_versions(get_current_version(), latest_version) >= 0:
            return {
                'has_update': False,
                'latest_version': latest_version,
                'download_url': None,
                'release_notes': None
            }
        
        # 查找 zip 下载链接
        download_url = None
        assets = data.get('assets', [])
        for asset in assets:
            if asset.get('name', '').endswith('.zip'):
                download_url = asset.get('browser_download_url')
                break
        
        # 如果没有上传 zip，使用源码 zip
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
        # 静默失败，不影响 Nuke 启动
        print("[YB Tools] 检查更新失败: {}".format(str(e)))
        return None


def download_update(download_url, target_path):
    """
    下载更新包
    
    返回：
    True: 下载成功
    False: 下载失败
    """
    try:
        try:
            from urllib.request import urlretrieve
        except ImportError:
            from urllib import urlretrieve
        
        print("[YB Tools] 正在下载更新...")
        urlretrieve(download_url, target_path)
        print("[YB Tools] 下载完成: {}".format(target_path))
        return True
        
    except Exception as e:
        print("[YB Tools] 下载更新失败: {}".format(str(e)))
        return False


def apply_update(zip_path):
    """
    应用更新（解压到插件目录）
    
    注意：
    这个函数应该在 Nuke 下次启动时执行
    当前运行中的文件无法被替换
    """
    plugin_root = get_plugin_root()
    temp_dir = None
    
    try:
        # 创建临时目录
        temp_dir = tempfile.mkdtemp(prefix='yb_tools_update_')
        
        # 解压更新包
        print("[YB Tools] 正在解压更新包...")
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            zip_ref.extractall(temp_dir)
        
        # 查找解压后的根目录
        # GitHub 的 zipball 会有一个额外的目录层级
        extracted_items = os.listdir(temp_dir)
        if len(extracted_items) == 1 and os.path.isdir(os.path.join(temp_dir, extracted_items[0])):
            source_dir = os.path.join(temp_dir, extracted_items[0])
        else:
            source_dir = temp_dir
        
        # 备份当前版本
        backup_dir = plugin_root + '_backup'
        if os.path.exists(backup_dir):
            shutil.rmtree(backup_dir)
        shutil.copytree(plugin_root, backup_dir)
        print("[YB Tools] 已备份当前版本到: {}".format(backup_dir))
        
        # 复制新文件（排除某些文件）
        exclude_patterns = ['.git', '__pycache__', '*.pyc', '.update_pending', '_backup']
        
        for item in os.listdir(source_dir):
            # 跳过排除的文件/目录
            if any(pattern in item for pattern in exclude_patterns):
                continue
            
            source_item = os.path.join(source_dir, item)
            target_item = os.path.join(plugin_root, item)
            
            # 删除旧文件/目录
            if os.path.exists(target_item):
                if os.path.isdir(target_item):
                    shutil.rmtree(target_item)
                else:
                    os.remove(target_item)
            
            # 复制新文件/目录
            if os.path.isdir(source_item):
                shutil.copytree(source_item, target_item)
            else:
                shutil.copy2(source_item, target_item)
        
        print("[YB Tools] 更新应用成功！")
        return True
        
    except Exception as e:
        print("[YB Tools] 应用更新失败: {}".format(str(e)))
        
        # 尝试恢复备份
        if backup_dir and os.path.exists(backup_dir):
            try:
                shutil.rmtree(plugin_root)
                shutil.copytree(backup_dir, plugin_root)
                print("[YB Tools] 已恢复到备份版本")
            except Exception:
                pass
        
        return False
        
    finally:
        # 清理临时目录
        if temp_dir and os.path.exists(temp_dir):
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass
        
        # 删除更新包
        if os.path.exists(zip_path):
            try:
                os.remove(zip_path)
            except Exception:
                pass


def check_pending_update():
    """
    检查是否有待应用的更新
    
    如果上次下载了更新包但还没应用，现在应用它
    """
    plugin_root = get_plugin_root()
    marker_file = os.path.join(plugin_root, UPDATE_MARKER_FILE)
    
    if not os.path.exists(marker_file):
        return
    
    try:
        # 读取标记文件
        with open(marker_file, 'r') as f:
            data = json.load(f)
        
        zip_path = data.get('zip_path')
        latest_version = data.get('latest_version')
        
        if not zip_path or not os.path.exists(zip_path):
            # 更新包不存在，删除标记
            os.remove(marker_file)
            return
        
        # 询问用户是否应用更新
        try:
            import nuke
            result = nuke.ask(
                "YB Tools 有新版本 v{} 已下载完成！\n\n"
                "是否现在更新？\n\n"
                "注意：更新过程中 Nuke 可能会短暂卡顿".format(latest_version)
            )
            
            if result:
                # 应用更新
                if apply_update(zip_path):
                    os.remove(marker_file)
                    nuke.message(
                        "YB Tools 已更新到 v{}！\n\n"
                        "请重启 Nuke 以使用新版本。".format(latest_version)
                    )
                else:
                    nuke.message("更新失败，请手动下载安装。")
            else:
                # 用户取消，保留标记，下次启动再问
                pass
                
        except ImportError:
            # 不在 Nuke 环境中
            pass
            
    except Exception as e:
        print("[YB Tools] 检查待更新失败: {}".format(str(e)))
        # 删除损坏的标记文件
        try:
            os.remove(marker_file)
        except Exception:
            pass


def download_update_async(update_info):
    """
    后台下载更新（异步执行）
    """
    def _download_thread():
        plugin_root = get_plugin_root()
        zip_path = os.path.join(tempfile.gettempdir(), 'yb_tools_update.zip')
        
        # 下载更新
        if download_update(update_info['download_url'], zip_path):
            # 创建标记文件
            marker_file = os.path.join(plugin_root, UPDATE_MARKER_FILE)
            try:
                with open(marker_file, 'w') as f:
                    json.dump({
                        'zip_path': zip_path,
                        'latest_version': update_info['latest_version'],
                        'release_notes': update_info['release_notes']
                    }, f)
                
                # 通知用户（如果在 Nuke 中）
                try:
                    import nuke
                    nuke.executeInMainThread(lambda: nuke.message(
                        "YB Tools 新版本 v{} 已下载完成！\n\n"
                        "下次启动 Nuke 时将提示更新。".format(update_info['latest_version'])
                    ))
                except Exception:
                    pass
                    
            except Exception as e:
                print("[YB Tools] 创建更新标记失败: {}".format(str(e)))
    
    # 启动后台线程
    thread = threading.Thread(target=_download_thread)
    thread.daemon = True  # 守护线程，不阻塞 Nuke 退出
    thread.start()


def start_update_check():
    """
    启动更新检查（在 Nuke 启动时调用）
    
    工作流程：
    1. 检查 version.json 中的 auto_update 配置
    2. 如果启用，首先检查是否有待应用的更新
    3. 然后在后台检查 GitHub 上的新版本
    4. 如果有新版本，后台下载
    
    整个过程不阻塞 Nuke 启动
    """
    # 检查是否启用自动更新
    if not is_auto_update_enabled():
        print("[YB Tools] 自动更新已禁用（在 version.json 中设置）")
        return
    
    # 先检查待应用的更新
    check_pending_update()
    
    # 后台检查新版本
    def _check_thread():
        update_info = check_for_updates()
        
        if update_info and update_info['has_update']:
            print("[YB Tools] 发现新版本: v{}".format(update_info['latest_version']))
            
            # 开始后台下载
            download_update_async(update_info)
        else:
            print("[YB Tools] 当前已是最新版本 v{}".format(get_current_version()))
    
    # 启动检查线程
    thread = threading.Thread(target=_check_thread)
    thread.daemon = True
    thread.start()


# 手动更新功能（可以添加到菜单）
def manual_update_check():
    """
    手动检查更新（用户主动触发）
    """
    try:
        import nuke
        
        # 显示检查中的提示
        print("[YB Tools] 正在检查更新...")
        
        update_info = check_for_updates()
        
        if not update_info:
            nuke.message("检查更新失败，请检查网络连接。")
            return
        
        if not update_info['has_update']:
            nuke.message(
                "当前已是最新版本！\n\n"
                "当前版本: v{}\n"
                "最新版本: v{}".format(get_current_version(), update_info['latest_version'])
            )
            return
        
        # 询问是否下载更新
        result = nuke.ask(
            "发现新版本！\n\n"
            "当前版本: v{}\n"
            "最新版本: v{}\n\n"
            "更新内容:\n{}\n\n"
            "是否立即下载？".format(
                get_current_version(),
                update_info['latest_version'],
                update_info.get('release_notes', '暂无说明')[:200]
            )
        )
        
        if result:
            nuke.message("正在后台下载更新，请稍候...")
            download_update_async(update_info)
        
    except ImportError:
        print("[YB Tools] 手动更新检查需要在 Nuke 环境中执行")

