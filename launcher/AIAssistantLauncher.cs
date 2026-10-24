// "AI Assistant" launcher: starts STE Slicer (if needed) and opens the assistant chat as a separate window.
// Built with the C# compiler that ships with .NET Framework 4 (present on every Windows 10/11), C# 5 syntax.

using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

static class Launcher
{
    const int StartTimeoutSeconds = 240;

    static string AppData { get { return Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData); } }
    static string LocalAppData { get { return Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData); } }
    static string PortFile { get { return Path.Combine(AppData, "steslicer", "ai_assistant.json"); } }
    static string OwnDir { get { return Path.Combine(LocalAppData, "STE_AI_Assistant"); } }
    static string SlicerPathFile { get { return Path.Combine(OwnDir, "slicer_path.txt"); } }

    [STAThread]
    static void Main()
    {
        Application.EnableVisualStyles();
        try
        {
            string url = RunningChatUrl();
            if (url == null)
            {
                if (Process.GetProcessesByName("STESlicer").Length == 0)
                {
                    string exe = FindSlicer();
                    if (exe == null) return;
                    DateTime started = DateTime.Now;
                    Process.Start(new ProcessStartInfo(exe) { WorkingDirectory = Path.GetDirectoryName(exe) });
                    url = WaitForChat(started);
                }
                else
                {
                    url = WaitForChat(DateTime.MinValue);
                }
            }
            if (url == null)
            {
                MessageBox.Show("STE Slicer запущен, но чат AI Assistant не отвечает.\n\n" +
                                "Проверьте, что плагин AI Assistant установлен (install.bat), и перезапустите слайсер.",
                                "AI Assistant", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            OpenWindow(url);
        }
        catch (Exception e)
        {
            MessageBox.Show("Не удалось запустить AI Assistant:\n" + e.Message, "AI Assistant",
                            MessageBoxButtons.OK, MessageBoxIcon.Error);
        }
    }

    // ---- chat server written by the plugin: %APPDATA%\steslicer\ai_assistant.json ----

    static string ReadField(string json, string name)
    {
        Match m = Regex.Match(json, "\"" + name + "\"\\s*:\\s*(\"((?:[^\"\\\\]|\\\\.)*)\"|(\\d+))");
        if (!m.Success) return null;
        if (m.Groups[3].Success) return m.Groups[3].Value;
        return Regex.Unescape(m.Groups[2].Value);
    }

    static string RunningChatUrl()
    {
        if (!File.Exists(PortFile)) return null;
        string json = File.ReadAllText(PortFile);
        string url = ReadField(json, "url");
        string pid = ReadField(json, "pid");
        if (url == null || pid == null) return null;
        try { Process.GetProcessById(int.Parse(pid)); }
        catch (ArgumentException) { return null; }  // the slicer that wrote the file is gone
        return Responds(url) ? url : null;
    }

    static bool Responds(string url)
    {
        try
        {
            HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url + "api/settings");
            request.Timeout = 2000;
            request.Proxy = null;
            using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                return response.StatusCode == HttpStatusCode.OK;
        }
        catch (WebException) { return false; }
    }

    static string WaitForChat(DateTime startedAfter)
    {
        using (Form splash = Splash())
        {
            splash.Show();
            DateTime deadline = DateTime.Now.AddSeconds(StartTimeoutSeconds);
            while (DateTime.Now < deadline)
            {
                Application.DoEvents();
                if (File.Exists(PortFile) && File.GetLastWriteTime(PortFile) >= startedAfter.AddSeconds(-2))
                {
                    string url = RunningChatUrl();
                    if (url != null) return url;
                }
                Thread.Sleep(500);
            }
            return null;
        }
    }

    static Form Splash()
    {
        Form f = new Form();
        f.Text = "AI Assistant";
        f.FormBorderStyle = FormBorderStyle.FixedDialog;
        f.StartPosition = FormStartPosition.CenterScreen;
        f.ClientSize = new Size(420, 110);
        f.MaximizeBox = false;
        f.MinimizeBox = false;
        f.BackColor = Color.FromArgb(21, 42, 54);
        f.ForeColor = Color.FromArgb(232, 241, 245);
        f.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Label label = new Label();
        label.Text = "Запускаю STE Slicer и AI Assistant…\nПервый запуск слайсера занимает до минуты.";
        label.AutoSize = false;
        label.SetBounds(16, 14, 388, 44);
        ProgressBar bar = new ProgressBar();
        bar.Style = ProgressBarStyle.Marquee;
        bar.SetBounds(16, 66, 388, 18);
        f.Controls.Add(label);
        f.Controls.Add(bar);
        return f;
    }

    // ---- finding STE Slicer ----

    static string FindSlicer()
    {
        string[] remembered = new string[] { SlicerPathFile };
        foreach (string file in remembered)
            if (File.Exists(file))
            {
                string p = File.ReadAllText(file).Trim();
                if (File.Exists(p)) return p;
            }
        if (File.Exists(PortFile))
        {
            string p = ReadField(File.ReadAllText(PortFile), "slicer_exe");
            if (p != null && File.Exists(p)) return Remember(p);
        }
        string fromRegistry = FromUninstallKeys();
        if (fromRegistry != null) return Remember(fromRegistry);
        foreach (string root in new string[] {
            Environment.GetEnvironmentVariable("ProgramFiles"), Environment.GetEnvironmentVariable("ProgramFiles(x86)"),
            Path.Combine(LocalAppData, "Programs") })
        {
            if (string.IsNullOrEmpty(root) || !Directory.Exists(root)) continue;
            foreach (string dir in Directory.GetDirectories(root, "*STE Slicer*"))
            {
                string p = Path.Combine(dir, "STESlicer.exe");
                if (File.Exists(p)) return Remember(p);
            }
        }
        OpenFileDialog dialog = new OpenFileDialog();
        dialog.Title = "Укажите STESlicer.exe (один раз)";
        dialog.Filter = "STE Slicer|STESlicer.exe|Программы|*.exe";
        if (dialog.ShowDialog() == DialogResult.OK) return Remember(dialog.FileName);
        return null;
    }

    static string FromUninstallKeys()
    {
        foreach (RegistryKey hive in new RegistryKey[] { Registry.LocalMachine, Registry.CurrentUser })
            foreach (string path in new string[] { @"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                                                   @"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall" })
            {
                using (RegistryKey key = hive.OpenSubKey(path))
                {
                    if (key == null) continue;
                    foreach (string name in key.GetSubKeyNames())
                        using (RegistryKey app = key.OpenSubKey(name))
                        {
                            string display = app == null ? null : app.GetValue("DisplayName") as string;
                            if (display == null || display.IndexOf("STE Slicer", StringComparison.OrdinalIgnoreCase) < 0) continue;
                            string dir = app.GetValue("InstallLocation") as string;
                            string uninstall = app.GetValue("UninstallString") as string;
                            if (string.IsNullOrEmpty(dir) && uninstall != null)
                                dir = Path.GetDirectoryName(uninstall.Trim('"'));
                            if (!string.IsNullOrEmpty(dir) && File.Exists(Path.Combine(dir, "STESlicer.exe")))
                                return Path.Combine(dir, "STESlicer.exe");
                        }
                }
            }
        return null;
    }

    static string Remember(string exe)
    {
        Directory.CreateDirectory(OwnDir);
        File.WriteAllText(SlicerPathFile, exe);
        return exe;
    }

    // ---- the chat window: a Chromium browser in app mode, the default browser as the last resort ----

    static void OpenWindow(string url)
    {
        string[] relative = {
            @"Microsoft\Edge\Application\msedge.exe", @"Google\Chrome\Application\chrome.exe",
            @"Yandex\YandexBrowser\Application\browser.exe", @"Chromium\Application\chrome.exe" };
        string[] roots = {
            Environment.GetEnvironmentVariable("ProgramFiles(x86)"), Environment.GetEnvironmentVariable("ProgramFiles"),
            LocalAppData };
        string profile = Path.Combine(OwnDir, "window");
        Directory.CreateDirectory(profile);
        foreach (string rel in relative)
            foreach (string root in roots)
            {
                if (string.IsNullOrEmpty(root)) continue;
                string browser = Path.Combine(root, rel);
                if (!File.Exists(browser)) continue;
                Process.Start(browser, "--app=" + url + " --user-data-dir=\"" + profile + "\"" +
                                       " --window-size=1280,860 --no-first-run --no-default-browser-check");
                return;
            }
        Process.Start(url);
    }
}
