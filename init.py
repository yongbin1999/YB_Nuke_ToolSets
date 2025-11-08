# -*- coding: utf-8 -*-
"""
YB Tools - 插件初始化
"""

import nuke
import os
import sys

plugin_path = os.path.dirname(__file__)

# 添加图标路径（根目录下的 yb_logo.png）
nuke.pluginAddPath(plugin_path)

# 添加 AEBridge 模块路径（让 Python 能导入 AEBridge 模块）
nuke.pluginAddPath(os.path.join(plugin_path, 'AEBridge'))

# 添加插件根目录到 Python 路径（让 updater 模块可以被导入）
if plugin_path not in sys.path:
    sys.path.insert(0, plugin_path)

# 启动自动更新检查（异步，不阻塞启动）
try:
    import updater
    updater.start_update_check()
except Exception as e:
    # 更新功能失败不影响插件主功能
    print("[YB Tools] 自动更新功能启动失败: {}".format(str(e)))

