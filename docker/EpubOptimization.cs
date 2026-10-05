using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

// Python performs local EPUB postprocessing on a worker; the original UI stays responsive.
internal sealed class EpubOptimization
{
    private volatile bool running;
    private readonly object gate = new object();
    private Dictionary<string, object> state = new Dictionary<string, object> { { "running", false } };
    public bool IsRunning { get { return running; } }

    public Dictionary<string, object> Snapshot()
    {
        lock (gate) return new Dictionary<string, object>(state);
    }

    private void Update(params object[] values)
    {
        lock (gate) for (int i = 0; i < values.Length; i += 2) state[(string)values[i]] = values[i + 1];
    }

    internal static string Size(long bytes)
    {
        return (bytes / 1048576.0).ToString("0.0", CultureInfo.InvariantCulture) + " MB";
    }

    public void Start(List<string> names, Action<string> log)
    {
        if (running) throw new InvalidOperationException("이미지 최적화를 처리 중입니다.");
        running = true;
        lock (gate) state = new Dictionary<string, object> {
            { "running", true }, { "id", Guid.NewGuid().ToString("N") }, { "status", "running" },
            { "file", names[0] }, { "done", 0 }, { "total", 0 },
            { "filesDone", 0 }, { "fileCount", names.Count }, { "message", "이미지 최적화 중" }
        };
        ThreadPool.QueueUserWorkItem(delegate {
            int completed = 0, errors = 0;
            var results = new List<object>();
            string message = "";
            try {
                foreach (string name in names) {
                    Update("file", name, "done", 0, "total", 0);
                    log("\r\n🖼 이미지 최적화 시작: " + name + "\r\n");
                    try {
                        var result = Run(name, delegate(Dictionary<string, object> progress) {
                            Update("done", progress["done"], "total", progress["total"]);
                        });
                        results.Add(result);
                        if (Convert.ToBoolean(result["changed"])) {
                            long before = Convert.ToInt64(result["originalBytes"]);
                            long after = Convert.ToInt64(result["optimizedBytes"]);
                            message = Size(before) + " → " + Size(after) + " (" +
                                ((1 - (double)after / before) * 100).ToString("0.0", CultureInfo.InvariantCulture) + "% 감소)";
                            log("✓ 이미지 최적화 완료: " + message + "\r\n  저장 파일: " + result["name"] + "\r\n");
                        } else {
                            message = "더 줄일 이미지가 없어 원본을 유지했습니다.";
                            log("✓ " + message + "\r\n");
                        }
                    } catch (Exception error) {
                        errors++;
                        message = "이미지 최적화 실패: " + error.Message;
                        log("✗ " + name + "\r\n  " + message + " (원본 유지)\r\n");
                    }
                    completed++;
                    Update("filesDone", completed);
                }
            } finally {
                if (names.Count > 1) message = "이미지 최적화 " + (completed - errors) + "개 완료" + (errors > 0 ? ", " + errors + "개 실패" : "");
                Update("running", false, "status", errors > 0 ? "error" : "complete", "message", message, "results", results);
                running = false;
            }
        });
    }

    private Dictionary<string, object> Run(string name, Action<Dictionary<string, object>> progress)
    {
        var start = new ProcessStartInfo("python3", "/opt/service/optimize_epub.py") {
            UseShellExecute = false, RedirectStandardInput = true, RedirectStandardOutput = true,
            RedirectStandardError = true, CreateNoWindow = true
        };
        var serializer = new JavaScriptSerializer();
        using (var process = new Process { StartInfo = start }) {
            var diagnostics = new StringBuilder();
            process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs args) {
                if (args.Data != null) lock (diagnostics) {
                    if (diagnostics.Length < 2048) diagnostics.AppendLine(args.Data);
                }
            };
            process.Start();
            process.BeginErrorReadLine();
            // Structured stdin avoids shell/command-line interpretation of book filenames.
            process.StandardInput.WriteLine(serializer.Serialize(new { name }));
            process.StandardInput.Close();
            Dictionary<string, object> result = null;
            string failure = null, line;
            while ((line = process.StandardOutput.ReadLine()) != null) {
                var item = serializer.Deserialize<Dictionary<string, object>>(line);
                string kind = Convert.ToString(item["event"]);
                if (kind == "progress") progress(item);
                else if (kind == "result") result = item;
                else if (kind == "error") failure = Convert.ToString(item["message"]);
            }
            process.WaitForExit();
            if (process.ExitCode != 0 || failure != null || result == null)
                throw new InvalidOperationException(failure ?? "파일을 처리하지 못했습니다.");
            return result;
        }
    }
}
