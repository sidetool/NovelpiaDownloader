using System;
using System.Collections.Generic;
using System.IO;
using System.Net;
using System.Reflection;
using System.Security.Cryptography;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using NovelpiaDownloader;

internal static class ImageRegression
{
    private const BindingFlags PrivateInstance = BindingFlags.NonPublic | BindingFlags.Instance;

    private static void Require(bool value, string message)
    {
        if (!value) throw new Exception(message);
    }

    private static byte[] Digest(string path)
    {
        Require(File.Exists(path), "The original downloader did not create " + Path.GetFileName(path));
        using (var sha = SHA256.Create()) return sha.ComputeHash(File.ReadAllBytes(path));
    }

    private static void SameImage(string path)
    {
        Require(Convert.ToBase64String(Digest(path)) == Convert.ToBase64String(Digest("expected.bin")),
            "Image bytes differ from the HTTPS CDN response.");
    }

    [STAThread]
    private static int Main(string[] args)
    {
        try {
            string url = args[0];
            Assembly.LoadFrom("/opt/novelpia/NovelpiaWebHost.exe").GetType("ImageTransport", true)
                .GetMethod("Register", BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, null);
            foreach (string address in new[] { url, url.Replace("https://", "http://") }) {
                var request = (HttpWebRequest)WebRequest.Create(address);
                Require(request.RequestUri.Host == "127.0.0.1" && request.RequestUri.Port == 8091,
                    "Image requests must use the private transport.");
            }
            Require(WebRequest.Create("https://novelpia.com/novel/0").RequestUri.Host == "novelpia.com",
                "The transport must leave original login and chapter requests alone.");
            Require(WebRequest.Create("https://images.novelpia.com.example.org/test").RequestUri.Host != "127.0.0.1",
                "The transport must match the exact CDN host.");

            using (var window = new MainWin()) {
                typeof(MainWin).GetMethod("DownloadImage", PrivateInstance)
                    .Invoke(window, new object[] { url, "cover.bin", "표지", false });
                SameImage("cover.bin");
                Console.WriteLine("PASS: original cover downloader preserves CDN image bytes");

                string imageUrl = url.Substring("https:".Length);
                var chapter = new { s = new[] { new { text = "<img alt=\"test\" src=\"" + imageUrl + "\" width=\"100%\"/>" } } };
                File.WriteAllText("chapter.json", new JavaScriptSerializer().Serialize(chapter));
                var imageContextType = typeof(MainWin).GetNestedType("ImageContext", BindingFlags.NonPublic);
                var context = Activator.CreateInstance(imageContextType, true);
                string html = (string)typeof(MainWin).GetMethod("BuildChapterHtml", PrivateInstance)
                    .Invoke(window, new object[] { "Image test", Path.GetFullPath("chapter.json"), true, false, true, context });
                int count = 0;
                var paths = (IEnumerable<KeyValuePair<int, string>>)imageContextType.GetProperty("Paths").GetValue(context, null);
                foreach (var image in paths) {
                    SameImage(image.Value);
                    Require(html != null && html.Contains("../Images/" + image.Key + ".__EXT__"), "Missing EPUB illustration reference.");
                    count++;
                }
                Require(count == 1, "The fixture must produce one EPUB illustration.");
                var typeArgs = new object[] { "cover.bin", null, null };
                Require((bool)typeof(MainWin).GetMethod("DetectImageType", BindingFlags.Static | BindingFlags.NonPublic)
                    .Invoke(null, typeArgs), "Original image format detection failed.");
                Console.WriteLine("PASS: original inline illustration download and EPUB HTML reference (" + typeArgs[1] + ")");
            }
            return 0;
        } catch (Exception error) {
            Console.Error.WriteLine(error);
            return 1;
        }
    }
}
