/** "Chrome · Windows" from a User-Agent (sessions list). Unknown → "Dispositivo desconhecido". */
export function describeDevice(ua: string | null | undefined): string {
  if (!ua) return "Dispositivo desconhecido";
  const browser =
    /Edg\//.test(ua) ? "Edge"
    : /OPR\/|Opera/.test(ua) ? "Opera"
    : /Firefox\//.test(ua) ? "Firefox"
    : /SamsungBrowser\//.test(ua) ? "Samsung Internet"
    : /Chrome\/|CriOS\//.test(ua) ? "Chrome"
    : /Safari\//.test(ua) ? "Safari"
    : /python-requests|httpx|curl|okhttp|axios/i.test(ua) ? "Integração (API)"
    : null;
  const os =
    /Android/.test(ua) ? "Android"
    : /iPhone|iPad|iPod/.test(ua) ? "iOS"
    : /Windows/.test(ua) ? "Windows"
    : /Mac OS X|Macintosh/.test(ua) ? "macOS"
    : /CrOS/.test(ua) ? "ChromeOS"
    : /Linux/.test(ua) ? "Linux"
    : null;
  if (!browser && !os) return "Dispositivo desconhecido";
  return [browser, os].filter(Boolean).join(" · ");
}
