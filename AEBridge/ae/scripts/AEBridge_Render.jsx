// AEBridge_Render.jsx
(function () {
    // ==================== Utility Functions ====================
    // Generate timestamp string (ExtendScript compatible)
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
    
    // ==================== Silent Mode Configuration ====================
    // Disable security warnings for network file access
    app.preferences.savePrefAsLong("Main Pref Section", "Pref_SCRIPTING_FILE_NETWORK_SECURITY", 1);
    
    // ==================== Progress Monitor ====================
    var progressWin = null;
    var progressBar = null;
    var progressText = null;
    var cancelFlag = false;
    
    function createProgressWindow() {
        progressWin = new Window("palette", "AEBridge - Rendering...", undefined, {closeButton: true});
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
        
        var cancelBtn = btnGroup.add("button", undefined, "Cancel");
        cancelBtn.onClick = function() {
            if (confirm("Are you sure you want to cancel rendering?")) {
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
    
    function updateProgress(text, value) {
        if (progressWin && progressWin.visible) {
            if (progressText) progressText.text = text;
            if (progressBar) progressBar.value = value;
            progressWin.update();
        }
    }
    
    function logError(msg) {
        var logFile = new File(Folder.temp.fsName + "/aebridge_render_error.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] ERROR: " + msg);
        logFile.close();
    }
    
    function alertAndStop(msg) {
        logError(msg);
        if (progressWin) progressWin.close();
        alert(msg);
        throw new Error(msg);
    }

    createProgressWindow();
    updateProgress("Reading render configuration...", 5);
    
    var configPath = (typeof AEBRIDGE_RENDER_CONFIG_PATH !== 'undefined') ? AEBRIDGE_RENDER_CONFIG_PATH : null;
    if (!configPath) {
        alertAndStop("ERROR: Render config path not found!\n\nPlease generate render script via Nuke refresh output button.");
    }

    var cfgFile = new File(configPath);
    if (!cfgFile.exists) {
        alertAndStop("ERROR: Render config file does not exist!\n\nPath: " + configPath);
    }

    cfgFile.open('r');
    var cfgContent = cfgFile.read();
    cfgFile.close();

    var cfg;
    try {
        cfg = JSON.parse(cfgContent);
    } catch (e) {
        alertAndStop("ERROR: Failed to parse render config JSON!\n\n" + e.toString());
    }

    var projectPath = cfg.project_path;
    var compName = cfg.comp_name || "AEBridge";
    var frameRate = cfg.frame_rate || 24;
    var globalFirst = cfg.global_first || 0;
    var globalLast = cfg.global_last || 0;
    var durationFrames = Math.max(0, globalLast - globalFirst + 1);
    var outputPath = cfg.output_path;
    var outputSettings = cfg.output_settings || {};
    var templateName = outputSettings.template_name || '';
    var desiredChannels = outputSettings.channels || 'RGB + Alpha';
    var desiredDepth = outputSettings.depth || '16 Bits/Channel';
    var desiredColor = outputSettings.color || 'Straight (Unmatted)';
    var desiredQuality = Number(outputSettings.quality || 100);
    var desiredPremult = !!outputSettings.premultiplied;

    if (!projectPath || !outputPath) {
        alertAndStop("ERROR: Render config missing required fields (project_path / output_path).");
    }

    var projectFile = new File(projectPath);
    if (!projectFile.exists) {
        alertAndStop("ERROR: AE project file not found:\n" + projectPath);
    }

    updateProgress("Opening project file...", 10);
    
    app.beginUndoGroup("AEBridge Render");
    try {
        app.exitAfterLaunchAndEval = true;

        if (!app.project || !app.project.file || app.project.file.fsName !== projectFile.fsName) {
            app.open(projectFile);
        }

        updateProgress("Loading composition...", 20);
        var proj = app.project;
        if (!proj) {
            alertAndStop("ERROR: Cannot access After Effects project.");
        }

        var mainComp = null;
        for (var i = 1; i <= proj.items.length; i++) {
            var item = proj.items[i];
            if (item instanceof CompItem && item.name === compName) {
                mainComp = item;
                break;
            }
        }
        if (!mainComp) {
            alertAndStop("ERROR: Composition \"" + compName + "\" not found in project.");
        }

        updateProgress("Configuring composition...", 30);
        
        // Set composition start frame to match Nuke's global first frame
        // Only set when globalFirst is not 0 or 1 to avoid AE warnings
        if (globalFirst !== 0 && globalFirst !== 1) {
            try {
                mainComp.displayStartFrame = globalFirst;
            } catch (e) {
                // Fallback: use displayStartTime if displayStartFrame is not available
                try {
                    mainComp.displayStartTime = (globalFirst - 1) / frameRate;
                } catch (e2) {}
            }
        }

        try {
            mainComp.frameRate = frameRate;
        } catch (e) {}

        try {
            mainComp.duration = durationFrames / frameRate;
        } catch (e) {}

        updateProgress("Preparing render queue...", 40);
        var rq = proj.renderQueue;
        if (!rq) {
            alertAndStop("ERROR: Cannot access Render Queue.");
        }

        rq.queueInAME = false;

        // Remove existing render queue items for this composition
        for (var ri = rq.numItems; ri >= 1; ri--) {
            var rqi = rq.item(ri);
            if (rqi && rqi.comp && rqi.comp === mainComp) {
                try { rqi.remove(); } catch (e) {}
            }
        }

        // Create render queue item
        var rqItem = rq.items.add(mainComp);
        try {
            // Critical: AE render should start from composition time 0 and span the entire duration
            // Do not use globalFirst / frameRate, as composition's displayStartFrame is already set
            // Use Math.max to ensure at least a small positive value to avoid warnings
            rqItem.timeSpanStart = 0;
            rqItem.timeSpanDuration = Math.max(mainComp.duration, 0.001);
        } catch (e) {}

        var om = rqItem.outputModule(1);
        if (!om) {
            alertAndStop("ERROR: Cannot create output module.");
        }

        var outfile = new File(outputPath);
        
        // Ensure output directory exists
        if (outfile.parent && !outfile.parent.exists) {
            try {
                outfile.parent.create();
            } catch (e) {
                alertAndStop("ERROR: Cannot create output directory:\n" + outfile.parent.fsName);
            }
        }

        function applyTemplateByNames(om, names) {
            try {
                var templates = om.templates;
                for (var i = 0; i < templates.length; i++) {
                    for (var j = 0; j < names.length; j++) {
                        if (templates[i] === names[j]) {
                            om.applyTemplate(names[j]);
                            return names[j];
                        }
                    }
                }
            } catch (e) {}
            return null;
        }

        function configureWithTemplate(om, outfile, name) {
            var applied = applyTemplateByNames(om, [name]);
            if (!applied) {
                alert('WARNING: Output template not found: ' + name);
                return false;
            }
            om.file = outfile;
            return true;
        }

        function configurePNGDefault(om, outfile) {
            // Step 1: Set format
            var applied = applyTemplateByNames(om, ['_HIDDEN X-Factor 8', '_HIDDEN X-Factor 8 Premul', 'PNG Sequence', 'Lossless with Alpha', 'Lossless']);
            if (!applied) {
                try {
                    om.setSetting('Output Module Settings', 'Format', 'PNG Sequence');
                } catch (e) {
                    alert('WARNING: Cannot set PNG output format. Please create an output module template containing "PNG" in After Effects.');
                    return false;
                }
            }

            // Step 2: Set channel and quality parameters
            try { om.setSetting('Output Module Settings', 'Channels', desiredChannels); } catch (e) {}
            try { om.setSetting('Output Module Settings', 'Depth', desiredDepth); } catch (e) {}
            try { om.setSetting('Output Module Settings', 'Color', desiredColor); } catch (e) {}
            try { om.setSetting('PNG Options', 'Quality', desiredQuality); } catch (e) {}
            try { om.setSetting('Format Options', 'Quality', desiredQuality); } catch (e) {}
            
            // Step 3: Set starting frame number (critical: must be done before setting file path)
            try {
                om.setSetting('Output Module Settings', 'Start Numbering', globalFirst);
            } catch (e) {
                try {
                    om.setSetting('Output Module Settings', 'Starting #', globalFirst);
                } catch (e2) {}
            }
            
            // Step 4: Set file path (must be last)
            om.file = outfile;

            return true;
        }

        function configureOutputModule(om, outfile) {
            updateProgress("Configuring output module...", 50);
            
            if (templateName && templateName.length > 0) {
                // Set starting frame number when using template
                var success = configureWithTemplate(om, outfile, templateName);
                if (success) {
                    try {
                        om.setSetting('Output Module Settings', 'Start Numbering', globalFirst);
                    } catch (e) {
                        try {
                            om.setSetting('Output Module Settings', 'Starting #', globalFirst);
                        } catch (e2) {}
                    }
                }
                return success;
            }
            return configurePNGDefault(om, outfile);
        }
        
        // Execute configuration
        var configSuccess = configureOutputModule(om, outfile);
        if (!configSuccess) {
            alertAndStop("ERROR: Cannot configure output module. Please check format settings or template name.");
        }
        
        try { om.includeSourceXMP = true; } catch (e) {}
        
        // Final confirmation: set file path again to ensure sequence format is correct
        om.file = outfile;

        updateProgress("Starting render...", 60);
        
        // Start rendering
        rq.render();
        
        updateProgress("Render complete!", 100);
        
        // Display briefly before auto-close
        if (progressWin) {
            $.sleep(1000);
            progressWin.close();
        }
        
        // Write success log
        var logFile = new File(Folder.temp.fsName + "/aebridge_render_success.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] SUCCESS: Render completed");
        logFile.writeln("Output: " + outputPath);
        logFile.close();

    } catch (err) {
        // Close progress window
        if (progressWin) {
            progressWin.close();
        }
        
        // Write error log
        var logFile = new File(Folder.temp.fsName + "/aebridge_render_error.log");
        logFile.open("w");
        logFile.writeln("[" + getTimestamp() + "] ERROR: " + err.toString());
        if (err.line) {
            logFile.writeln("Line: " + err.line);
        }
        logFile.close();
        
        // Show error only if not cancelled by user
        if (err.message !== "User cancelled") {
            alert("AEBridge Render Error:\n\n" + err.toString() + "\n\nError log saved to:\n" + logFile.fsName);
        }
    } finally {
        app.endUndoGroup();
    }
})();
