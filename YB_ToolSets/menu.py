# -*- coding: utf-8 -*-
"""
YB Tools - 菜单注册

这个文件在 Nuke 界面加载完成后执行
负责在节点菜单中添加我们的工具，让用户可以创建 AEBridge 节点
"""

import nuke
import os
import sys

plugin_path = os.path.dirname(__file__)

# 确保 Python 能找到 AEBridge 模块
aebridge_path = os.path.join(plugin_path, 'AEBridge')
if aebridge_path not in sys.path:
    sys.path.insert(0, aebridge_path)

# 在 Nodes 菜单下创建 YB 子菜单（带我们的 Logo）
yb_menu = nuke.menu('Nodes').addMenu('YB', icon='yb_logo.png')

# 记录加载失败的工具（方便排查问题）
failed_tools = []

# 注册 AE Bridge 工具
try:
    from AEBridge import ae_bridge
    yb_menu.addCommand(
        'AE Bridge',
        lambda: ae_bridge.create_ae_bridge_node()
    )
    # 注册回调：自动管理输入端口（类似 Merge 节点的行为）
    ae_bridge.register_aebridge_callbacks()
except Exception as e:
    failed_tools.append('AE Bridge')
    import traceback
    traceback.print_exc()


# 添加菜单分隔符和实用工具
yb_menu.addSeparator()

# 添加手动检查更新功能
try:
    import updater
    yb_menu.addCommand(
        '检查更新...',
        lambda: updater.manual_update_check(),
        icon=''  # 不使用图标
    )
    yb_menu.addCommand(
        '关于 YB Tools',
        lambda: nuke.message(
            "YB Tools v{}\n\n"
            "Nuke 节点工具集\n"
            "包含 AE Bridge 等实用工具\n\n"
            "作者: YB_\n"
            "项目主页:\n"
            "github.com/yongbin1999/YB_Nuke_ToolSets".format(updater.get_current_version())
        ),
        icon=''
    )
except Exception as e:
    print("[YB Tools] 更新菜单加载失败: {}".format(str(e)))

# 启动日志：让用户知道插件加载状态
if failed_tools:
    print("[YB Tools] 部分工具加载失败: {}".format(', '.join(failed_tools)))
else:
    print("[YB Tools] 所有工具加载成功！")
    
# 显示当前版本
try:
    import updater
    print("[YB Tools] 当前版本: v{}".format(updater.get_current_version()))
except Exception:
    pass

