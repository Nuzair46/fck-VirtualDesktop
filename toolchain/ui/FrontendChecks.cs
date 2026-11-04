using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;

internal static class FrontendChecks
{
    static T Field<T>(object target, string name) {
        return (T)target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(target);
    }
    static object Call(object target, string name, params object[] args) {
        return target.GetType().GetMethod(name, BindingFlags.Instance | BindingFlags.NonPublic).Invoke(target, args);
    }
    static void Check(bool value, string message) { if (!value) throw new Exception(message); }
    static void Render(Form form, string path) {
        // Make child controls participate in native layout without displaying
        // a window on the user's screen. Startup detection is disabled above.
        form.ShowInTaskbar = false;
        form.StartPosition = FormStartPosition.Manual;
        form.Location = new Point(-20000, -20000);
        form.Opacity = 0;
        form.Show(); Application.DoEvents(); form.PerformLayout();
        using (var bitmap = new Bitmap(form.Width, form.Height)) {
            form.DrawToBitmap(bitmap, new Rectangle(Point.Empty, bitmap.Size));
            bitmap.Save(path, System.Drawing.Imaging.ImageFormat.Png);
        }
        form.Hide();
    }
    [STAThread]
    static int Main(string[] args) {
        try {
            string root = Path.GetFullPath(args[0]), output = Path.GetFullPath(args[1]);
            Directory.CreateDirectory(output);
            string fixture = Path.Combine(output, "session-fixture");
            Directory.CreateDirectory(fixture);
            string journal = Path.Combine(fixture, "session.json");
            File.WriteAllText(journal, "{\"schema_version\":2,\"state\":\"prepared\",\"mode\":\"adb\"}");
            Application.EnableVisualStyles();
            var assembly = Assembly.LoadFrom(Path.Combine(root, "tools", "VdOfflineTool.exe"));
            var type = assembly.GetType("OfflineToolForm", true);
            using (var form = (Form)Activator.CreateInstance(type, new object[] {
                root, Path.Combine(root, "runtime", "python", "python.exe"), fixture, false })) {
                Check(!Field<CheckBox>(form, "advancedToggle").Checked, "Advanced options must default to collapsed.");
                Check(!Field<CheckBox>(form, "detailsToggle").Checked, "Technical logs must default to collapsed.");
                foreach (string name in new [] {"adb", "serial", "state", "session"})
                    Check(Field<Control>(form, name).Parent == Field<Control>(form, "advanced"), "Technical override leaked into the default form: " + name);
                Check(Field<Button>(form, "install").Enabled, "A prepared session should allow installation.");
                Call(form, "ShowDetection", "{\"streamer\":{\"Found\":true,\"IsAmbiguous\":false,\"Version\":\"1.34.25.0\"},\"devices\":[{\"serial\":\"one\",\"model\":\"Quest_Pro\",\"state\":\"device\"},{\"serial\":\"two\",\"model\":\"Quest_Pro\",\"state\":\"device\"}]}");
                Check(Field<ComboBox>(form, "headsetChoice").Items.Count == 3, "Both detected headsets and a selection prompt must be present.");
                Check(Field<TextBox>(form, "serial").Text == "", "Do not silently choose between multiple headsets.");
                Check(Field<Label>(form, "streamerSummary").Text.Contains("1.34.25.0"), "Show the detected Streamer version.");
                Render(form, Path.Combine(output, "automatic.png"));
                Field<RadioButton>(form, "manual").Checked = true;
                Render(form, Path.Combine(output, "manual.png"));
                File.WriteAllText(journal, "{\"schema_version\":2,\"state\":\"recovery_required\",\"mode\":\"adb\"}");
                Call(form, "ReadSessionState"); Call(form, "UpdateAvailability");
                Check(!Field<Button>(form, "install").Enabled && !Field<Button>(form, "prepare").Enabled, "An interrupted session must block new installations.");
                Check(Field<Button>(form, "restore").Enabled && Field<Button>(form, "restore").Text == "Recover originals", "Recovery must be the available action.");
                Render(form, Path.Combine(output, "recovery.png"));
                File.WriteAllText(journal, "{\"schema_version\":2,\"state\":\"rolled_back\",\"mode\":\"adb\"}");
                Call(form, "ReadSessionState"); Call(form, "UpdateAvailability");
                Check(Field<Button>(form, "prepare").Enabled && !Field<Button>(form, "install").Enabled, "A recovered session must prepare fresh files before installation.");
            }
            Console.WriteLine("Frontend checks passed: defaults, device selection, and recovery actions. No ADB or app commands executed.");
            return 0;
        } catch (Exception ex) { Console.Error.WriteLine(ex); return 1; }
    }
}
