using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using NovelpiaDownloader;

internal static class FileBusyRegression
{
    [STAThread]
    private static int Main(string[] args)
    {
        try {
            var host = Assembly.LoadFrom("/opt/novelpia/NovelpiaWebHost.exe").GetType("WebHost", true);
            var execute = host.GetMethod("Execute", BindingFlags.NonPublic | BindingFlags.Static);
            using (var window = new MainWin()) {
                host.GetField("window", BindingFlags.NonPublic | BindingFlags.Static).SetValue(null, window);
                foreach (string fieldName in new[] { "_running", "_queueRunning" }) {
                    var field = typeof(MainWin).GetField(fieldName, BindingFlags.NonPublic | BindingFlags.Instance);
                    field.SetValue(window, true);
                    foreach (string route in new[] { "/api/files/delete", "/api/files/clear" }) {
                        bool blocked = false;
                        try {
                            execute.Invoke(null, new object[] { route, new Dictionary<string, object> { { "name", args[0] } } });
                        } catch (TargetInvocationException error) {
                            if (!(error.InnerException is InvalidOperationException)) throw;
                            blocked = true;
                        }
                        if (!blocked || !File.Exists(Path.Combine("/data/downloads", args[0])))
                            throw new Exception("Deletion must be blocked while " + fieldName + " is true.");
                    }
                    field.SetValue(window, false);
                }
            }
            Console.WriteLine("PASS: original download/queue running state blocks both deletion operations");
            return 0;
        } catch (Exception error) {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}
