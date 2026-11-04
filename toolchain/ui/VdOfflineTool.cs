using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Windows.Forms;

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
    private bool running;

    public OfflineToolForm(string root, string python, string initialSession)
    {
        toolRoot = root;
        pythonPath = python;
        Text = "Virtual Desktop Offline Tool";
        Font = new Font("Segoe UI", 9F);
        StartPosition = FormStartPosition.CenterScreen;
        MinimumSize = new Size(820, 670);
        ClientSize = new Size(930, 740);
        AutoScaleMode = AutoScaleMode.Dpi;

        var layout = new TableLayoutPanel();
        layout.Dock = DockStyle.Fill;
        layout.Padding = new Padding(18);
        layout.ColumnCount = 1;
        layout.RowCount = 6;
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        layout.RowStyles.Add(new RowStyle(SizeType.Percent, 100F));
        layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
        Controls.Add(layout);

        var intro = new Label();
        intro.Text = "Prepare a paired Streamer and APK from your original files. Install or restore a prepared session separately.";
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
        automatic.Text = "Extract installed APK using ADB";
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
        AddPathRow(2, "Streamer EXE", streamer, null, delegate { BrowseFile(streamer, "VirtualDesktop.Streamer.exe|VirtualDesktop.Streamer.exe|Executables (*.exe)|*.exe"); });
        AddPathRow(3, "ADB executable", adb, null, delegate { BrowseFile(adb, "ADB executable (adb.exe)|adb.exe|Executables (*.exe)|*.exe"); });
        AddTextRow(4, "Device serial", serial);
        AddPathRow(5, "Working folder", state, null, delegate { BrowseFolder(state); });
        AddPathRow(6, "Prepared session", session, null, delegate { BrowseFolder(session); });
        session.ReadOnly = true;
        if (!String.IsNullOrWhiteSpace(initialSession)) session.Text = initialSession;

        string defaultStreamer = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "Virtual Desktop Streamer", "VirtualDesktop.Streamer.exe");
        if (File.Exists(defaultStreamer)) streamer.Text = defaultStreamer;
        var hints = new Label();
        hints.Text = "Leave ADB, device serial, or working folder blank to use automatic detection and defaults. Manual APK mode can prepare files without a connected headset.";
        hints.AutoSize = true;
        hints.MaximumSize = new Size(850, 0);
        hints.ForeColor = SystemColors.GrayText;
        hints.Margin = new Padding(0, 8, 0, 10);
        layout.Controls.Add(hints, 0, 2);

        var actions = new FlowLayoutPanel();
        actions.AutoSize = true;
        actions.Dock = DockStyle.Fill;
        actions.Margin = new Padding(0, 0, 0, 10);
        ConfigureButton(check, "Check", delegate { StartSourceCommand("scan"); });
        ConfigureButton(prepare, "Prepare patches", delegate { StartSourceCommand("prepare"); });
        ConfigureButton(install, "Install session", delegate { StartSessionCommand("install"); });
        ConfigureButton(restore, "Restore originals", delegate { StartSessionCommand("restore"); });
        actions.Controls.AddRange(new Control[] { check, prepare, install, restore });
        layout.Controls.Add(actions, 0, 3);

        log.Multiline = true;
        log.ReadOnly = true;
        log.ScrollBars = ScrollBars.Both;
        log.WordWrap = false;
        log.Dock = DockStyle.Fill;
        log.Font = new Font("Consolas", 9F);
        log.BackColor = SystemColors.Window;
        layout.Controls.Add(log, 0, 4);
        status.Text = "Ready. Check the environment or prepare a session.";
        status.AutoSize = true;
        status.Margin = new Padding(0, 10, 0, 0);
        layout.Controls.Add(status, 0, 5);
        UpdateMode();
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

    private void AddTextRow(int row, string title, TextBox box)
    {
        box.Dock = DockStyle.Fill;
        box.Margin = new Padding(0, 5, 8, 5);
        inputs.Controls.Add(MakeLabel(title), 0, row);
        inputs.Controls.Add(box, 1, row);
        inputs.SetColumnSpan(box, 2);
    }

    private void AddPathRow(int row, string title, TextBox box, Button button, Action action)
    {
        box.Dock = DockStyle.Fill;
        box.Margin = new Padding(0, 5, 8, 5);
        if (button == null) button = new Button();
        button.Text = "Browse...";
        button.Dock = DockStyle.Fill;
        button.Margin = new Padding(0, 3, 0, 3);
        button.Click += delegate { action(); };
        inputs.Controls.Add(MakeLabel(title), 0, row);
        inputs.Controls.Add(box, 1, row);
        inputs.Controls.Add(button, 2, row);
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
        var args = new List<string> { command };
        if (manual.Checked) AddOption(args, "--apk", apk.Text);
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
        using (var confirmation = new InstallConfirmation(command == "restore"))
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
                    process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e) { QueueLine(e.Data); };
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
                status.Text = result == 0 ? "Finished: " + operation + "." : "Operation did not complete. See the log above.";
                running = false;
                inputs.Enabled = true;
                check.Enabled = prepare.Enabled = install.Enabled = restore.Enabled = true;
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

    public InstallConfirmation(bool restore)
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
        summary.Text = "This changes the installations recorded in the selected session. Replacing the headset APK may require uninstalling it first. Without a connected headset, the prepared APK stays available for manual installation.";
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
