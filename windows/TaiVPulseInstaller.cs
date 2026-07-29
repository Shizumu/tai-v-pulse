using Microsoft.Win32;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace TaiVPulse.WindowsInstaller
{
    internal static class Program
    {
        private static readonly Encoding Utf8WithBom = new UTF8Encoding(true);

        [STAThread]
        private static void Main(string[] args)
        {
            bool silent = false;
            try
            {
                var options = InstallOptions.Parse(args);
                silent = options.Silent;
                var engine = new InstallerEngine(options);
                if (options.Silent)
                {
                    try
                    {
                        engine.Install();
                        Environment.ExitCode = 0;
                    }
                    catch (InstallException ex)
                    {
                        engine.Log("[" + ex.Code + "] " + ex.Message);
                        Environment.ExitCode = ex.ExitCode;
                    }
                    catch (Exception ex)
                    {
                        engine.Log("[TVP-I900] " + ex);
                        Environment.ExitCode = 99;
                    }
                    return;
                }

                Application.EnableVisualStyles();
                Application.SetCompatibleTextRenderingDefault(false);
                Application.Run(new InstallerForm(engine));
            }
            catch (Exception ex)
            {
                string fatalLog = Path.Combine(Path.GetTempPath(), "TaiVPulse-installer-fatal.log");
                try { File.WriteAllText(fatalLog, ex.ToString(), Utf8WithBom); } catch { }
                if (!silent)
                {
                    try
                    {
                        MessageBox.Show("[TVP-I900] 安裝程式無法初始化。\r\n\r\n診斷 LOG：\r\n" + fatalLog,
                            "台V Pulse 安裝失敗", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }
                    catch { }
                }
                Environment.ExitCode = 99;
            }
        }
    }

    internal sealed class InstallOptions
    {
        public bool Silent;
        public bool NoLaunch;
        public bool NoShortcuts;
        public bool NoRegister;
        public string InstallDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
            "Programs", "TaiVPulse");

        public static InstallOptions Parse(string[] args)
        {
            var options = new InstallOptions();
            for (int i = 0; i < args.Length; i++)
            {
                string arg = args[i];
                if (String.Equals(arg, "--silent", StringComparison.OrdinalIgnoreCase)) options.Silent = true;
                else if (String.Equals(arg, "--no-launch", StringComparison.OrdinalIgnoreCase)) options.NoLaunch = true;
                else if (String.Equals(arg, "--no-shortcuts", StringComparison.OrdinalIgnoreCase)) options.NoShortcuts = true;
                else if (String.Equals(arg, "--no-register", StringComparison.OrdinalIgnoreCase)) options.NoRegister = true;
                else if (String.Equals(arg, "--install-dir", StringComparison.OrdinalIgnoreCase) && i + 1 < args.Length)
                    options.InstallDirectory = Path.GetFullPath(args[++i]);
            }
            return options;
        }
    }

    internal sealed class InstallException : Exception
    {
        public readonly string Code;
        public readonly int ExitCode;
        public InstallException(string code, int exitCode, string message, Exception inner)
            : base(message, inner) { Code = code; ExitCode = exitCode; }
        public InstallException(string code, int exitCode, string message)
            : this(code, exitCode, message, null) { }
    }

    internal sealed class InstallerEngine
    {
        private const string PayloadResource = "TaiVPulse.Payload.zip";
        private const string VersionResource = "TaiVPulse.Version.txt";
        private static readonly Encoding Utf8WithBom = new UTF8Encoding(true);
        private readonly InstallOptions options;
        private readonly string logPath;
        public string Version { get; private set; }
        public string InstallDirectory { get { return options.InstallDirectory; } }
        public string LogPath { get { return logPath; } }

        public InstallerEngine(InstallOptions installOptions)
        {
            options = installOptions;
            string logRoot = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "TaiVPulse", "logs");
            try { Directory.CreateDirectory(logRoot); }
            catch
            {
                logRoot = Path.Combine(Path.GetTempPath(), "TaiVPulse-logs");
                Directory.CreateDirectory(logRoot);
            }
            logPath = Path.Combine(logRoot, "installer-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            Version = ReadVersion();
        }

        public void Install()
        {
            try
            {
                Log("開始安裝台V Pulse " + Version);
                Log("安裝位置：" + ProtectPath(options.InstallDirectory));
                Directory.CreateDirectory(options.InstallDirectory);
                StopExistingServices();
                HashSet<string> currentFiles = ExtractPayload();
                int removedFiles = CleanStaleManagedFiles(currentFiles);
                if (removedFiles > 0) Log("已清除舊版程式檔：" + removedFiles + " 個");

                string launcher = Path.Combine(options.InstallDirectory, "TaiVPulse.exe");
                if (!File.Exists(launcher))
                    throw new InstallException("TVP-I001", 11, "安裝內容缺少 TaiVPulse.exe，請重新下載安裝程式。");
                string shortcutIcon = Path.Combine(options.InstallDirectory, "TaiVPulse-" + Version + ".ico");
                if (!File.Exists(shortcutIcon))
                    throw new InstallException("TVP-I001", 11, "安裝內容缺少最新版捷徑圖示，請重新下載安裝程式。");

                if (!options.NoShortcuts) CreateShortcuts(launcher, shortcutIcon);
                if (!options.NoRegister) RegisterUninstaller(launcher, shortcutIcon);
                Log("安裝完成。");

                if (!options.NoLaunch) Process.Start(launcher);
            }
            catch (InstallException) { throw; }
            catch (InvalidDataException ex)
            {
                throw new InstallException("TVP-I001", 11, "安裝內容損毀，請重新下載並核對 SHA-256。", ex);
            }
            catch (UnauthorizedAccessException ex)
            {
                throw new InstallException("TVP-I002", 12, "沒有權限寫入安裝位置。請關閉防毒攔截後重試。", ex);
            }
            catch (IOException ex)
            {
                throw new InstallException("TVP-I002", 12, "無法寫入安裝檔案；請先關閉正在執行的台V Pulse 後重試。", ex);
            }
        }

        public void Log(string message)
        {
            string safe = ProtectPath(message);
            File.AppendAllText(logPath,
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + safe + Environment.NewLine,
                Utf8WithBom);
        }

        private string ReadVersion()
        {
            try
            {
                using (Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(VersionResource))
                using (var reader = new StreamReader(stream, Encoding.UTF8, true))
                    return reader.ReadToEnd().Trim();
            }
            catch { return "未知版本"; }
        }

        private void StopExistingServices()
        {
            string stopScript = Path.Combine(options.InstallDirectory, "stop-local.ps1");
            if (!File.Exists(stopScript)) return;
            try
            {
                using (var process = Process.Start(new ProcessStartInfo
                {
                    FileName = "powershell.exe",
                    Arguments = "-NoProfile -ExecutionPolicy Bypass -File \"" + stopScript + "\" -Quiet",
                    WorkingDirectory = options.InstallDirectory,
                    UseShellExecute = false,
                    CreateNoWindow = true
                }))
                {
                    if (process != null) process.WaitForExit(15000);
                }
            }
            catch (Exception ex) { Log("更新前停止舊服務時收到警告：" + ex.Message); }
        }

        private HashSet<string> ExtractPayload()
        {
            Stream stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(PayloadResource);
            if (stream == null)
                throw new InstallException("TVP-I001", 11, "安裝程式不含應用程式內容，請重新下載。");

            string root = Path.GetFullPath(options.InstallDirectory).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            var currentFiles = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            using (stream)
            using (var archive = new ZipArchive(stream, ZipArchiveMode.Read, false))
            {
                foreach (ZipArchiveEntry entry in archive.Entries)
                {
                    string destination = Path.GetFullPath(Path.Combine(root, entry.FullName));
                    if (!destination.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("封裝含有不安全的路徑。");
                    if (String.IsNullOrEmpty(entry.Name))
                    {
                        Directory.CreateDirectory(destination);
                        continue;
                    }
                    currentFiles.Add(entry.FullName.Replace('\\', '/').TrimStart('/'));
                    Directory.CreateDirectory(Path.GetDirectoryName(destination));
                    using (Stream input = entry.Open())
                    using (var output = new FileStream(destination, FileMode.Create, FileAccess.Write, FileShare.None))
                        input.CopyTo(output);
                    File.SetLastWriteTime(destination, entry.LastWriteTime.LocalDateTime);
                }
            }
            return currentFiles;
        }

        private int CleanStaleManagedFiles(HashSet<string> currentFiles)
        {
            string root = Path.GetFullPath(options.InstallDirectory).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            string[] managedDirectories = { ".openai", "app", "build", "collector", "public", "windows", "worker" };
            int removedFiles = 0;

            foreach (string directoryName in managedDirectories)
            {
                string managedRoot = Path.GetFullPath(Path.Combine(root, directoryName));
                if (!managedRoot.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !Directory.Exists(managedRoot))
                    continue;

                foreach (string file in Directory.GetFiles(managedRoot, "*", SearchOption.AllDirectories))
                {
                    string fullPath = Path.GetFullPath(file);
                    if (!fullPath.StartsWith(root, StringComparison.OrdinalIgnoreCase))
                        throw new InvalidDataException("舊版程式檔路徑超出安裝目錄。");
                    string relativePath = fullPath.Substring(root.Length).Replace('\\', '/');
                    if (currentFiles.Contains(relativePath)) continue;
                    File.Delete(fullPath);
                    removedFiles++;
                }

                string[] directories = Directory.GetDirectories(managedRoot, "*", SearchOption.AllDirectories);
                Array.Sort(directories, delegate(string left, string right) { return right.Length.CompareTo(left.Length); });
                foreach (string directory in directories)
                {
                    if (Directory.GetFileSystemEntries(directory).Length == 0) Directory.Delete(directory);
                }
            }
            return removedFiles;
        }

        private void CreateShortcuts(string launcher, string shortcutIcon)
        {
            try
            {
                string desktopShortcut = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "台V Pulse.lnk");
                string startFolder = Path.Combine(
                    Environment.GetFolderPath(Environment.SpecialFolder.Programs), "台V Pulse");
                Directory.CreateDirectory(startFolder);
                CreateShortcut(desktopShortcut, launcher, "啟動台V Pulse", shortcutIcon);
                CreateShortcut(Path.Combine(startFolder, "台V Pulse.lnk"), launcher, "啟動台V Pulse", shortcutIcon);
                CreateShortcut(Path.Combine(startFolder, "解除安裝台V Pulse.lnk"), "powershell.exe", "解除安裝台V Pulse",
                    "-NoProfile -ExecutionPolicy Bypass -File \"" + Path.Combine(options.InstallDirectory, "uninstall.ps1") + "\"",
                    shortcutIcon);
            }
            catch (Exception ex)
            {
                throw new InstallException("TVP-I003", 13, "檔案已安裝，但無法建立捷徑。LOG：" + logPath, ex);
            }
        }

        private void CreateShortcut(string shortcutPath, string target, string description, string arguments, string iconPath)
        {
            Type shellType = Type.GetTypeFromProgID("WScript.Shell");
            object shell = Activator.CreateInstance(shellType);
            object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { shortcutPath });
            Type shortcutType = shortcut.GetType();
            shortcutType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { target });
            shortcutType.InvokeMember("Arguments", BindingFlags.SetProperty, null, shortcut, new object[] { arguments ?? "" });
            shortcutType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { options.InstallDirectory });
            shortcutType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { description });
            shortcutType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { iconPath + ",0" });
            shortcutType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
            Marshal.FinalReleaseComObject(shortcut);
            Marshal.FinalReleaseComObject(shell);
        }

        private void CreateShortcut(string shortcutPath, string target, string description, string iconPath)
        {
            CreateShortcut(shortcutPath, target, description, "", iconPath);
        }

        private void RegisterUninstaller(string launcher, string shortcutIcon)
        {
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(
                    @"Software\Microsoft\Windows\CurrentVersion\Uninstall\TaiVPulse"))
                {
                    key.SetValue("DisplayName", "台V Pulse");
                    key.SetValue("DisplayVersion", Version);
                    key.SetValue("Publisher", "台V Pulse");
                    key.SetValue("DisplayIcon", shortcutIcon + ",0");
                    key.SetValue("InstallLocation", options.InstallDirectory);
                    key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                    key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    key.SetValue("UninstallString", "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"" +
                        Path.Combine(options.InstallDirectory, "uninstall.ps1") + "\"");
                    key.SetValue("QuietUninstallString", "powershell.exe -NoProfile -ExecutionPolicy Bypass -File \"" +
                        Path.Combine(options.InstallDirectory, "uninstall.ps1") + "\" -Quiet");
                }
            }
            catch (Exception ex)
            {
                throw new InstallException("TVP-I004", 14, "檔案與捷徑已建立，但無法註冊解除安裝項目。", ex);
            }
        }

        private string ProtectPath(string text)
        {
            if (String.IsNullOrEmpty(text)) return "";
            string profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
            return String.IsNullOrEmpty(profile)
                ? text
                : text.Replace(profile, Path.GetPathRoot(profile) + "Users" + Path.DirectorySeparatorChar + "<USER>");
        }
    }

    internal sealed class InstallerForm : Form
    {
        private readonly InstallerEngine engine;
        private readonly CheckBox agreement = new CheckBox();
        private readonly Button installButton = new Button();
        private readonly Label status = new Label();

        public InstallerForm(InstallerEngine installerEngine)
        {
            engine = installerEngine;
            Text = "安裝台V Pulse " + engine.Version;
            Width = 620;
            Height = 455;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(246, 250, 247);
            Font = new Font("Microsoft JhengHei UI", 10F);

            var title = new Label();
            title.Text = "安裝台V Pulse";
            title.Font = new Font(Font.FontFamily, 21F, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(35, 83, 66);
            title.AutoSize = true;
            title.Location = new Point(30, 25);
            Controls.Add(title);

            var intro = new Label();
            intro.Text = "本安裝程式會把台V Pulse 安裝到目前 Windows 使用者，不需要管理員權限。\r\n" +
                         "第一次啟動會檢查 Node.js 22.13+、Python 3.11+、時區資料與前端套件。";
            intro.Location = new Point(34, 82);
            intro.Size = new Size(545, 55);
            Controls.Add(intro);

            var notice = new TextBox();
            notice.Multiline = true;
            notice.ReadOnly = true;
            notice.ScrollBars = ScrollBars.Vertical;
            notice.BackColor = Color.White;
            notice.Location = new Point(34, 145);
            notice.Size = new Size(535, 115);
            notice.Text = "使用與授權摘要\r\n\r\n" +
                          "• 本工具只在本機執行，使用者自行提供 YouTube Data API Key。\r\n" +
                          "• 專案採 PolyForm Noncommercial License 1.0.0，僅供非商業用途。\r\n" +
                          "• 不會把 .env、SQLite 或 Studio 原始檔放進診斷報告。\r\n" +
                          "• 完整 LICENSE、NOTICE 與隱私說明會安裝在程式資料夾。";
            Controls.Add(notice);

            agreement.Text = "我已閱讀並同意上述非商用授權及本機資料說明";
            agreement.Location = new Point(35, 278);
            agreement.AutoSize = true;
            agreement.CheckedChanged += delegate { installButton.Enabled = agreement.Checked; };
            Controls.Add(agreement);

            status.Text = "安裝位置：" + engine.InstallDirectory;
            status.Location = new Point(35, 310);
            status.Size = new Size(530, 35);
            status.ForeColor = Color.FromArgb(86, 96, 91);
            Controls.Add(status);

            installButton.Text = "安裝並啟動";
            installButton.Location = new Point(405, 350);
            installButton.Size = new Size(164, 40);
            installButton.Enabled = false;
            installButton.BackColor = Color.FromArgb(47, 125, 91);
            installButton.ForeColor = Color.White;
            installButton.FlatStyle = FlatStyle.Flat;
            installButton.FlatAppearance.BorderSize = 0;
            installButton.Click += async delegate { await Install(); };
            Controls.Add(installButton);

            var cancel = new Button();
            cancel.Text = "取消";
            cancel.Location = new Point(295, 350);
            cancel.Size = new Size(96, 40);
            cancel.Click += delegate { Close(); };
            Controls.Add(cancel);
        }

        private async Task Install()
        {
            installButton.Enabled = false;
            agreement.Enabled = false;
            UseWaitCursor = true;
            status.Text = "正在解壓縮、建立捷徑並註冊解除安裝…";
            try
            {
                await Task.Run(delegate { engine.Install(); });
                status.Text = "安裝完成，正在開啟台V Pulse。";
                MessageBox.Show(this,
                    "台V Pulse 已安裝完成。\r\n\r\n若缺少 Node.js、Python 或前端套件，啟動器會逐項說明並提供安裝選項。",
                    "台V Pulse", MessageBoxButtons.OK, MessageBoxIcon.Information);
                Close();
            }
            catch (InstallException ex)
            {
                engine.Log("[" + ex.Code + "] " + ex);
                status.Text = "安裝失敗：" + ex.Code;
                MessageBox.Show(this,
                    "[" + ex.Code + "] " + ex.Message + "\r\n\r\n安裝 LOG：\r\n" + engine.LogPath,
                    "台V Pulse 安裝失敗", MessageBoxButtons.OK, MessageBoxIcon.Error);
                installButton.Enabled = agreement.Checked;
                agreement.Enabled = true;
            }
            catch (Exception ex)
            {
                engine.Log("[TVP-I900] " + ex);
                status.Text = "安裝失敗：TVP-I900";
                MessageBox.Show(this,
                    "[TVP-I900] 發生未預期的安裝問題。\r\n\r\n安裝 LOG：\r\n" + engine.LogPath,
                    "台V Pulse 安裝失敗", MessageBoxButtons.OK, MessageBoxIcon.Error);
                installButton.Enabled = agreement.Checked;
                agreement.Enabled = true;
            }
            finally { UseWaitCursor = false; }
        }
    }
}
