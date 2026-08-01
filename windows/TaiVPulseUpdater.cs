using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Threading;
using System.Windows.Forms;

namespace TaiVPulse.WindowsUpdater
{
    internal sealed class UpdateOptions
    {
        public int WaitProcessId;
        public string InstallerPath = "";
        public string InstallDirectory = "";
        public string ExpectedVersion = "";

        public static UpdateOptions Parse(string[] args)
        {
            var options = new UpdateOptions();
            for (int index = 0; index < args.Length; index++)
            {
                string argument = args[index];
                if (String.Equals(argument, "--wait-pid", StringComparison.OrdinalIgnoreCase) && index + 1 < args.Length)
                    Int32.TryParse(args[++index], out options.WaitProcessId);
                else if (String.Equals(argument, "--installer", StringComparison.OrdinalIgnoreCase) && index + 1 < args.Length)
                    options.InstallerPath = Path.GetFullPath(args[++index]);
                else if (String.Equals(argument, "--install-dir", StringComparison.OrdinalIgnoreCase) && index + 1 < args.Length)
                    options.InstallDirectory = Path.GetFullPath(args[++index]);
                else if (String.Equals(argument, "--expected-version", StringComparison.OrdinalIgnoreCase) && index + 1 < args.Length)
                    options.ExpectedVersion = args[++index].Trim();
            }
            return options;
        }
    }

    internal static class Program
    {
        private static readonly Encoding Utf8WithBom = new UTF8Encoding(true);
        private static string logPath = "";

        [STAThread]
        private static void Main(string[] args)
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            try
            {
                UpdateOptions options = UpdateOptions.Parse(args);
                InitializeLog();
                ValidateOptions(options);
                Log("準備更新至 " + options.ExpectedVersion + "。");
                WaitForLauncher(options.WaitProcessId);
                int exitCode = RunInstaller(options);
                if (exitCode != 0)
                    throw new InvalidOperationException("安裝程式結束碼：" + exitCode);

                string launcherPath = Path.Combine(options.InstallDirectory, "TaiVPulse.exe");
                if (!File.Exists(launcherPath))
                    throw new FileNotFoundException("更新完成後找不到 TaiVPulse.exe。", launcherPath);

                string installedVersion = FileVersionInfo.GetVersionInfo(launcherPath).ProductVersion ?? "";
                if (!VersionMatches(installedVersion, options.ExpectedVersion))
                    throw new InvalidDataException(
                        "更新後版本不符；預期 " + options.ExpectedVersion + "，實際 " + installedVersion + "。"
                    );

                Log("更新完成，啟動台V Pulse " + options.ExpectedVersion + "。");
                var startInfo = new ProcessStartInfo();
                startInfo.FileName = launcherPath;
                startInfo.WorkingDirectory = options.InstallDirectory;
                startInfo.UseShellExecute = true;
                Process.Start(startInfo);
                TryDelete(options.InstallerPath);
                Environment.ExitCode = 0;
            }
            catch (Exception error)
            {
                try { Log("更新失敗：" + error.Message); } catch { }
                MessageBox.Show(
                    "台V Pulse 更新失敗。原本的 .env、資料庫與私人資料不會被更新器刪除。\r\n\r\n" +
                    error.Message + (String.IsNullOrWhiteSpace(logPath) ? "" : "\r\n\r\nLOG：" + logPath),
                    "台V Pulse 更新失敗",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                Environment.ExitCode = 81;
            }
        }

        private static void InitializeLog()
        {
            string root = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "TaiVPulse", "logs"
            );
            Directory.CreateDirectory(root);
            logPath = Path.Combine(root, "updater-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            File.WriteAllText(logPath, "台V Pulse 更新器" + Environment.NewLine, Utf8WithBom);
        }

        private static void ValidateOptions(UpdateOptions options)
        {
            if (options.WaitProcessId <= 0)
                throw new ArgumentException("缺少要等待的啟動器程序 ID。");
            if (String.IsNullOrWhiteSpace(options.InstallerPath) || !File.Exists(options.InstallerPath))
                throw new FileNotFoundException("找不到已驗證的台V Pulse 安裝程式。", options.InstallerPath);
            if (String.IsNullOrWhiteSpace(options.InstallDirectory) || !Directory.Exists(options.InstallDirectory))
                throw new DirectoryNotFoundException("找不到台V Pulse 安裝目錄。");
            Version parsed;
            if (!Version.TryParse(options.ExpectedVersion, out parsed))
                throw new ArgumentException("更新版本格式不正確。");
        }

        private static void WaitForLauncher(int processId)
        {
            try
            {
                using (Process process = Process.GetProcessById(processId))
                {
                    Log("等待舊啟動器關閉。");
                    if (!process.WaitForExit(30000))
                        throw new TimeoutException("舊啟動器未在 30 秒內關閉。");
                }
            }
            catch (ArgumentException)
            {
                // The launcher already exited before the updater attached.
            }
        }

        private static int RunInstaller(UpdateOptions options)
        {
            var startInfo = new ProcessStartInfo();
            startInfo.FileName = options.InstallerPath;
            startInfo.Arguments = "--silent --no-launch --install-dir " + Quote(options.InstallDirectory);
            startInfo.WorkingDirectory = Path.GetDirectoryName(options.InstallerPath);
            startInfo.UseShellExecute = false;
            startInfo.CreateNoWindow = true;
            Log("啟動已驗證的安裝程式。");
            using (Process process = Process.Start(startInfo))
            {
                if (process == null) throw new InvalidOperationException("無法啟動安裝程式。");
                if (!process.WaitForExit(10 * 60 * 1000))
                {
                    try { process.Kill(); } catch { }
                    throw new TimeoutException("安裝程式超過 10 分鐘仍未完成。");
                }
                return process.ExitCode;
            }
        }

        private static bool VersionMatches(string installed, string expected)
        {
            Version installedVersion;
            Version expectedVersion;
            string installedCore = (installed ?? "").Split('+')[0].Trim();
            return Version.TryParse(installedCore, out installedVersion) &&
                   Version.TryParse(expected, out expectedVersion) &&
                   installedVersion.Major == expectedVersion.Major &&
                   installedVersion.Minor == expectedVersion.Minor &&
                   installedVersion.Build == expectedVersion.Build;
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }

        private static void TryDelete(string path)
        {
            try
            {
                if (File.Exists(path)) File.Delete(path);
            }
            catch { }
        }

        private static void Log(string message)
        {
            File.AppendAllText(
                logPath,
                DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss") + " " + message + Environment.NewLine,
                Utf8WithBom
            );
        }
    }
}
