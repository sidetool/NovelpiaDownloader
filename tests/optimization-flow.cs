using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Windows.Forms;
using NovelpiaDownloader;

internal static class OptimizationFlow
{
    private static Type host;
    private static MainWin window;
    private static object Call(string method, params object[] args)
    {
        return host.GetMethod(method, BindingFlags.NonPublic | BindingFlags.Static).Invoke(null, args);
    }
    private static object HostField(string name) { return host.GetField(name, BindingFlags.NonPublic | BindingFlags.Static).GetValue(null); }
    private static FieldInfo OriginalField(string name) { return typeof(MainWin).GetField(name, BindingFlags.NonPublic | BindingFlags.Instance); }
    private static bool Busy() { var value = HostField("optimizer"); return (bool)value.GetType().GetProperty("IsRunning").GetValue(value, null); }
    private static void Assert(bool value, string message) { if (!value) throw new Exception(message); }
    private static void Wait()
    {
        DateTime until = DateTime.UtcNow.AddSeconds(60);
        while (Busy() && DateTime.UtcNow < until) { Application.DoEvents(); Thread.Sleep(20); }
        Application.DoEvents();
        Assert(!Busy(), "Optimizer did not finish");
    }

    private sealed class LocalTitle : IWebRequestCreate
    {
        public string Address;
        public WebRequest Create(Uri uri) { return WebRequest.CreateHttp(Address); }
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try {
            // Exercise the original add/duplicate handlers without contacting Novelpia.
            var port = new TcpListener(IPAddress.Loopback, 0); port.Start();
            int number = ((IPEndPoint)port.LocalEndpoint).Port; port.Stop();
            var listener = new HttpListener();
            string address = "http://127.0.0.1:" + number + "/";
            listener.Prefixes.Add(address); listener.Start();
            var server = new Thread(delegate() {
                while (listener.IsListening) {
                    HttpListenerContext request;
                    try { request = listener.GetContext(); } catch { break; }
                    byte[] bytes = Encoding.UTF8.GetBytes("productName = 'generated fixture';");
                    request.Response.ContentLength64 = bytes.Length;
                    request.Response.OutputStream.Write(bytes, 0, bytes.Length);
                    request.Response.Close();
                }
            }) { IsBackground = true }; server.Start();
            WebRequest.RegisterPrefix("https://novelpia.com/novel/", new LocalTitle { Address = address });
            host = Assembly.LoadFrom("/opt/novelpia/NovelpiaWebHost.exe").GetType("WebHost", true);
            using (window = new MainWin()) {
                host.GetField("window", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, window);
                IntPtr handle = window.Handle;
                handle = window.Controls.Find("ConsoleBox", true)[0].Handle;
                ((TextBox)window.Controls.Find("OutputDirText", true)[0]).Text = "/data/downloads";
                string stem = "AUTO-" + Guid.NewGuid().ToString("N");
                string single = "/data/downloads/" + stem + "-single.epub";
                Call("BeginDownloadWatch", new object[] { null });
                OriginalField("_running").SetValue(window, true);
                File.Copy(args[0], single);
                Call("CheckCompletedDownload");
                Assert(!Busy(), "Optimization must wait for the original downloader");
                OriginalField("_running").SetValue(window, false);
                Call("CheckCompletedDownload");
                Assert(Busy(), "Finished single download did not start postprocessing");
                try {
                    Call("Execute", "/api/files/clear", new Dictionary<string, object>());
                    throw new Exception("Files must not be deleted during optimization");
                } catch (TargetInvocationException error) {
                    Assert(error.InnerException is InvalidOperationException, "Wrong busy response");
                }
                Wait();
                Assert(File.Exists(Path.ChangeExtension(single, null) + " [최적화].epub"), "Single optimized copy missing");

                var options = new Dictionary<string, object> { { "novelNumber", "41" }, { "format", "epub" }, { "optimizeImages", true } };
                Call("Execute", "/api/queue/add", new Dictionary<string, object> { { "settings", options } });
                var jobs = (IList)OriginalField("_queue").GetValue(window);
                var flags = (IDictionary)HostField("queueOptimization");
                Assert(jobs.Count == 1 && (bool)flags[jobs[0]], "Queue did not capture enabled option");
                options["optimizeImages"] = false;
                Call("Execute", "/api/queue/add", new Dictionary<string, object> { { "settings", options } });
                Assert(jobs.Count == 1 && (bool)flags[jobs[0]], "Duplicate add changed the captured option");
                options["novelNumber"] = "42";
                Call("Execute", "/api/queue/add", new Dictionary<string, object> { { "settings", options } });
                Assert(jobs.Count == 2 && !(bool)flags[jobs[1]], "Queue did not capture disabled option");
                string enabled = "/data/downloads/" + stem + "-enabled.epub";
                string disabled = "/data/downloads/" + stem + "-disabled.epub";
                jobs[0].GetType().GetField("targetPath").SetValue(jobs[0], enabled);
                jobs[1].GetType().GetField("targetPath").SetValue(jobs[1], disabled);
                Call("BeginDownloadWatch", new List<object> { jobs[0] });
                OriginalField("_queueRunning").SetValue(window, true);
                File.Copy(args[0], enabled); File.Copy(args[0], disabled);
                Call("CheckCompletedDownload");
                Assert(!Busy(), "Optimization must wait until the queue finishes");
                OriginalField("_queueRunning").SetValue(window, false);
                Call("CheckCompletedDownload"); Wait();
                Assert(File.Exists(Path.ChangeExtension(enabled, null) + " [최적화].epub"), "Enabled queue copy missing");
                Assert(!File.Exists(Path.ChangeExtension(disabled, null) + " [최적화].epub"), "Disabled queue job was optimized");
                // Unchanged files from a failed download must not be processed again.
                Call("BeginDownloadWatch", new List<object> { jobs[0] });
                Call("CheckCompletedDownload"); Assert(!Busy(), "Unchanged previous output was optimized again");
                options["format"] = "txt"; options["novelNumber"] = "43"; options["optimizeImages"] = true;
                Call("Execute", "/api/queue/add", new Dictionary<string, object> { { "settings", options } });
                Assert(!(bool)flags[jobs[2]], "TXT must not request image optimization");
                listener.Close();
            }
            Console.WriteLine("PASS: single/queue completion, captured options, original duplicate handling, TXT/no-change and busy guards");
            return 0;
        } catch (Exception error) { Console.Error.WriteLine(error); return 1; }
    }
}
