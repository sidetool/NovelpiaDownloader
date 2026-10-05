using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Net;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using NovelpiaDownloader;

// The original assembly owns every login, job, queue and download operation.
// This host maps HTTP requests to its controls and existing event handlers.
internal static class WebHost
{
    private static MainWin window;
    private static HttpListener listener;
    private static volatile string stateJson = "{}";
    private static string activeAction;
    private static string authMode = "";
    private static readonly Dictionary<string, string> Numbers = new Dictionary<string, string> {
        {"threadNum", "ThreadNum"}, {"interval", "IntervalNum"}, {"retry", "RetryNum"},
        {"from", "FromNum"}, {"to", "ToNum"}
    };
    private static readonly Dictionary<string, string> Checks = new Dictionary<string, string> {
        {"fromEnabled", "FromCheck"}, {"toEnabled", "ToCheck"},
        {"includeNotice", "NoticeCheck"}, {"removeBlank", "RemoveBlankCheck"},
        {"keepHtml", "KeepHtmlCheck"}, {"compress", "CompressCheck"},
        {"downloadImage", "DownloadImageCheck"}, {"stopOnError", "StopOnErrorCheck"},
        {"includeNovelNo", "IncludeNovelNoCheck"}, {"includeChapterRange", "IncludeChapterRangeCheck"},
        {"vertical", "VerticalCheck"}, {"gothic", "GothicCheck"},
        {"bonusNever", "BonusNeverCheck"}, {"bonusAlways", "BonusAlwaysCheck"}
    };

    [STAThread]
    private static void Main()
    {
        ImageTransport.Register();
        Application.EnableVisualStyles();
        Application.SetCompatibleTextRenderingDefault(false);
        window = new MainWin();
        ControlOf<TextBox>("OutputDirText").Text = "/data/downloads";
        window.ShowInTaskbar = false;
        window.Shown += delegate {
            // Keep the original UI thread and handles alive for Invoke/BeginInvoke.
            foreach (string name in new[] { "ConsoleBox", "DownloadList" }) {
                IntPtr handle = window.Controls.Find(name, true)[0].Handle;
            }
            window.Hide();
            authMode = ControlOf<TextBox>("LoginkeyText").Text.Length > 0 ? "saved" : "";
            CaptureState();
            listener = new HttpListener();
            listener.Prefixes.Add("http://127.0.0.1:8090/");
            listener.Start();
            var thread = new Thread(Listen) { IsBackground = true };
            thread.Start();
        };
        var timer = new System.Windows.Forms.Timer { Interval = 300 };
        timer.Tick += delegate { CaptureState(); };
        timer.Start();
        window.FormClosed += delegate { if (listener != null) listener.Close(); };
        Application.Run(window);
    }

    private static T ControlOf<T>(string name) where T : Control
    {
        return (T)window.Controls.Find(name, true)[0];
    }

    private static object Field(string name)
    {
        return typeof(MainWin).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance).GetValue(window);
    }

    private static void OriginalEvent(string name)
    {
        try {
            typeof(MainWin).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Instance)
                .Invoke(window, new object[] { window, null });
        } catch (TargetInvocationException error) {
            throw error.InnerException ?? error;
        }
    }

    private static void CaptureState()
    {
        var settings = new Dictionary<string, object>();
        var limits = new Dictionary<string, object>();
        foreach (var field in Numbers) {
            var control = ControlOf<NumericUpDown>(field.Value);
            settings[field.Key] = control.Value;
            limits[field.Key] = new { min = control.Minimum, max = control.Maximum };
        }
        foreach (var field in Checks) settings[field.Key] = ControlOf<CheckBox>(field.Value).Checked;
        settings["format"] = ControlOf<RadioButton>("EpubButton").Checked ? "epub" : "txt";
        settings["novelNumber"] = ControlOf<TextBox>("NovelNoText").Text;
        var queue = new List<object>();
        var list = ControlOf<ListBox>("DownloadList");
        for (int i = 0; i < list.Items.Count; i++) queue.Add(new { index = i, label = list.Items[i].ToString() });
        string log = ControlOf<TextBox>("ConsoleBox").Text;
        if (log.Length > 64000) log = log.Substring(log.Length - 64000);
        var snapshot = new {
            settings, limits, queue, log,
            running = (bool)Field("_running"), queueRunning = (bool)Field("_queueRunning"),
            cancelRequested = (bool)Field("_cancelRequested"),
            progress = new { total = Field("_progress_total"), done = Field("_progress_done"),
                failed = Field("_progress_fail"), skipped = Field("_progress_skip") },
            authenticated = authMode.Length > 0, authMode,
            email = ControlOf<TextBox>("EmailText").Text
        };
        stateJson = new JavaScriptSerializer().Serialize(snapshot);
    }

    private static void Listen()
    {
        while (listener.IsListening) {
            HttpListenerContext context;
            try { context = listener.GetContext(); }
            catch (HttpListenerException) { break; }
            catch (ObjectDisposedException) { break; }
            ThreadPool.QueueUserWorkItem(delegate { Handle(context); });
        }
    }

    private static void Send(HttpListenerContext context, int status, object body)
    {
        byte[] bytes = Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(body));
        context.Response.StatusCode = status;
        context.Response.ContentType = "application/json; charset=utf-8";
        context.Response.Headers["Cache-Control"] = "no-store";
        context.Response.ContentLength64 = bytes.Length;
        context.Response.OutputStream.Write(bytes, 0, bytes.Length);
        context.Response.Close();
    }

    private static void Handle(HttpListenerContext context)
    {
        bool claimed = false;
        try {
            string path = context.Request.Url.AbsolutePath;
            string method = context.Request.HttpMethod;
            if (method == "GET" && path == "/healthz") { Send(context, 200, new { ready = true }); return; }
            if (method == "GET" && path == "/api/state") {
                var state = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(stateJson);
                state["action"] = Interlocked.CompareExchange(ref activeAction, null, null);
                Send(context, 200, state);
                return;
            }
            if (method == "POST" && path == "/internal/close") {
                Send(context, 200, new { ok = true });
                window.BeginInvoke(new Action(delegate { window.Close(); }));
                return;
            }
            var routes = new HashSet<string> { "/api/settings", "/api/login", "/api/download",
                "/api/stop", "/api/queue/add", "/api/queue/start", "/api/queue/remove", "/api/queue/clear" };
            if (method != "POST" || !routes.Contains(path)) { Send(context, 404, new { error = "요청한 기능이 없습니다." }); return; }
            // Custom JSON requests require a preflight cross-origin; no CORS is enabled.
            if (context.Request.Headers["X-Requested-With"] != "Novelpia-Web") {
                Send(context, 403, new { error = "웹 화면에서 요청해 주세요." }); return;
            }
            if (context.Request.ContentLength64 > 65536) { Send(context, 413, new { error = "요청이 너무 큽니다." }); return; }
            var buffer = new char[65537];
            int count = 0;
            using (var reader = new StreamReader(context.Request.InputStream, Encoding.UTF8)) {
                while (count < buffer.Length) {
                    int read = reader.Read(buffer, count, buffer.Length - count);
                    if (read == 0) break;
                    count += read;
                }
            }
            if (count > 65536) { Send(context, 413, new { error = "요청이 너무 큽니다." }); return; }
            var data = count == 0 ? new Dictionary<string, object>() :
                new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(new string(buffer, 0, count));
            if (Interlocked.CompareExchange(ref activeAction, path, null) != null) {
                Send(context, 409, new { error = "이전 요청을 처리 중입니다." }); return;
            }
            claimed = true;
            object result = window.Invoke(new Func<object>(delegate {
                object value = Execute(path, data);
                CaptureState();
                return value;
            }));
            Send(context, 200, result);
        } catch (ArgumentException error) {
            Send(context, 400, new { error = error.Message });
        } catch (InvalidOperationException error) {
            Send(context, 409, new { error = error.Message });
        } catch (Exception error) {
            while (error is TargetInvocationException && error.InnerException != null) error = error.InnerException;
            int status = error is ArgumentException ? 400 : error is InvalidOperationException ? 409 : 502;
            try { Send(context, status, new { error = (status == 502 ? "원본 프로그램 요청 실패: " : "") + error.Message }); }
            catch { }
        } finally {
            if (claimed) Interlocked.Exchange(ref activeAction, null);
        }
    }

    private static string Text(Dictionary<string, object> data, string key)
    {
        object value;
        if (!data.TryGetValue(key, out value) || !(value is string)) return "";
        return (string)value;
    }

    private static void ApplySettings(Dictionary<string, object> data)
    {
        object raw;
        if (!data.TryGetValue("settings", out raw) || !(raw is Dictionary<string, object>))
            throw new ArgumentException("다운로드 설정이 필요합니다.");
        var settings = (Dictionary<string, object>)raw;
        var numbers = new Dictionary<string, decimal>();
        foreach (var item in settings) {
            if (Numbers.ContainsKey(item.Key)) {
                decimal value;
                if (!decimal.TryParse(Convert.ToString(item.Value, CultureInfo.InvariantCulture),
                    NumberStyles.Number, CultureInfo.InvariantCulture, out value))
                    throw new ArgumentException("숫자 설정이 올바르지 않습니다: " + item.Key);
                var control = ControlOf<NumericUpDown>(Numbers[item.Key]);
                if (value < control.Minimum || value > control.Maximum ||
                    (item.Key != "interval" && value != Math.Truncate(value)))
                    throw new ArgumentException("설정 범위를 확인해 주세요: " + item.Key);
                numbers[item.Key] = value;
            } else if (Checks.ContainsKey(item.Key)) {
                if (!(item.Value is bool)) throw new ArgumentException("체크 옵션이 올바르지 않습니다.");
            } else if (item.Key == "format") {
                if (!Equals(item.Value, "epub") && !Equals(item.Value, "txt")) throw new ArgumentException("EPUB 또는 TXT를 선택해 주세요.");
            } else if (item.Key == "novelNumber") {
                if (!(item.Value is string) || ((string)item.Value).Length > 2048) throw new ArgumentException("소설 주소가 올바르지 않습니다.");
            } else throw new ArgumentException("알 수 없는 설정: " + item.Key);
        }
        if (settings.ContainsKey("bonusNever") && settings.ContainsKey("bonusAlways") &&
            (bool)settings["bonusNever"] && (bool)settings["bonusAlways"])
            throw new ArgumentException("BONUS 제외와 항상 포함은 동시에 선택할 수 없습니다.");
        foreach (var item in numbers) ControlOf<NumericUpDown>(Numbers[item.Key]).Value = item.Value;
        foreach (var item in settings) {
            if (Checks.ContainsKey(item.Key)) ControlOf<CheckBox>(Checks[item.Key]).Checked = (bool)item.Value;
        }
        if (settings.ContainsKey("format")) {
            ControlOf<RadioButton>("EpubButton").Checked = Equals(settings["format"], "epub");
            ControlOf<RadioButton>("TxtButton").Checked = Equals(settings["format"], "txt");
        }
        if (settings.ContainsKey("novelNumber")) ControlOf<TextBox>("NovelNoText").Text = (string)settings["novelNumber"];
        ControlOf<TextBox>("OutputDirText").Text = "/data/downloads";
    }

    private static object Execute(string path, Dictionary<string, object> data)
    {
        bool running = (bool)Field("_running") || (bool)Field("_queueRunning");
        if (running && path != "/api/stop") throw new InvalidOperationException("다운로드가 끝난 뒤 변경해 주세요.");
        switch (path) {
            case "/api/login":
                if (Text(data, "mode") == "key") {
                    string key = Text(data, "loginKey").Trim();
                    if (key.Length == 0 || key.Length > 4096) throw new ArgumentException("LOGINKEY를 입력해 주세요.");
                    ControlOf<TextBox>("LoginkeyText").Text = key;
                    OriginalEvent("LoginButton2_Click");
                    authMode = "key";
                } else if (Text(data, "mode") == "email") {
                    string email = Text(data, "email");
                    string password = Text(data, "password");
                    if (email.Length == 0 || password.Length == 0 || email.Length > 320 || password.Length > 4096)
                        throw new ArgumentException("이메일과 비밀번호를 입력해 주세요.");
                    ControlOf<TextBox>("EmailText").Text = email;
                    ControlOf<TextBox>("PasswordText").Text = password;
                    OriginalEvent("LoginButton1_Click");
                    var lang = typeof(MainWin).Assembly.GetType("NovelpiaDownloader.Lang");
                    string ok = (string)lang.GetMethod("T", new[] { typeof(string) }).Invoke(null, new object[] { "login_ok" });
                    if (!ControlOf<TextBox>("ConsoleBox").Text.EndsWith(ok))
                        throw new ArgumentException("로그인에 실패했습니다. 원본 로그를 확인해 주세요.");
                    authMode = "email";
                } else throw new ArgumentException("로그인 방식을 선택해 주세요.");
                OriginalEvent("MainWin_FormClosed");
                return new { ok = true, message = authMode == "key" ? "LOGINKEY를 적용했습니다. 접근 권한은 다운로드 시 확인됩니다." : "로그인했습니다." };
            case "/api/settings":
                ApplySettings(data);
                OriginalEvent("MainWin_FormClosed");
                return new { ok = true, message = "설정을 저장했습니다." };
            case "/api/download":
            case "/api/queue/add":
                object rawSettings;
                string novel = ControlOf<TextBox>("NovelNoText").Text;
                if (data.TryGetValue("settings", out rawSettings) && rawSettings is Dictionary<string, object>) {
                    var incoming = (Dictionary<string, object>)rawSettings;
                    if (incoming.ContainsKey("novelNumber")) novel = Text(incoming, "novelNumber");
                }
                if (!Regex.IsMatch(novel, @"\d+")) throw new ArgumentException("소설 URL 또는 번호를 입력해 주세요.");
                ApplySettings(data);
                OriginalEvent(path == "/api/download" ? "DownloadButton_Click" : "AddToListButton_Click");
                OriginalEvent("MainWin_FormClosed");
                return new { ok = true };
            case "/api/stop":
                if ((bool)Field("_running")) OriginalEvent("DownloadButton_Click");
                return new { ok = true };
            case "/api/queue/start":
                OriginalEvent("QueueDownloadButton_Click");
                return new { ok = true };
            case "/api/queue/clear":
                OriginalEvent("QueueDeleteAllButton_Click");
                return new { ok = true };
            case "/api/queue/remove":
                object indices;
                if (!data.TryGetValue("indices", out indices) || !(indices is ArrayList))
                    throw new ArgumentException("삭제할 목록을 선택해 주세요.");
                var list = ControlOf<ListBox>("DownloadList");
                var selected = new List<int>();
                foreach (object raw in (ArrayList)indices) {
                    int index;
                    if (!int.TryParse(Convert.ToString(raw, CultureInfo.InvariantCulture), out index) || index < 0 || index >= list.Items.Count)
                        throw new ArgumentException("목록 번호가 올바르지 않습니다.");
                    selected.Add(index);
                }
                list.ClearSelected();
                foreach (int index in selected) list.SetSelected(index, true);
                OriginalEvent("QueueDeleteSelectedButton_Click");
                return new { ok = true };
        }
        throw new ArgumentException("알 수 없는 요청입니다.");
    }
}
