using System;
using System.Net;

// Mono's TLS stack cannot connect to the image CDN when it requires TLS 1.3.
// Keep the original HttpWebRequest downloader and use container-local nginx
// only for this CDN's HTTPS transport. Nginx validates the upstream certificate.
internal sealed class ImageTransport : IWebRequestCreate
{
    internal static void Register()
    {
        var factory = new ImageTransport();
        foreach (string scheme in new[] { "https", "http" }) {
            if (!WebRequest.RegisterPrefix(scheme + "://images.novelpia.com/", factory))
                throw new InvalidOperationException("Could not register the image CDN transport.");
        }
    }

    public WebRequest Create(Uri uri)
    {
        var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:8091" + uri.PathAndQuery);
        request.Proxy = null;
        return request;
    }
}
