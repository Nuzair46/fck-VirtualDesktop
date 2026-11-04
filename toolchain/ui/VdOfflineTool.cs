using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using System.Web.Script.Serialization;

internal static class Program
{
    [STAThread]
    private static void Main(string[] args)
    {
        string root = null, python = null, session = null;
        for (int i = 0; i < args.Length; i++)
        {
            if (args[i] == "--root" && i + 1 < args.Length) root = args[++i];
            else if (args[i] == "--python" && i + 1 < args.Length) python = args[++i];
            else if (args[i] == "--session" && i + 1 < args.Length) session = args[++i];
        }
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        if (String.IsNullOrEmpty(root))
        {
            string candidate = Application.StartupPath;
            if (!File.Exists(Path.Combine(candidate, "toolchain_cli.py")))
                candidate = Directory.GetParent(candidate).FullName;
            if (File.Exists(Path.Combine(candidate, "toolchain_cli.py"))) root = candidate;
        }
        if (String.IsNullOrEmpty(python) && !String.IsNullOrEmpty(root))
        {
            string bundled = Path.Combine(root, "runtime", "python", "python.exe");
            if (File.Exists(bundled)) python = bundled;
        }
        if (String.IsNullOrEmpty(root) || String.IsNullOrEmpty(python) || !File.Exists(python))
        {
            MessageBox.Show("Start this tool using Launch-Tool.ps1 so it can locate Python and the tool files.",
                "Virtual Desktop Offline Tool", MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        Application.Run(new OfflineToolForm(Path.GetFullPath(root), Path.GetFullPath(python), session));
    }

    // Windows CRT argument quoting. No command shell is used to run Python.
    internal static string QuoteArgument(string value)
    {
        var text = new StringBuilder("\"");
        int slashes = 0;
        foreach (char c in value)
        {
            if (c == '\\') { slashes++; continue; }
            if (c == '"') { text.Append('\\', slashes * 2 + 1); text.Append('"'); }
            else { text.Append('\\', slashes); text.Append(c); }
            slashes = 0;
        }
        text.Append('\\', slashes * 2);
        text.Append('"');
        return text.ToString();
    }

    internal static string JoinArguments(IEnumerable<string> args)
    {
        var result = new List<string>();
        foreach (string item in args) result.Add(QuoteArgument(item));
        return String.Join(" ", result.ToArray());
    }
}

internal sealed class OfflineToolForm : Form
{
    private readonly string toolRoot;
    private readonly string pythonPath;
    private readonly RadioButton automatic = new RadioButton();
    private readonly RadioButton manual = new RadioButton();
    private readonly TextBox apk = new TextBox();
    private readonly TextBox streamer = new TextBox();
    private readonly TextBox adb = new TextBox();
    private readonly TextBox serial = new TextBox();
    private readonly TextBox state = new TextBox();
    private readonly TextBox session = new TextBox();
    private readonly Button apkBrowse = new Button();
    private readonly Button check = new Button();
    private readonly Button prepare = new Button();
    private readonly Button install = new Button();
    private readonly Button restore = new Button();
    private readonly TextBox log = new TextBox();
    private readonly Label status = new Label();
    private readonly TableLayoutPanel inputs = new TableLayoutPanel();
    private readonly TableLayoutPanel advanced = new TableLayoutPanel();
    private readonly CheckBox advancedToggle = new CheckBox();
    private readonly CheckBox detailsToggle = new CheckBox();
    private readonly Label streamerSummary = new Label();
    private readonly ComboBox headsetChoice = new ComboBox();
    private readonly ComboBox sessionChoice = new ComboBox();
    private string selectedSessionState;
    private string selectedSessionMode;
    private bool refreshingChoices;
    private bool running;

    private sealed class Choice
    {
        public string Label;
        public string Value;
        public override string ToString() { return Label; }
    }

    public OfflineToolForm(string root, string python, string initialSession, bool startDetection = true)
    {
        toolRoot = root;
        pythonPath = python;
        Text = "Virtual Desktop Offline Tool";
        Font = new Font("Segoe UI", 9F);
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(800, 480);
        ClientSize = new Size(850, 510);
        AutoScaleMode = AutoScaleMode.Dpi;

        var layout = new TableLayoutPanel();
        layout.Dock = DockStyle.Fill;
        layout.Padding = new Padding(18);
        layout.ColumnCount = 1;
        layout.RowCount = 8;
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Controls.Add(layout);

        var intro = new Label();
        intro.Text = "Connect your headset or choose your APK. The tool finds your Streamer and uses its included ADB automatically.";
        intro.AutoSize = true;
        intro.MaximumSize = new Size(850, 0);
        intro.Margin = new Padding(0, 0, 0, 12);
        layout.Controls.Add(intro, 0, 0);

        inputs.AutoSize = true;
        inputs.Dock = DockStyle.Top;
        inputs.ColumnCount = 3;
        inputs.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 135F));
        inputs.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
        inputs.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 88F));
        layout.Controls.Add(inputs, 0, 1);

        var modes = new FlowLayoutPanel();
        modes.AutoSize = true;
        modes.WrapContents = true;
        modes.Dock = DockStyle.Fill;
        automatic.Text = "Use connected headset";
        automatic.AutoSize = true;
        automatic.Checked = true;
        manual.Text = "Use an APK file";
        manual.AutoSize = true;
        manual.Margin = new Padding(16, 3, 0, 3);
        modes.Controls.Add(automatic);
        modes.Controls.Add(manual);
        inputs.Controls.Add(MakeLabel("APK source"), 0, 0);
        inputs.Controls.Add(modes, 1, 0);
        inputs.SetColumnSpan(modes, 2);
        automatic.CheckedChanged += delegate { UpdateMode(); };
        manual.CheckedChanged += delegate { UpdateMode(); };

        AddPathRow(1, "Original APK", apk, apkBrowse, delegate { BrowseFile(apk, "Android package (*.apk)|*.apk|All files (*.*)|*.*"); });
        streamerSummary.Text = "Detecting installed Streamer...";
        streamerSummary.AutoSize = true;
        streamerSummary.Anchor = AnchorStyles.Left;
        inputs.Controls.Add(MakeLabel("Streamer"), 0, 2);
        inputs.Controls.Add(streamerSummary, 1, 2);
        var chooseStreamer = new Button { Text = "Choose...", Dock = DockStyle.Fill };
        chooseStreamer.Click += delegate {
            BrowseFile(streamer, "VirtualDesktop.Streamer.exe|VirtualDesktop.Streamer.exe");
            if (!running) StartSourceCommand("scan");
        };
        inputs.Controls.Add(chooseStreamer, 2, 2);
        headsetChoice.DropDownStyle = ComboBoxStyle.DropDownList;
        headsetChoice.Dock = DockStyle.Fill;
        headsetChoice.SelectedIndexChanged += delegate {
            if (!refreshingChoices && headsetChoice.SelectedItem is Choice)
                serial.Text = ((Choice)headsetChoice.SelectedItem).Value;
        };
        inputs.Controls.Add(MakeLabel("Headset"), 0, 3);
        inputs.Controls.Add(headsetChoice, 1, 3);
        inputs.SetColumnSpan(headsetChoice, 2);
        sessionChoice.DropDownStyle = ComboBoxStyle.DropDownList;
        sessionChoice.Dock = DockStyle.Fill;
        sessionChoice.SelectedIndexChanged += delegate {
            if (!refreshingChoices && sessionChoice.SelectedItem is Choice)
                session.Text = ((Choice)sessionChoice.SelectedItem).Value;
        };
        inputs.Controls.Add(MakeLabel("Saved session"), 0, 4);
        inputs.Controls.Add(sessionChoice, 1, 4);
        inputs.SetColumnSpan(sessionChoice, 2);
        session.TextChanged += delegate { ReadSessionState(); UpdateAvailability(); };
        session.ReadOnly = true;
        if (!String.IsNullOrWhiteSpace(initialSession)) session.Text = initialSession;

        advancedToggle.Text = "Advanced options";
        advancedToggle.AutoSize = true;
        advancedToggle.Margin = new Padding(0, 12, 0, 6);
        layout.Controls.Add(advancedToggle, 0, 2);
        advanced.AutoSize = true;
        advanced.Dock = DockStyle.Top;
        advanced.ColumnCount = 3;
        advanced.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 135F));
        advanced.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100F));
        advanced.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 88F));
        advanced.Visible = false;
        layout.Controls.Add(advanced, 0, 3);
        AddPathRow(0, "Streamer override", streamer, null, delegate { BrowseFile(streamer, "VirtualDesktop.Streamer.exe|VirtualDesktop.Streamer.exe"); }, advanced);
        AddPathRow(1, "Custom ADB", adb, null, delegate { BrowseFile(adb, "ADB executable (adb.exe)|adb.exe"); }, advanced);
        AddTextRow(2, "Device override", serial, advanced);
        AddPathRow(3, "Working folder", state, null, delegate { BrowseFolder(state); RefreshSessions(); }, advanced);
        AddPathRow(4, "Session folder", session, null, delegate { BrowseFolder(session); RefreshSessions(); }, advanced);
        advancedToggle.CheckedChanged += delegate {
            advanced.Visible = advancedToggle.Checked;
            ClientSize = new Size(ClientSize.Width, 510 + (advancedToggle.Checked ? 195 : 0) + (detailsToggle.Checked ? 170 : 0));
        };
        var hints = new Label();
        hints.Text = "No APK or Streamer files are supplied. Authorize this PC on the headset, or provide your original APK file.";
        hints.AutoSize = true;
        hints.MaximumSize = new Size(850, 0);
        hints.ForeColor = SystemColors.GrayText;
        hints.Margin = new Padding(0, 8, 0, 10);
        layout.Controls.Add(hints, 0, 4);

        var actions = new FlowLayoutPanel();
        actions.AutoSize = true;
        actions.Dock = DockStyle.Fill;
        actions.Margin = new Padding(0, 0, 0, 10);
        ConfigureButton(check, "Refresh", delegate { StartSourceCommand("scan"); RefreshSessions(); });
        ConfigureButton(prepare, "Prepare patches", delegate { StartSourceCommand("prepare"); });
        ConfigureButton(install, "Install", delegate { StartSessionCommand("install"); });
        ConfigureButton(restore, "Restore originals", delegate { StartSessionCommand("restore"); });
        actions.Controls.AddRange(new Control[] { check, prepare, install, restore });
        detailsToggle.Text = "Show details";
        detailsToggle.AutoSize = true;
        detailsToggle.Margin = new Padding(5, 10, 0, 0);
        detailsToggle.CheckedChanged += delegate {
            log.Visible = detailsToggle.Checked;
            ClientSize = new Size(ClientSize.Width, 510 + (advancedToggle.Checked ? 195 : 0) + (detailsToggle.Checked ? 170 : 0));
        };
        actions.Controls.Add(detailsToggle);
        layout.Controls.Add(actions, 0, 5);

        log.Multiline = true;
        log.ReadOnly = true;
        log.ScrollBars = ScrollBars.Both;
        log.WordWrap = false;
        log.Dock = DockStyle.Fill;
        log.Font = new Font("Consolas", 9F);
        log.BackColor = SystemColors.Window;
        log.Visible = false;
        layout.Controls.Add(log, 0, 6);
        status.Text = "Detecting your setup...";
        status.AutoSize = true;
        status.MaximumSize = new Size(780, 0);
        status.Margin = new Padding(0, 10, 0, 0);
        layout.Controls.Add(status, 0, 7);
        RefreshSessions();
        UpdateMode();
        if (startDetection) Shown += delegate { StartSourceCommand("scan"); };
        FormClosing += delegate(object sender, FormClosingEventArgs e)
        {
            if (running)
            {
                e.Cancel = true;
                MessageBox.Show(this, "An operation is still running. Wait for it to finish before closing the tool.",
                    Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
            }
        };
    }

    private static Label MakeLabel(string text)
    {
        return new Label { Text = text, AutoSize = true, Anchor = AnchorStyles.Left, Margin = new Padding(0, 8, 10, 8) };
    }

    private void AddTextRow(int row, string title, TextBox box, TableLayoutPanel target = null)
    {
        var table = target ?? inputs;
        box.Dock = DockStyle.Fill;
        box.Margin = new Padding(0, 5, 8, 5);
        table.Controls.Add(MakeLabel(title), 0, row);
        table.Controls.Add(box, 1, row);
        table.SetColumnSpan(box, 2);
    }

    private void AddPathRow(int row, string title, TextBox box, Button button, Action action, TableLayoutPanel target = null)
    {
        var table = target ?? inputs;
        box.Dock = DockStyle.Fill;
        box.Margin = new Padding(0, 5, 8, 5);
        if (button == null) button = new Button();
        button.Text = "Browse...";
        button.Dock = DockStyle.Fill;
        button.Margin = new Padding(0, 3, 0, 3);
        button.Click += delegate { action(); };
        table.Controls.Add(MakeLabel(title), 0, row);
        table.Controls.Add(box, 1, row);
        table.Controls.Add(button, 2, row);
    }

    private static void ConfigureButton(Button button, string text, Action action)
    {
        button.Text = text;
        button.AutoSize = true;
        button.Padding = new Padding(9, 4, 9, 4);
        button.Margin = new Padding(0, 0, 9, 0);
        button.Click += delegate { action(); };
    }

    private void BrowseFile(TextBox target, string filter)
    {
        using (var dialog = new OpenFileDialog())
        {
            dialog.Filter = filter;
            dialog.CheckFileExists = true;
            if (File.Exists(target.Text)) dialog.FileName = target.Text;
            if (dialog.ShowDialog(this) == DialogResult.OK) target.Text = dialog.FileName;
        }
    }

    private void BrowseFolder(TextBox target)
    {
        using (var dialog = new FolderBrowserDialog())
        {
            dialog.Description = target == session ? "Select the prepared session folder" : "Select the working folder";
            if (Directory.Exists(target.Text)) dialog.SelectedPath = target.Text;
            if (dialog.ShowDialog(this) == DialogResult.OK) target.Text = dialog.SelectedPath;
        }
    }

    private void UpdateMode()
    {
        apk.Enabled = manual.Checked && !running;
        apkBrowse.Enabled = manual.Checked && !running;
        foreach (Control control in inputs.Controls)
        {
            int row = inputs.GetRow(control);
            if (row == 1) control.Visible = manual.Checked;
            if (row == 3) control.Visible = !manual.Checked;
        }
        UpdateAvailability();
    }

    private static Dictionary<string, object> ReadJson(string path)
    {
        if (new FileInfo(path).Length > 16 * 1024 * 1024) throw new IOException("Session journal is too large.");
        return new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 }
            .Deserialize<Dictionary<string, object>>(File.ReadAllText(path).TrimStart('\ufeff'));
    }

    private static string Value(Dictionary<string, object> data, string key)
    {
        object value;
        return data != null && data.TryGetValue(key, out value) && value != null ? Convert.ToString(value) : "";
    }

    private void ReadSessionState()
    {
        selectedSessionState = selectedSessionMode = null;
        try {
            var data = ReadJson(Path.Combine(session.Text, "session.json"));
            selectedSessionState = Value(data, "state");
            selectedSessionMode = Value(data, "mode");
        } catch (IOException) {} catch (ArgumentException) {} catch (InvalidOperationException) {}
    }

    private static string SessionLabel(Dictionary<string, object> data)
    {
        string state = Value(data, "state"), title;
        switch (state) {
            case "prepared": title = "Ready to install"; break;
            case "installed": title = "Installed"; break;
            case "recovery_required": case "installing": case "restoring": title = "Recovery needed"; break;
            case "rolled_back": case "restored": title = "Originals restored"; break;
            case "prepare_failed": title = "Preparation failed"; break;
            default: title = "Preparing"; break;
        }
        DateTimeOffset created;
        return title + " | " + (DateTimeOffset.TryParse(Value(data, "created_utc"), out created)
            ? created.ToLocalTime().ToString("g") : "Saved session");
    }

    private void RefreshSessions()
    {
        string preferred = session.Text;
        var candidates = new List<string>();
        string work = String.IsNullOrWhiteSpace(state.Text)
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VirtualDesktopOffline")
            : state.Text;
        string folder = Path.Combine(work, "sessions");
        try {
            if (Directory.Exists(folder)) candidates.AddRange(Directory.GetDirectories(folder).OrderByDescending(p => Path.GetFileName(p)).Take(40));
            if (!String.IsNullOrWhiteSpace(preferred) && !candidates.Contains(preferred)) candidates.Insert(0, preferred);
            refreshingChoices = true;
            sessionChoice.Items.Clear();
            foreach (string candidate in candidates) {
                try {
                    var data = ReadJson(Path.Combine(candidate, "session.json"));
                    string label = SessionLabel(data);
                    sessionChoice.Items.Add(new Choice { Label = label, Value = candidate });
                } catch (IOException) {} catch (ArgumentException) {} catch (InvalidOperationException) {}
            }
            int index = -1;
            for (int i = 0; i < sessionChoice.Items.Count; i++)
                if (String.Equals(((Choice)sessionChoice.Items[i]).Value, preferred, StringComparison.OrdinalIgnoreCase)) index = i;
            if (index < 0 && sessionChoice.Items.Count > 0) index = 0;
            sessionChoice.SelectedIndex = index;
            if (index >= 0) session.Text = ((Choice)sessionChoice.Items[index]).Value;
            foreach (Control control in inputs.Controls)
                if (inputs.GetRow(control) == 4) control.Visible = sessionChoice.Items.Count > 0;
        } catch (IOException) {} catch (UnauthorizedAccessException) {}
        finally { refreshingChoices = false; }
        ReadSessionState();
        UpdateAvailability();
    }

    private void UpdateAvailability()
    {
        bool recovery = selectedSessionState == "recovery_required" || selectedSessionState == "installing" || selectedSessionState == "restoring";
        check.Enabled = !running;
        prepare.Enabled = !running && !recovery && selectedSessionState != "installed";
        install.Enabled = !running && selectedSessionState == "prepared";
        restore.Enabled = !running && (recovery || selectedSessionState == "installed");
        restore.Text = recovery ? "Recover originals" : "Restore originals";
        if (!running && recovery) status.Text = "Recovery is needed. Choose Recover originals before preparing or installing again.";
        else if (!running && (selectedSessionState == "rolled_back" || selectedSessionState == "restored"))
            status.Text = "Originals restored. Prepare a new session to try again.";
        else if (!running && selectedSessionState == "installed")
            status.Text = "This session is installed. Restore originals before preparing another version.";
    }

    private void ShowDetection(string text)
    {
        var data = new JavaScriptSerializer { MaxJsonLength = 16 * 1024 * 1024 }
            .Deserialize<Dictionary<string, object>>(text.TrimStart('\ufeff'));
        object raw;
        var pc = data.TryGetValue("streamer", out raw) ? raw as Dictionary<string, object> : null;
        bool found = Value(pc, "Found") == "True" && Value(pc, "IsAmbiguous") != "True";
        streamerSummary.Text = found ? "Detected: version " + Value(pc, "Version") : "Not detected. Choose your Streamer executable.";
        refreshingChoices = true;
        try {
            headsetChoice.Items.Clear();
            object[] devices = data.TryGetValue("devices", out raw) && raw is System.Collections.IEnumerable
                ? ((System.Collections.IEnumerable)raw).Cast<object>().ToArray() : null;
            if (devices != null) foreach (object entry in devices) {
                var item = entry as Dictionary<string, object>;
                if (item == null) continue;
                string id = Value(item, "serial");
                string model = Value(item, "model").Replace('_', ' ');
                string connection = Value(item, "state");
                headsetChoice.Items.Add(new Choice { Value = id,
                    Label = (String.IsNullOrEmpty(model) ? "Headset" : model) + " — " + connection + (devices.Length > 1 ? " (" + id + ")" : "") });
            }
            if (headsetChoice.Items.Count == 0) {
                headsetChoice.Items.Add(new Choice { Value = "", Label = "Connect and authorize your headset, or choose an APK file" });
                headsetChoice.SelectedIndex = 0;
            } else {
                if (headsetChoice.Items.Count > 1) headsetChoice.Items.Insert(0,
                    new Choice { Value = "", Label = "Choose a headset (" + headsetChoice.Items.Count + " detected)" });
                int selected = 0;
                for (int i = 0; i < headsetChoice.Items.Count; i++)
                    if (((Choice)headsetChoice.Items[i]).Value == serial.Text) selected = i;
                headsetChoice.SelectedIndex = selected;
                if (selected >= 0 && !manual.Checked) serial.Text = ((Choice)headsetChoice.Items[selected]).Value;
            }
        } finally { refreshingChoices = false; }
        if (data.ContainsKey("adb_error")) AppendLog("Headset detection: " + Value(data, "adb_error"));
    }

    private static void AddOption(List<string> args, string option, string value)
    {
        if (!String.IsNullOrWhiteSpace(value)) { args.Add(option); args.Add(value.Trim()); }
    }

    private void StartSourceCommand(string command)
    {
        if (running) return;
        if (command == "prepare" && manual.Checked && !File.Exists(apk.Text.Trim()))
        {
            MessageBox.Show(this, "Choose the original APK file first.", Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        if (command == "prepare" && automatic.Checked && headsetChoice.Items.Count > 1 && String.IsNullOrWhiteSpace(serial.Text))
        {
            MessageBox.Show(this, "Choose which connected headset to use.", Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        var args = new List<string> { command };
        if (manual.Checked) {
            if (command == "scan") args.Add("--pc-only");
            else AddOption(args, "--apk", apk.Text);
        }
        AddOption(args, "--streamer", streamer.Text);
        AddOption(args, "--adb", adb.Text);
        AddOption(args, "--serial", serial.Text);
        AddOption(args, "--state-directory", state.Text);
        RunBackend(command, args);
    }

    private void StartSessionCommand(string command)
    {
        if (running) return;
        string selected = session.Text.Trim();
        if (String.IsNullOrEmpty(selected) || !File.Exists(Path.Combine(selected, "session.json")))
        {
            MessageBox.Show(this, "Prepare a session, or browse to a folder containing session.json.",
                Text, MessageBoxButtons.OK, MessageBoxIcon.Information);
            return;
        }
        if (!IsAdministrator())
        {
            var answer = MessageBox.Show(this,
                "Installing or restoring the Streamer in Program Files requires administrator access. Relaunch this tool as administrator with the selected session?",
                Text, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
            if (answer != DialogResult.Yes) return;
            try
            {
                var start = new ProcessStartInfo();
                start.FileName = Application.ExecutablePath;
                start.Arguments = Program.JoinArguments(new string[] { "--root", toolRoot, "--python", pythonPath, "--session", selected });
                start.WorkingDirectory = toolRoot;
                start.UseShellExecute = true;
                start.Verb = "runas";
                Process.Start(start);
                Close();
            }
            catch (Exception ex) { AppendLog("Administrator relaunch did not complete: " + ex.Message); }
            return;
        }
        using (var confirmation = new InstallConfirmation(command == "restore", selectedSessionMode == "adb"))
        {
            if (confirmation.ShowDialog(this) != DialogResult.OK) return;
            var args = new List<string> { command, "--session", selected };
            if (confirmation.AppReplacementApproved) args.Add("--confirm-reinstall");
            if (confirmation.DataLossApproved) args.Add("--allow-data-loss");
            if (confirmation.ForceStopApproved) args.Add("--force-stop");
            if (confirmation.DisableUpdater) args.Add("--disable-updater");
            RunBackend(command, args);
        }
    }

    private static bool IsAdministrator()
    {
        using (WindowsIdentity identity = WindowsIdentity.GetCurrent())
            return new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator);
    }

    private void RunBackend(string operation, List<string> commandArgs)
    {
        string cli = Path.Combine(toolRoot, "toolchain_cli.py");
        if (!File.Exists(cli))
        {
            MessageBox.Show(this, "The backend file toolchain_cli.py is missing from the tool folder.", Text,
                MessageBoxButtons.OK, MessageBoxIcon.Error);
            return;
        }
        running = true;
        inputs.Enabled = false;
        advanced.Enabled = false;
        check.Enabled = prepare.Enabled = install.Enabled = restore.Enabled = false;
        status.Text = "Running " + operation + "...";
        AppendLog("");
        AppendLog("=== " + operation + " ===");
        var args = new List<string> { "-u", cli };
        args.AddRange(commandArgs);
        ThreadPool.QueueUserWorkItem(delegate
        {
            int result = -1;
            string error = null;
            var captured = new StringBuilder();
            try
            {
                var start = new ProcessStartInfo();
                start.FileName = pythonPath;
                start.Arguments = Program.JoinArguments(args);
                start.WorkingDirectory = toolRoot;
                start.UseShellExecute = false;
                start.CreateNoWindow = true;
                start.RedirectStandardOutput = true;
                start.RedirectStandardError = true;
                start.StandardOutputEncoding = Encoding.UTF8;
                start.StandardErrorEncoding = Encoding.UTF8;
                start.EnvironmentVariables["PYTHONUTF8"] = "1";
                start.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
                // UAC relaunch can drop the bootstrapper's environment. Keep
                // Python imports from changing the verified extracted tools.
                start.EnvironmentVariables["PYTHONDONTWRITEBYTECODE"] = "1";
                string dotnetRoot = Path.Combine(toolRoot, "runtime", "dotnet");
                if (File.Exists(Path.Combine(dotnetRoot, "dotnet.exe")))
                {
                    start.EnvironmentVariables["PATH"] = dotnetRoot + ";" + start.EnvironmentVariables["PATH"];
                    start.EnvironmentVariables["DOTNET_ROOT"] = dotnetRoot;
                    start.EnvironmentVariables["DOTNET_ROOT_X64"] = dotnetRoot;
                }
                using (var process = new Process())
                {
                    process.StartInfo = start;
                    process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) {
                        if (e.Data != null) lock (captured) captured.AppendLine(e.Data);
                        QueueLine(e.Data);
                    };
                    process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e) { QueueLine(e.Data); };
                    process.Start();
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    process.WaitForExit();
                    result = process.ExitCode;
                }
            }
            catch (Exception ex) { error = ex.Message; }
            BeginInvoke(new Action(delegate
            {
                if (error != null) AppendLog("ERROR: " + error);
                AppendLog("Operation finished with exit code " + result + ".");
                if (operation == "scan" && result == 0) {
                    try { ShowDetection(captured.ToString()); }
                    catch (Exception ex) { AppendLog("Detection response could not be read: " + ex.Message); }
                }
                status.Text = result == 0 ? (operation == "scan" ? "Ready. Prepare patches to continue." : "Finished: " + operation + ".")
                    : "The operation did not complete. See details below.";
                if (result != 0) detailsToggle.Checked = true;
                running = false;
                inputs.Enabled = true;
                advanced.Enabled = true;
                RefreshSessions();
                UpdateMode();
            }));
        });
    }

    private void QueueLine(string line)
    {
        if (line == null) return;
        BeginInvoke(new Action(delegate
        {
            if (line.StartsWith("SESSION_PATH=", StringComparison.Ordinal))
            {
                string path = line.Substring("SESSION_PATH=".Length).Trim();
                if (Path.IsPathRooted(path)) session.Text = path;
            }
            AppendLog(line);
        }));
    }

    private void AppendLog(string line)
    {
        if (log.TextLength > 2000000) log.Text = log.Text.Substring(log.TextLength - 1500000);
        log.AppendText(line + Environment.NewLine);
    }
}

internal sealed class InstallConfirmation : Form
{
    private readonly CheckBox replacement = new CheckBox();
    private readonly RadioButton preserve = new RadioButton();
    private readonly RadioButton discard = new RadioButton();
    private readonly CheckBox forceStop = new CheckBox();
    private readonly CheckBox disableUpdater = new CheckBox();
    private readonly bool restoring;
    public bool AppReplacementApproved { get { return replacement.Checked; } }
    public bool DataLossApproved { get { return replacement.Checked && discard.Checked; } }
    public bool ForceStopApproved { get { return forceStop.Checked; } }
    public bool DisableUpdater { get { return !restoring && disableUpdater.Checked; } }

    public InstallConfirmation(bool restore, bool usesHeadset)
    {
        restoring = restore;
        Text = restore ? "Restore this session" : "Install this session";
        Font = new Font("Segoe UI", 9F);
        FormBorderStyle = FormBorderStyle.FixedDialog;
        StartPosition = FormStartPosition.CenterParent;
        MinimizeBox = false;
        MaximizeBox = false;
        ShowInTaskbar = false;
        ClientSize = new Size(620, restore ? 315 : 355);
        var panel = new TableLayoutPanel();
        panel.Dock = DockStyle.Fill;
        panel.Padding = new Padding(18);
        panel.ColumnCount = 1;
        panel.RowCount = restore ? 6 : 7;
        for (int i = 0; i < panel.RowCount; i++) panel.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Controls.Add(panel);
        var summary = new Label();
        summary.AutoSize = true;
        summary.MaximumSize = new Size(550, 0);
        summary.Text = usesHeadset
            ? "This changes the Streamer and headset recorded in this session. Replacing the headset APK may require uninstalling it first. Your original files and verified backups are kept for recovery."
            : "This changes the PC Streamer only. Your prepared APK remains available in the session output folder for manual installation.";
        summary.Margin = new Padding(0, 0, 0, 14);
        panel.Controls.Add(summary);
        replacement.Text = "I approve uninstalling and reinstalling the headset app if required.";
        replacement.AutoSize = true;
        replacement.Margin = new Padding(0, 0, 0, 10);
        panel.Controls.Add(replacement);
        preserve.Text = "Preserve app data using a root backup; stop if preservation is unavailable.";
        preserve.AutoSize = true;
        preserve.Checked = true;
        preserve.Margin = new Padding(18, 0, 0, 6);
        panel.Controls.Add(preserve);
        discard.Text = "Allow a clean headset installation and loss of existing app data.";
        discard.AutoSize = true;
        discard.Margin = new Padding(18, 0, 0, 12);
        panel.Controls.Add(discard);
        replacement.Visible = preserve.Visible = discard.Visible = usesHeadset;
        forceStop.Text = "Allow stopping running Virtual Desktop processes if needed.";
        forceStop.AutoSize = true;
        forceStop.Margin = new Padding(0, 0, 0, 15);
        panel.Controls.Add(forceStop);
        disableUpdater.Text = "Disable Streamer updater (rename Updater.exe to Updater.exe.bak).";
        disableUpdater.AutoSize = true;
        disableUpdater.Margin = new Padding(0, 0, 0, 15);
        disableUpdater.Visible = !restore;
        if (!restore) panel.Controls.Add(disableUpdater);
        var actions = new FlowLayoutPanel();
        actions.AutoSize = true;
        actions.FlowDirection = FlowDirection.RightToLeft;
        actions.Dock = DockStyle.Fill;
        var cancel = new Button { Text = "Cancel", DialogResult = DialogResult.Cancel, AutoSize = true };
        var proceed = new Button { Text = restore ? "Restore" : "Install", DialogResult = DialogResult.OK, AutoSize = true };
        actions.Controls.Add(cancel);
        actions.Controls.Add(proceed);
        panel.Controls.Add(actions);
        AcceptButton = proceed;
        CancelButton = cancel;
        replacement.CheckedChanged += delegate { preserve.Enabled = discard.Enabled = replacement.Checked; };
        preserve.Enabled = discard.Enabled = false;
    }
}
