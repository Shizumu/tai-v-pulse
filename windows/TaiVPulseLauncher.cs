using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Net;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace TaiVPulse.Windows
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new LauncherForm());
        }
    }

    internal sealed class ScriptResult
    {
        public int ExitCode;
        public string Output = "";
        public string LogPath = "";
    }

    internal sealed class BackendActivity
    {
        public bool Online;
        public string CurrentJob;
        public string LastJob;
        public string LastJobFinishedAt;
        public string LastJobStatus;
    }

    internal sealed class LauncherForm : Form
    {
        private static readonly Encoding Utf8WithoutBom = new UTF8Encoding(false);
        private static readonly Encoding Utf8WithBom = new UTF8Encoding(true);
        private const string WebUrl = "http://127.0.0.1:3000";
        private readonly string root = AppDomain.CurrentDomain.BaseDirectory.TrimEnd(Path.DirectorySeparatorChar);
        private readonly TextBox outputBox = new TextBox();
        private readonly Label stateLabel = new Label();
        private readonly Label activityLabel = new Label();
        private readonly Button startButton = new Button();
        private readonly Button stopButton = new Button();
        private readonly Button openButton = new Button();
        private readonly Button configButton = new Button();
        private readonly Button exportButton = new Button();
        private readonly Button uninstallButton = new Button();
        private readonly Timer statusTimer = new Timer();
        private bool busy;
        private bool checking;
        private bool browserOpenedForStart;
        private string lastObservedJob;
        private string lastReportedCompletionAt;

        public LauncherForm()
        {
            Text = "台V Pulse";
            Width = 720;
            Height = 520;
            MinimumSize = new Size(640, 460);
            StartPosition = FormStartPosition.CenterScreen;
            BackColor = Color.FromArgb(246, 250, 247);
            Font = new Font("Microsoft JhengHei UI", 10F, FontStyle.Regular, GraphicsUnit.Point);

            var title = new Label();
            title.Text = "台V Pulse 本機啟動器";
            title.Font = new Font(Font.FontFamily, 18F, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(35, 83, 66);
            title.AutoSize = true;
            title.Location = new Point(24, 20);
            Controls.Add(title);

            stateLabel.Text = "正在檢查服務狀態…";
            stateLabel.AutoSize = true;
            stateLabel.Location = new Point(27, 60);
            stateLabel.ForeColor = Color.FromArgb(86, 96, 91);
            Controls.Add(stateLabel);

            activityLabel.Text = "後台動態：等待資料服務";
            activityLabel.AutoSize = true;
            activityLabel.Location = new Point(27, 80);
            activityLabel.ForeColor = Color.FromArgb(86, 96, 91);
            Controls.Add(activityLabel);

            ConfigureButton(startButton, "啟動", 24, 108, Color.FromArgb(47, 125, 91), Color.White);
            ConfigureButton(stopButton, "停止", 132, 108, Color.FromArgb(229, 236, 232), Color.FromArgb(35, 83, 66));
            ConfigureButton(openButton, "開啟網頁", 240, 108, Color.FromArgb(229, 236, 232), Color.FromArgb(35, 83, 66));
            ConfigureButton(configButton, "編輯 API Key", 348, 108, Color.FromArgb(229, 236, 232), Color.FromArgb(35, 83, 66));
            ConfigureButton(exportButton, "匯出診斷", 456, 108, Color.FromArgb(255, 239, 196), Color.FromArgb(98, 72, 7));
            ConfigureButton(uninstallButton, "解除安裝", 564, 108, Color.FromArgb(244, 226, 226), Color.FromArgb(135, 55, 55));

            startButton.Click += async delegate { await StartApplication(); };
            stopButton.Click += async delegate { await StopApplication(); };
            openButton.Click += delegate { OpenTarget(WebUrl); };
            configButton.Click += delegate { EditConfiguration(); };
            exportButton.Click += async delegate { await ExportDiagnostics(); };
            uninstallButton.Click += delegate { BeginUninstall(); };

            var logLabel = new Label();
            logLabel.Text = "執行狀態（完整內容會同步寫入 LOG）";
            logLabel.AutoSize = true;
            logLabel.Location = new Point(24, 161);
            Controls.Add(logLabel);

            outputBox.Location = new Point(24, 188);
            outputBox.Size = new Size(ClientSize.Width - 48, ClientSize.Height - 226);
            outputBox.Anchor = AnchorStyles.Top | AnchorStyles.Bottom | AnchorStyles.Left | AnchorStyles.Right;
            outputBox.Multiline = true;
            outputBox.ReadOnly = true;
            outputBox.ScrollBars = ScrollBars.Vertical;
            outputBox.BackColor = Color.White;
            outputBox.Font = new Font("Microsoft JhengHei UI", 9F);
            outputBox.Text = "按「啟動」後，程式會自動檢查 Node.js、Python、時區資料、前端套件與兩個本機服務。\r\n";
            Controls.Add(outputBox);

            statusTimer.Interval = 5000;
            statusTimer.Tick += async delegate { await RefreshStatus(); };
            Shown += async delegate
            {
                await RefreshStatus();
                statusTimer.Start();
            };
        }

        private void ConfigureButton(Button button, string text, int x, int y, Color back, Color fore)
        {
            button.Text = text;
            button.Location = new Point(x, y);
            button.Size = new Size(100, 36);
            button.FlatStyle = FlatStyle.Flat;
            button.FlatAppearance.BorderSize = 0;
            button.BackColor = back;
            button.ForeColor = fore;
            button.Cursor = Cursors.Hand;
            Controls.Add(button);
        }

        private async Task StartApplication()
        {
            if (busy) return;
            browserOpenedForStart = false;
            SetBusy(true, "正在檢查環境並啟動…");
            AppendLine("開始啟動。第一次安裝前端套件時可能需要幾分鐘。", false);
            ScriptResult result = await RunScript("start-local.ps1", "-NoOpen", HandleStartupOutput);
            SetBusy(false, "啟動檢查完成");

            if (result.ExitCode == 0)
            {
                AppendLine("台V Pulse 已可使用。", false);
                OpenWebPageOnce();
                await RefreshStatus();
                return;
            }

            string code = FindErrorCode(result.Output, result.ExitCode);
            string summary = DescribeExitCode(result.ExitCode);
            ShowProblem(code, summary, result.LogPath);
            await RefreshStatus();
        }

        private async Task StopApplication()
        {
            if (busy) return;
            SetBusy(true, "正在停止服務…");
            ScriptResult result = await RunScript("stop-local.ps1", "");
            SetBusy(false, result.ExitCode == 0 ? "已停止" : "停止時發生問題");
            if (result.ExitCode != 0)
                ShowProblem(FindErrorCode(result.Output, result.ExitCode), "無法完整停止台V Pulse。", result.LogPath);
            await RefreshStatus();
        }

        private async Task ExportDiagnostics()
        {
            if (busy) return;
            SetBusy(true, "正在建立已移除敏感資訊的診斷報告…");
            string desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
            ScriptResult result = await RunScript("export-diagnostics.ps1", "-OutputDirectory " + Quote(desktop));
            SetBusy(false, result.ExitCode == 0 ? "診斷報告已建立" : "診斷報告建立失敗");
            if (result.ExitCode != 0)
            {
                ShowProblem("TVP-D001", "無法建立診斷報告。", result.LogPath);
                return;
            }

            Match match = Regex.Match(result.Output, @"(?m)^DIAGNOSTIC_ZIP=(.+)$");
            string zipPath = match.Success ? match.Groups[1].Value.Trim() : desktop;
            MessageBox.Show(this,
                "診斷報告已建立。報告不含 .env、API Key、SQLite 或 Studio 原始檔。\r\n\r\n" + zipPath,
                "台V Pulse", MessageBoxButtons.OK, MessageBoxIcon.Information);
            if (File.Exists(zipPath)) OpenTarget("/select," + Quote(zipPath), "explorer.exe");
            else OpenTarget(desktop);
        }

        private void EditConfiguration()
        {
            try
            {
                string envPath = Path.Combine(root, ".env");
                if (!File.Exists(envPath))
                {
                    string example = Path.Combine(root, ".env.example");
                    if (!File.Exists(example)) throw new FileNotFoundException("缺少 .env.example。", example);
                    File.Copy(example, envPath);
                }
                Process.Start("notepad.exe", Quote(envPath));
                AppendLine("已開啟設定檔。API Key 請只保存在自己的電腦。", false);
            }
            catch (Exception ex)
            {
                ShowProblem("TVP-E301", "無法開啟 API Key 設定檔：" + ex.Message, "");
            }
        }

        private void BeginUninstall()
        {
            if (busy) return;
            DialogResult choice = MessageBox.Show(this,
                "要解除安裝台V Pulse 嗎？\r\n\r\n按「是」：保留 .env、資料庫與本機分析資料。\r\n按「否」：永久刪除所有本機資料。\r\n按「取消」：返回啟動器。",
                "解除安裝台V Pulse", MessageBoxButtons.YesNoCancel, MessageBoxIcon.Warning);
            if (choice == DialogResult.Cancel) return;

            bool removeData = choice == DialogResult.No;
            if (removeData)
            {
                DialogResult confirmDelete = MessageBox.Show(this,
                    "確定要永久刪除所有台V Pulse 本機資料嗎？\r\n\r\n這會刪除 API Key 設定、SQLite、Studio 結構化資料、既有保留資料與 LOG，而且無法復原。",
                    "永久刪除本機資料", MessageBoxButtons.YesNo, MessageBoxIcon.Error);
                if (confirmDelete != DialogResult.Yes) return;
            }

            string scriptPath = Path.Combine(root, "uninstall.ps1");
            if (!File.Exists(scriptPath))
            {
                ShowProblem("TVP-U001", "缺少解除安裝腳本，請重新安裝完整的台V Pulse。", "");
                return;
            }

            try
            {
                var process = new ProcessStartInfo();
                process.FileName = "powershell.exe";
                process.Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(scriptPath) + " -Quiet" + (removeData ? " -RemoveData" : "");
                process.WorkingDirectory = root;
                process.UseShellExecute = false;
                process.CreateNoWindow = true;
                Process.Start(process);
                statusTimer.Stop();
                Application.Exit();
            }
            catch (Exception ex)
            {
                ShowProblem("TVP-U001", "無法啟動解除安裝程序：" + ex.Message, "");
            }
        }

        private async Task RefreshStatus()
        {
            if (checking || busy) return;
            checking = true;
            try
            {
                BackendActivity activity = await Task.Run(delegate { return FetchBackendActivity(); });
                bool web = await Task.Run(delegate { return Ping(WebUrl); });
                if (activity.Online && web)
                {
                    stateLabel.Text = "● 執行中 — " + WebUrl;
                    stateLabel.ForeColor = Color.FromArgb(36, 126, 77);
                    ShowBackendActivity(activity);
                }
                else if (activity.Online || web)
                {
                    stateLabel.Text = "● 部分服務未回應，建議先停止再重新啟動";
                    stateLabel.ForeColor = Color.FromArgb(181, 115, 10);
                    if (activity.Online) ShowBackendActivity(activity);
                    else activityLabel.Text = "後台動態：資料服務未回應";
                }
                else
                {
                    stateLabel.Text = "● 尚未啟動";
                    stateLabel.ForeColor = Color.FromArgb(100, 106, 103);
                    activityLabel.Text = "後台動態：等待資料服務";
                    lastObservedJob = null;
                }
            }
            finally
            {
                checking = false;
            }
        }

        private BackendActivity FetchBackendActivity()
        {
            var activity = new BackendActivity();
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8787/api/summary");
                request.Timeout = 2000;
                request.ReadWriteTimeout = 2000;
                request.Proxy = null;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    activity.Online = (int)response.StatusCode >= 200 && (int)response.StatusCode < 400;
                    using (Stream stream = response.GetResponseStream())
                    using (var reader = new StreamReader(stream, Utf8WithoutBom))
                    {
                        string json = reader.ReadToEnd();
                        activity.CurrentJob = JsonString(json, "current_job");
                        activity.LastJob = JsonString(json, "last_job");
                        activity.LastJobFinishedAt = JsonString(json, "last_job_finished_at");
                        activity.LastJobStatus = JsonString(json, "last_job_status");
                    }
                }
            }
            catch { activity.Online = false; }
            return activity;
        }

        private void ShowBackendActivity(BackendActivity activity)
        {
            if (!String.IsNullOrWhiteSpace(activity.CurrentJob))
            {
                string jobLabel = JobLabel(activity.CurrentJob);
                activityLabel.Text = "後台動態：正在" + jobLabel;
                activityLabel.ForeColor = Color.FromArgb(181, 115, 10);
                if (!String.Equals(lastObservedJob, activity.CurrentJob, StringComparison.Ordinal))
                    AppendLine("後台開始：" + jobLabel + "。", false);
                lastObservedJob = activity.CurrentJob;
                return;
            }

            lastObservedJob = null;
            if (String.IsNullOrWhiteSpace(activity.LastJob))
            {
                activityLabel.Text = "後台動態：目前待命";
                activityLabel.ForeColor = Color.FromArgb(86, 96, 91);
                return;
            }

            string lastLabel = JobLabel(activity.LastJob);
            bool failed = String.Equals(activity.LastJobStatus, "error", StringComparison.OrdinalIgnoreCase);
            activityLabel.Text = failed
                ? "後台動態：最近工作未完成 — " + lastLabel
                : "後台動態：最近完成 — " + lastLabel;
            activityLabel.ForeColor = failed ? Color.FromArgb(181, 72, 52) : Color.FromArgb(36, 126, 77);

            if (!String.IsNullOrWhiteSpace(activity.LastJobFinishedAt) &&
                !String.Equals(lastReportedCompletionAt, activity.LastJobFinishedAt, StringComparison.Ordinal))
            {
                AppendLine("後台" + (failed ? "未完成：" : "完成：") + lastLabel + "。", failed);
                lastReportedCompletionAt = activity.LastJobFinishedAt;
            }
        }

        private string JobLabel(string job)
        {
            switch (job)
            {
                case "discover": return "探索台 V 頻道";
                case "live-poll": return "更新直播同接資料";
                case "hourly-live-scan": return "執行整點開台偵測";
                case "upload-scan": return "掃描最新上傳";
                case "channel-refresh": return "更新頻道公開數據";
                case "manual-channel-refresh": return "更新手動新增頻道";
                default: return "處理資料（" + job + "）";
            }
        }

        private string JsonString(string json, string property)
        {
            Match match = Regex.Match(
                json ?? "",
                "\"" + Regex.Escape(property) + "\"\\s*:\\s*(?:null|\"(?<value>(?:\\\\.|[^\"])*)\")"
            );
            return match.Success && match.Groups["value"].Success ? match.Groups["value"].Value : null;
        }

        private bool Ping(string url)
        {
            try
            {
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
                request.Timeout = 1500;
                request.ReadWriteTimeout = 1500;
                request.Proxy = null;
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                    return (int)response.StatusCode >= 200 && (int)response.StatusCode < 400;
            }
            catch { return false; }
        }

        private async Task<ScriptResult> RunScript(string scriptName, string arguments)
        {
            return await RunScript(scriptName, arguments, null);
        }

        private async Task<ScriptResult> RunScript(string scriptName, string arguments, Action<string> lineReceived)
        {
            var result = new ScriptResult();
            string scriptPath = Path.Combine(root, scriptName);
            string work = Path.Combine(root, "work");
            Directory.CreateDirectory(work);
            result.LogPath = Path.Combine(work, "launcher-" + DateTime.Now.ToString("yyyyMMdd-HHmmss") + ".log");
            File.WriteAllText(result.LogPath, "台V Pulse 啟動器" + Environment.NewLine, Utf8WithBom);
            if (!File.Exists(scriptPath))
            {
                result.ExitCode = 10;
                result.Output = "[TVP-E101] 缺少 " + scriptName;
                File.AppendAllText(result.LogPath, result.Output + Environment.NewLine, Utf8WithoutBom);
                return result;
            }

            var combined = new StringBuilder();
            object gate = new object();
            using (var writer = new StreamWriter(result.LogPath, true, Utf8WithoutBom))
            using (var process = new Process())
            {
                process.StartInfo.FileName = "powershell.exe";
                process.StartInfo.Arguments = "-NoProfile -ExecutionPolicy Bypass -File " + Quote(scriptPath) + (String.IsNullOrWhiteSpace(arguments) ? "" : " " + arguments);
                process.StartInfo.WorkingDirectory = root;
                process.StartInfo.UseShellExecute = false;
                process.StartInfo.CreateNoWindow = true;
                process.StartInfo.RedirectStandardOutput = true;
                process.StartInfo.RedirectStandardError = true;
                process.StartInfo.StandardOutputEncoding = Utf8WithoutBom;
                process.StartInfo.StandardErrorEncoding = Utf8WithoutBom;
                process.EnableRaisingEvents = true;

                DataReceivedEventHandler receive = delegate(object sender, DataReceivedEventArgs e)
                {
                    if (e.Data == null) return;
                    lock (gate)
                    {
                        combined.AppendLine(e.Data);
                        writer.WriteLine(e.Data);
                        writer.Flush();
                    }
                    AppendLine(e.Data, false);
                    if (lineReceived != null && IsHandleCreated && !IsDisposed)
                    {
                        try
                        {
                            BeginInvoke(new Action(delegate { lineReceived(e.Data); }));
                        }
                        catch (InvalidOperationException) {}
                    }
                };
                process.OutputDataReceived += receive;
                process.ErrorDataReceived += receive;

                try
                {
                    process.Start();
                    process.BeginOutputReadLine();
                    process.BeginErrorReadLine();
                    await Task.Run(delegate
                    {
                        while (!process.WaitForExit(250)) { }
                    });
                    // Do not call the parameterless WaitForExit here. Long-running
                    // Node/Python descendants can retain the redirected pipe handles,
                    // which would leave the launcher busy until those services stop.
                    await Task.Delay(150);
                    try { process.CancelOutputRead(); } catch (InvalidOperationException) { }
                    try { process.CancelErrorRead(); } catch (InvalidOperationException) { }
                    lock (gate) { writer.Flush(); }
                    result.ExitCode = process.ExitCode;
                }
                catch (Exception ex)
                {
                    string line = "[TVP-E900] 無法執行 PowerShell：" + ex.Message;
                    lock (gate)
                    {
                        combined.AppendLine(line);
                        writer.WriteLine(line);
                    }
                    result.ExitCode = 90;
                }
            }
            result.Output = combined.ToString();
            return result;
        }

        private void SetBusy(bool value, string state)
        {
            busy = value;
            startButton.Enabled = !value;
            stopButton.Enabled = !value;
            openButton.Enabled = true;
            configButton.Enabled = true;
            exportButton.Enabled = !value;
            uninstallButton.Enabled = !value;
            activityLabel.Text = state;
            activityLabel.ForeColor = value ? Color.FromArgb(181, 115, 10) : Color.FromArgb(86, 96, 91);
        }

        private void HandleStartupOutput(string line)
        {
            if (String.IsNullOrWhiteSpace(line) || line.IndexOf("台V Pulse 已啟動", StringComparison.Ordinal) < 0)
                return;
            activityLabel.Text = "服務已啟動，正在完成啟動器收尾…";
            activityLabel.ForeColor = Color.FromArgb(36, 126, 77);
            OpenWebPageOnce();
        }

        private void OpenWebPageOnce()
        {
            if (browserOpenedForStart) return;
            browserOpenedForStart = true;
            OpenTarget(WebUrl);
        }

        private void AppendLine(string line, bool error)
        {
            if (InvokeRequired)
            {
                BeginInvoke(new Action<string, bool>(AppendLine), line, error);
                return;
            }
            outputBox.AppendText("[" + DateTime.Now.ToString("HH:mm:ss") + "] " + line + Environment.NewLine);
        }

        private string FindErrorCode(string output, int exitCode)
        {
            Match match = Regex.Match(output ?? "", @"\[(TVP-[A-Z]\d{3})\]");
            if (match.Success) return match.Groups[1].Value;
            if (exitCode == 30) return "TVP-E301";
            if (exitCode == 40 || exitCode == 41) return "TVP-E402";
            if (exitCode == 42) return "TVP-E403";
            if (exitCode == 50) return "TVP-E501";
            if (exitCode == 51) return "TVP-E502";
            return "TVP-E900";
        }

        private string DescribeExitCode(int exitCode)
        {
            switch (exitCode)
            {
                case 10: return "安裝內容不完整，請重新執行安裝程式。";
                case 20: return "Node.js 或 Python 尚未安裝完成；完成官方安裝後請重新開啟啟動器。";
                case 30: return "請先在剛開啟的設定檔填入 YouTube API Key，儲存後再按一次「啟動」。";
                case 40: return "前端套件尚未安裝。";
                case 41: return "前端套件安裝失敗，可能是網路、npm 或防毒軟體造成。";
                case 42: return "Python 時區資料安裝失敗，可能是網路、pip 或防毒軟體造成。";
                case 50: return "本機資料服務啟動失敗。";
                case 51: return "本機網頁服務啟動失敗。";
                default: return "啟動時發生未預期的問題。";
            }
        }

        private void ShowProblem(string code, string summary, string logPath)
        {
            using (var dialog = new ProblemDialog(code, summary, logPath)) dialog.ShowDialog(this);
        }

        private void OpenTarget(string target)
        {
            try
            {
                var startInfo = new ProcessStartInfo();
                startInfo.FileName = target;
                startInfo.UseShellExecute = true;
                Process.Start(startInfo);
            }
            catch (Exception ex) { ShowProblem("TVP-E701", "無法開啟：" + ex.Message, ""); }
        }

        private void OpenTarget(string arguments, string executable)
        {
            try
            {
                var startInfo = new ProcessStartInfo();
                startInfo.FileName = executable;
                startInfo.Arguments = arguments;
                startInfo.UseShellExecute = true;
                Process.Start(startInfo);
            }
            catch (Exception ex) { ShowProblem("TVP-E701", "無法開啟：" + ex.Message, ""); }
        }

        private static string Quote(string value)
        {
            return "\"" + value.Replace("\"", "\\\"") + "\"";
        }
    }

    internal sealed class ProblemDialog : Form
    {
        public ProblemDialog(string code, string summary, string logPath)
        {
            Text = "台V Pulse 發生問題";
            Width = 560;
            Height = 300;
            StartPosition = FormStartPosition.CenterParent;
            FormBorderStyle = FormBorderStyle.FixedDialog;
            MaximizeBox = false;
            MinimizeBox = false;
            Font = new Font("Microsoft JhengHei UI", 10F);

            var title = new Label();
            title.Text = "錯誤代碼：" + code;
            title.Font = new Font(Font.FontFamily, 14F, FontStyle.Bold);
            title.ForeColor = Color.FromArgb(164, 52, 52);
            title.Location = new Point(22, 20);
            title.AutoSize = true;
            Controls.Add(title);

            var body = new TextBox();
            body.Multiline = true;
            body.ReadOnly = true;
            body.BorderStyle = BorderStyle.None;
            body.BackColor = SystemColors.Control;
            body.Location = new Point(25, 62);
            body.Size = new Size(495, 105);
            body.Text = summary + (String.IsNullOrWhiteSpace(logPath) ? "" : "\r\n\r\nLOG：" + logPath);
            Controls.Add(body);

            var copy = new Button();
            copy.Text = "複製錯誤代碼";
            copy.Location = new Point(25, 195);
            copy.Size = new Size(140, 35);
            copy.Click += delegate { Clipboard.SetText(code); };
            Controls.Add(copy);

            var open = new Button();
            open.Text = "開啟 LOG";
            open.Location = new Point(178, 195);
            open.Size = new Size(120, 35);
            open.Enabled = !String.IsNullOrWhiteSpace(logPath) && File.Exists(logPath);
            open.Click += delegate { Process.Start("explorer.exe", "/select,\"" + logPath + "\""); };
            Controls.Add(open);

            var close = new Button();
            close.Text = "關閉";
            close.Location = new Point(400, 195);
            close.Size = new Size(120, 35);
            close.DialogResult = DialogResult.OK;
            Controls.Add(close);
            AcceptButton = close;
        }
    }
}
