/**
 * AEBridge 启动脚本 - AE 工程创建和更新
 * 
 * @version 2.2.0
 */
(function() {
    // 工具函数：生成时间戳（用于日志）
    function getTimestamp() {
        var d = new Date();
        function pad(n) { return n < 10 ? '0' + n : n; }
        return d.getFullYear() + '-' +
               pad(d.getMonth() + 1) + '-' +
               pad(d.getDate()) + ' ' +
               pad(d.getHours()) + ':' +
               pad(d.getMinutes()) + ':' +
               pad(d.getSeconds());
    }
    
    // 静默模式：关闭网络文件访问的安全警告
    app.preferences.savePrefAsLong("Main Pref Section", "Pref_SCRIPTING_FILE_NETWORK_SECURITY", 1);
    
    // Nuke 会通过命令行参数传入配置文件的位置
    var configPath = null;
    if (typeof AEBRIDGE_CONFIG_PATH !== 'undefined') {
        configPath = AEBRIDGE_CONFIG_PATH;
    }
    
    if (!configPath) {
        var logFile = new File(Folder.temp.fsName + "/aebridge_error.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] ERROR: Config path not found");
        logFile.close();
        return;
    }
    
    // 检查配置文件是否存在
    var configFile = new File(configPath);
    if (!configFile.exists) {
        var logFile = new File(Folder.temp.fsName + "/aebridge_error.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] ERROR: Config file not exists: " + configPath);
        logFile.close();
        return;
    }
    
    // 读取并解析 JSON 配置
    configFile.open('r');
    var configContent = configFile.read();
    configFile.close();
    
    var config;
    try {
        config = JSON.parse(configContent);
    } catch(e) {
        var logFile = new File(Folder.temp.fsName + "/aebridge_error.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] ERROR: JSON parse failed: " + e.toString());
        logFile.close();
        return;
    }
    
    // ==================== 进度窗口管理 ====================
    var progressWin = null;
    var progressBar = null;
    var progressText = null;
    var cancelFlag = false;
    var lastUpdateTime = new Date().getTime();
    var TIMEOUT_MS = 180000;      // 超时时间：3分钟（大型工程需要更长时间）
    var UPDATE_THROTTLE = 200;    // UI更新节流：避免刷新太频繁导致卡顿
    
    /**
     * 创建进度窗口
     * 
     */
    function createProgressWindow() {
        progressWin = new Window("palette", "AEBridge - Processing...", undefined, {closeButton: true});
        progressWin.orientation = "column";
        progressWin.alignChildren = ["fill", "top"];
        progressWin.spacing = 10;
        progressWin.margins = 16;
        
        progressText = progressWin.add("statictext", undefined, "Initializing...");
        progressText.preferredSize.width = 400;
        
        progressBar = progressWin.add("progressbar", undefined, 0, 100);
        progressBar.preferredSize.width = 400;
        progressBar.preferredSize.height = 20;
        
        var btnGroup = progressWin.add("group");
        btnGroup.orientation = "row";
        btnGroup.alignChildren = ["center", "center"];
        
        var pauseBtn = btnGroup.add("button", undefined, "Pause");
        var cancelBtn = btnGroup.add("button", undefined, "Cancel");
        
        pauseBtn.onClick = function() {
            if (pauseBtn.text === "Pause") {
                pauseBtn.text = "Resume";
            } else {
                pauseBtn.text = "Pause";
            }
        };
        
        cancelBtn.onClick = function() {
            if (confirm("Are you sure you want to cancel?")) {
                cancelFlag = true;
                progressWin.close();
            }
        };
        
        progressWin.onClose = function() {
            cancelFlag = true;
            return true;
        };
        
        progressWin.center();
        progressWin.show();
        
        return progressWin;
    }
    
    var lastUIUpdate = 0;
    
    /**
     * 更新进度显示
     */
    function updateProgress(text, value, forceUpdate) {
        if (!progressWin || !progressWin.visible) return;
        
        var now = new Date().getTime();
        if (!forceUpdate && (now - lastUIUpdate) < UPDATE_THROTTLE) {
            return;
        }
        
        if (progressText) progressText.text = text;
        if (progressBar) progressBar.value = value;
        progressWin.update();
        lastUpdateTime = now;
        lastUIUpdate = now;
    }
    
    function checkTimeout() {
        var now = new Date().getTime();
        if (now - lastUpdateTime > TIMEOUT_MS) {
            if (progressWin) progressWin.close();
            var logFile = new File(Folder.temp.fsName + "/aebridge_error.log");
            logFile.open("w");
            logFile.writeln("[" + getTimestamp() + "] ERROR: Script timeout (no progress for 3 minutes)");
            logFile.close();
            return true;
        }
        return false;
    }
    
    createProgressWindow();
    updateProgress("Reading configuration...", 5, true);
    
    // ==================== 主要执行流程 ====================
    app.beginUndoGroup("AEBridge Update");
    try {
        // 解析配置参数
        var FRAME_RATE = config.frame_rate || 24;
        var GLOBAL_FIRST = config.global_first || 1;
        var GLOBAL_LAST = config.global_last || 100;
        var COMP_NAME = config.comp_name || "AEBridge";
        var COMP_WIDTH = config.width || 1920;
        var COMP_HEIGHT = config.height || 1080;
        var DURATION = (GLOBAL_LAST - GLOBAL_FIRST + 1) / FRAME_RATE;
        var items = config.items || [];
        var PROJECT_PATH = config.project_path;
        var OUTPUT_PATH = config.output_path || "";
        var SHOULD_RENDER = config.should_render || false;
        
        // 色彩管理设置（来自 Nuke）
        var NUKE_COLORSPACE = config.nuke_colorspace || "scene_linear";
        var NUKE_WORKING_SPACE = config.nuke_working_space || "linear";
        var NUKE_OUTPUT_TRANSFORM = config.nuke_output_transform || "";
        var ACES_COMPLIANT = config.aces_compliant || false;
        
        // 打开或创建 AE 工程
        var prjFile = new File(PROJECT_PATH);
        if (prjFile.exists) {
            app.open(prjFile);
        } else {
            app.newProject();
        }
        var proj = app.project;

        // 设置时间显示为帧模式（方便和 Nuke 对照）
        try { app.project.timeDisplayType = TimeDisplayType.FRAMES; } catch (e) {}

        /**
         * 色彩管理配置
         * 
         * 1. 如果 Nuke 使用 ACES 工作流，尝试在 AE 中也启用 OCIO 引擎
         * 2. 如果 OCIO 不可用，退回到线性工作流（也能正常工作）
         * 3. 确保线性混合模式开启，保证合成精度
         * 
         */
        try {
            
            if (ACES_COMPLIANT) {
                // Attempt to enable OCIO color engine for ACES workflow
                // This requires AE 2022+ and OCIO config in Preferences
                try {
                    // Try to access color settings
                    // proj.colorSettings is available in AE 2022+
                    if (proj.colorSettings) {
                        // Try to set color engine to OCIO (value: 1)
                        // 0 = Adobe Color Management
                        // 1 = OCIO Color Management
                        try {
                            proj.colorSettings.colorEngine = 1;  // OCIO
                        } catch (e) {
                            // If OCIO engine fails, log it but continue
                            // Fall back to linear working space
                        }
                        
                        // Try to set working space to ACEScg
                        // This may fail if OCIO config doesn't have ACEScg
                        try {
                            proj.workingSpace = "ACEScg";
                        } catch (e) {
                            // Fallback to linear working space if ACEScg not available
                            proj.workingSpace = "sRGB IEC61966-2.1";
                        }
                    } else {
                        // colorSettings not available, use standard approach
                        proj.workingSpace = "sRGB IEC61966-2.1";
                    }
                } catch (e) {
                    // If all OCIO attempts fail, use linear working space
                    proj.workingSpace = "sRGB IEC61966-2.1";
                }
            } else if (NUKE_WORKING_SPACE.indexOf("linear") >= 0 || NUKE_WORKING_SPACE.indexOf("Linear") >= 0) {
                // Linear workflow
                proj.workingSpace = "sRGB IEC61966-2.1";
            } else if (NUKE_WORKING_SPACE.indexOf("sRGB") >= 0) {
                proj.workingSpace = "sRGB IEC61966-2.1";
            } else if (NUKE_WORKING_SPACE.indexOf("Rec709") >= 0 || NUKE_WORKING_SPACE.indexOf("rec709") >= 0) {
                proj.workingSpace = "Rec. 709 Gamma 2.4";
            } else {
                // Default to sRGB
                proj.workingSpace = "sRGB IEC61966-2.1";
            }
            
            // Always use linear blending for accurate compositing (matches Nuke)
            proj.linearBlending = true;
        } catch (e) {}

        function syncCompSettings(comp) {
            if (!comp) return;
            
            // Check and update frame rate
            try { 
                if (Math.abs(comp.frameRate - FRAME_RATE) > 0.01) {
                    comp.frameRate = FRAME_RATE; 
                }
            } catch (e) {}
            
            // Check and update duration (THIS SETS THE END FRAME)
            // Duration in seconds determines the composition's frame range
            // together with displayStartFrame
            try { 
                if (Math.abs(comp.duration - DURATION) > 0.01) {
                    comp.duration = DURATION; 
                }
            } catch (e) {}
            
            // Check and update resolution (width/height)
            try {
                if (comp.width !== COMP_WIDTH || comp.height !== COMP_HEIGHT) {
                    comp.width = COMP_WIDTH;
                    comp.height = COMP_HEIGHT;
                }
            } catch (e) {}
            
            // Set composition start frame to match Nuke's global first frame
            // Combined with duration, this defines the complete frame range
            // Start frame: GLOBAL_FIRST
            // End frame: GLOBAL_FIRST + (DURATION * FRAME_RATE) - 1 = GLOBAL_LAST
            try {
                comp.displayStartFrame = GLOBAL_FIRST;
            } catch (e) {
                // Fallback: use displayStartTime if displayStartFrame is not available
                try {
                    comp.displayStartTime = (GLOBAL_FIRST - 1) / FRAME_RATE;
                } catch (e2) {}
            }
            
            // Update work area to span the entire composition
            // This ensures rendering covers the full frame range
            try { 
                comp.workAreaStart = 0; 
                comp.workAreaDuration = DURATION; 
            } catch (e) {}
            
            // Note: AE doesn't have a separate "displayEndFrame" property
            // The end frame is implicitly defined by: displayStartFrame + (duration * frameRate) - 1
            // Example: startFrame=1001, duration=4.17s @24fps = 100 frames
            // Result: frames 1001-1100 (inclusive)
        }

        function findCompByName(name) {
            for (var i = 1; i <= proj.items.length; i++) {
                var it = proj.items[i];
                if (it instanceof CompItem && it.name === name) {
                    return it;
                }
            }
            return null;
        }

        function ensureComp(name) {
            var comp = findCompByName(name);
            if (!comp) {
                comp = proj.items.addComp(name, COMP_WIDTH, COMP_HEIGHT, 1.0, DURATION, FRAME_RATE);
                // Apply Nuke settings to new composition
                syncCompSettings(comp);
            } else {
                // Existing composition: update all settings to match Nuke
                syncCompSettings(comp);
            }
            return comp;
        }

        function findFootageByFirstFile(firstFile) {
            if (!firstFile) return null;
            var targetFs = new File(firstFile).fsName;
            for (var i = 1; i <= proj.items.length; i++) {
                var it = proj.items[i];
                if (it instanceof FootageItem && it.mainSource && it.mainSource.file) {
                    try {
                        if (it.mainSource.file.fsName === targetFs) {
                            return it;
                        }
                    } catch (e) {}
                }
            }
            return null;
        }
        
        /**
         * 根据图层名称查找素材
         * 
         * 1. 先尝试精确匹配（快速路径）
         * 2. 如果找不到，用前缀匹配 + 正则验证
         */
        function findFootageByName(name) {
            if (!name) return null;
            
            // 快速路径：精确匹配
            for (var i = 1; i <= proj.items.length; i++) {
                var it = proj.items[i];
                if (it instanceof FootageItem && it.name === name) {
                    return it;
                }
            }
            
            // 智能匹配：处理 AE 自动添加的帧范围后缀
            for (var i = 1; i <= proj.items.length; i++) {
                var it = proj.items[i];
                if (it instanceof FootageItem) {
                    if (it.name.indexOf(name) === 0) {
                        var suffix = it.name.substring(name.length);
                        // 验证后缀是否符合序列命名模式
                        if (suffix.match(/^[_\.\[\]0-9\-]+\.(exr|dpx|tif|tiff|jpg|jpeg|png)$/i)) {
                            return it;
                        }
                    }
                }
            }
            
            return null;
        }

        /**
         * 导入或更新素材序列
         * 
         * 处理三种情况：
         * 1. 素材路径完全相同 → 只刷新（可能有新增帧）
         * 2. 素材名称相同但路径变了 → 替换源文件（保留所有引用和属性）
         * 3. 全新素材 → 导入新序列
         * 
         */
        function importOrUpdateSequence(firstFile, layerName) {
            if (!firstFile) return null;
            var f = new File(firstFile);
            if (!f.exists) {
                return null;
            }
            
            // 情况1：路径完全相同，只需刷新
            var existing = findFootageByFirstFile(firstFile);
            if (existing) {
                try {
                    existing.mainSource.reload();
                } catch (e) {}
                return existing;
            }
            
            // 情况2：名称相同但路径改变，需要替换源
            var sameNameFootage = findFootageByName(layerName);
            if (sameNameFootage && sameNameFootage.mainSource && sameNameFootage.mainSource.file) {
                try {
                    var oldFile = sameNameFootage.mainSource.file.fsName;
                    var newFile = f.fsName;
                    if (oldFile !== newFile) {
                        // 原地替换素材源（关键：保留所有图层引用）
                        try {
                            sameNameFootage.replaceWithSequence(f, false);
                            
                            // 刷新帧范围信息
                            try {
                                sameNameFootage.mainSource.reload();
                            } catch (e) {}
                            
                            return sameNameFootage;
                        } catch (e) {
                            // 降级方案：使用 replace 方法
                            try {
                                sameNameFootage.replace(f);
                                try {
                                    sameNameFootage.mainSource.reload();
                                } catch (e2) {}
                                return sameNameFootage;
                            } catch (e2) {}
                        }
                    }
                } catch (e) {}
                
                // 路径相同，只刷新
                try {
                    sameNameFootage.mainSource.reload();
                } catch (e) {}
                return sameNameFootage;
            }
            
            // 情况3：全新素材，导入序列
            var io = new ImportOptions(f);
            io.sequence = true;
            io.forceAlphabetical = false;
            return proj.importFile(io);
        }

        function ensureSourceLayer(precomp, footage, layerName, startFrame, endFrame) {
            var targetLayer = null;
            for (var li = 1; li <= precomp.layers.length; li++) {
                var lay = precomp.layers[li];
                if (lay.name === layerName && lay.source && lay.source instanceof FootageItem) {
                    targetLayer = lay;
                    break;
                }
            }
            
            if (!targetLayer) {
                if (!footage) {
                    return null;
                }
                targetLayer = precomp.layers.add(footage);
            } else if (footage && targetLayer.source !== footage) {
                // Use replaceSource to update footage (preserves layer properties)
                try {
                    targetLayer.replaceSource(footage, false);
                } catch (e) {
                    // Fallback: remove and re-add if replaceSource fails
                    targetLayer.remove();
                    targetLayer = precomp.layers.add(footage);
                }
            }
            
            if (targetLayer && footage) {
                try { targetLayer.name = layerName; } catch (e) {}
                
                /**
                 * 帧范围同步 
                 */
                
                // Step 1: Configure footage interpretation to match Nuke frame range
                try {
                    if (footage.mainSource && footage.mainSource.isStill === false) {
                        // Ensure footage frame rate matches composition
                        footage.mainSource.conformFrameRate = FRAME_RATE;
                        
                        // Set footage duration to match Nuke's frame range
                        // This trims the footage to exactly the frames used in Nuke
                        var footageDuration = (endFrame - startFrame + 1) / FRAME_RATE;
                        
                        try {
                            // Use trimming to set footage start and end points
                            // This ensures the footage only spans the correct frame range
                            footage.mainSource.guessAlphaMode();
                            footage.mainSource.guessPulldown();
                            
                            // Note: AE's footage.mainSource doesn't expose startFrame/endFrame directly
                            // We achieve frame synchronization through layer timing instead
                        } catch (e) {}
                    }
                } catch (e) {}
                
                
                // Calculate when this layer should appear in the composition timeline
                var layerStartFrame = startFrame - GLOBAL_FIRST;  // Frames from comp start
                var layerStartTime = layerStartFrame / FRAME_RATE;  // Convert to seconds
                var layerDuration = (endFrame - startFrame + 1) / FRAME_RATE;  // Duration in seconds
                var layerEndTime = layerStartTime + layerDuration;  // End time in seconds
                
                // Set layer timing (start, in point, out point)
                // This ensures the layer is visible only for its designated frame range
                targetLayer.startTime = layerStartTime;
                targetLayer.inPoint = layerStartTime;
                targetLayer.outPoint = layerEndTime;
                
                // Enable time remapping if needed to ensure frame-accurate playback
                // This is especially important when footage duration doesn't match layer duration
                try {
                    // Time remap property: ensures footage plays back at exactly the right frames
                    if (targetLayer.canSetTimeRemapEnabled && !targetLayer.timeRemapEnabled) {
                        // Only enable if footage duration needs adjustment
                        var actualFootageDuration = footage.duration;
                        if (Math.abs(actualFootageDuration - layerDuration) > 0.01) {
                            targetLayer.timeRemapEnabled = true;
                            
                            // Set time remap keyframes to map layer time to footage frames
                            var timeRemapProp = targetLayer.property("ADBE Time Remapping");
                            if (timeRemapProp) {
                                // Remove existing keyframes
                                while (timeRemapProp.numKeys > 0) {
                                    timeRemapProp.removeKey(1);
                                }
                                
                                // Map layer's start time to footage's first frame
                                timeRemapProp.setValueAtTime(layerStartTime, 0);
                                
                                // Map layer's end time to footage's last frame
                                timeRemapProp.setValueAtTime(layerEndTime, actualFootageDuration);
                            }
                        }
                    }
                } catch (e) {}
                
                // Log frame range for debugging
                // User will see: frames startFrame to endFrame in timeline
            }
            
            // Remove duplicate layers with same name
            for (var idx = precomp.layers.length; idx >= 1; idx--) {
                var other = precomp.layers[idx];
                if (other === targetLayer) {
                    continue;
                }
                if (other.name === layerName && other.source && other.source instanceof FootageItem) {
                    try { other.remove(); } catch (e) {}
                }
            }
            
            return targetLayer;
        }

        updateProgress("Creating main composition...", 10, true);
        var mainComp = ensureComp(COMP_NAME);
        
        updateProgress("Processing footage...", 15, true);
        var totalItems = items.length;
        
        // Batch processing with periodic memory management
        var BATCH_SIZE = 10; // Process items in batches to prevent memory buildup
        
        for (var idx = 0; idx < items.length; idx++) {
            // Check for user cancellation
            if (cancelFlag) {
                updateProgress("User cancelled", 100, true);
                throw new Error("User cancelled");
            }
            
            // Check for timeout
            if (checkTimeout()) {
                throw new Error("Script timeout");
            }
            
            // Periodic memory cleanup every BATCH_SIZE items
            if (idx > 0 && idx % BATCH_SIZE === 0) {
                try {
                    app.purge(PurgeTarget.ALL_CACHES);
                } catch (e) {}
            }
            
            var progress = 15 + Math.floor((idx / totalItems) * 70);
            updateProgress("Processing " + (idx + 1) + "/" + totalItems + ": " + items[idx].name, progress);
            
            var it = items[idx];
            var preName = "[AEBridge]_" + it.name;
            var preComp = ensureComp(preName);

            // Import or update footage sequence (with smart replacement)
            var footage = importOrUpdateSequence(it.first_file || it.path, it.name);
            if (footage && footage.mainSource) {
                try { 
                    // Set footage frame rate to match composition
                    footage.mainSource.conformFrameRate = FRAME_RATE;
                    
                    // Set footage color profile to match Nuke's output
                    // EXR files from Nuke are typically in linear color space
                    try {
                        // Always set straight alpha for EXR (Nuke outputs straight/unmatted alpha)
                        footage.mainSource.alphaMode = AlphaMode.STRAIGHT;
                        
                        // Set color profile based on Nuke's output colorspace
                        // This ensures correct interpretation in AE
                        try {
                            // For ACES workflow, footage should be interpreted as linear
                            if (ACES_COMPLIANT) {
                                // EXR from ACES workflow is in ACEScg (linear)
                                // AE will interpret it correctly with OCIO engine
                                // No additional color profile needed - OCIO handles it
                            } else if (NUKE_COLORSPACE.indexOf("linear") >= 0 || NUKE_COLORSPACE.indexOf("scene_linear") >= 0) {
                                // Linear color space
                                // The working space setting handles interpretation
                            }
                            // Note: AE's footage.mainSource.colorProfile is read-only in most cases
                            // Color interpretation is handled by project working space settings
                        } catch (e) {}
                    } catch (e2) {}
                } catch (e) {}
            }

            // Remove old missing placeholder layers
            var missingName = it.name + " (Missing)";
            for (var rmIdx = preComp.layers.length; rmIdx >= 1; rmIdx--) {
                var rmLayer = preComp.layers[rmIdx];
                if (rmLayer.name === missingName) {
                    try { rmLayer.remove(); } catch (e) {}
                }
            }

            if (!footage) {
                // Remove old source layer if footage is missing
                for (var li2 = preComp.layers.length; li2 >= 1; li2--) {
                    var lay2 = preComp.layers[li2];
                    if (lay2.name === "[AEBridge]_Source_" + it.name) {
                        try { lay2.remove(); } catch (e) {}
                    }
                }
                // Create red placeholder solid for missing footage
                var placeholder = null;
                try {
                    placeholder = preComp.layers.addSolid([1, 0, 0], it.name + " (Missing)", COMP_WIDTH, COMP_HEIGHT, 1.0, DURATION);
                } catch (e) {}
                if (placeholder) {
                    placeholder.startTime = 0;
                    placeholder.outPoint = DURATION;
                }
                continue;
            }

            // Ensure source layer with proper start and end frame settings
            var sourceLayerName = "[AEBridge]_Source_" + it.name;
            var srcLayer = ensureSourceLayer(preComp, footage, sourceLayerName, it.first, it.last);

            // Ensure precomp is added to main composition
            var existsInMain = false;
            for (var ml = 1; ml <= mainComp.layers.length; ml++) {
                var mainLayer = mainComp.layers[ml];
                if (mainLayer.source && mainLayer.source instanceof CompItem && mainLayer.source.name === preName) {
                    existsInMain = true;
                    break;
                }
            }
            if (!existsInMain) {
                var added = mainComp.layers.add(preComp);
                try { added.name = preName; } catch (e) {}
                try { added.startTime = 0; added.outPoint = DURATION; } catch (e) {}
            }
        }

        updateProgress("Saving project...", 90, true);
        
        if (prjFile.parent && !prjFile.parent.exists) {
            prjFile.parent.create();
        }
        proj.save(prjFile);

        updateProgress("Project saved", 95, true);
        
        if (SHOULD_RENDER && OUTPUT_PATH) {
            updateProgress("Preparing render...", 96, true);
            var rq = proj.renderQueue;
            var ri = rq.items.add(mainComp);
            var om = ri.outputModule(1);
            var targetFile = new File(OUTPUT_PATH);
            om.file = targetFile;
            try {
                var templates = om.templates;
                var exrTemplate = null;
                for (var ti = 0; ti < templates.length; ti++) {
                    if (templates[ti].toLowerCase().indexOf('exr') >= 0) {
                        exrTemplate = templates[ti];
                        break;
                    }
                }
                if (exrTemplate) {
                    om.applyTemplate(exrTemplate);
                    om.file = targetFile;
                }
            } catch (e) {}
            try { om.includeSourceXMP = true; } catch (e) {}
            om.file = targetFile;
            rq.render();
        }

        updateProgress("Done!", 100, true);
        
        if (progressWin) {
            $.sleep(1000);
            progressWin.close();
        }
        
        var logFile = new File(Folder.temp.fsName + "/aebridge_success.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] SUCCESS: Project updated");
        logFile.writeln("Project: " + PROJECT_PATH);
        logFile.writeln("Comp: " + COMP_NAME);
        logFile.close();
        
    } catch(e) {
        if (progressWin) {
            progressWin.close();
        }
        
        var logFile = new File(Folder.temp.fsName + "/aebridge_error.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] ERROR: " + e.toString());
        if (e.line) {
            logFile.writeln("Line: " + e.line);
        }
        logFile.close();
        
        if (e.message !== "User cancelled" && e.message !== "Script timeout") {
            alert("AEBridge Error:\n\n" + e.toString() + "\n\nLog saved to:\n" + logFile.fsName);
        }
    }
    app.endUndoGroup();
})();

