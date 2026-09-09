// Government Bond Futures Converter -- coworker-facing bootstrapper (Issue #206).
//
// This is the whole download. It carries no runtime, no Python and no payload:
// it checks prerequisites, makes sure the one exact runtime named in its
// compiled-in manifest is installed under %LOCALAPPDATA%, and starts the
// converter. Measured at ~9.5 KB.
//
// **C# 5 only.** It is compiled by csc.exe from the in-box .NET Framework, so
// nothing has to be installed to build it and nothing has to be installed to
// run it (.NET Framework 4.x ships with Windows). That compiler predates
// expression-bodied members, inline `out` variables, string interpolation,
// `nameof` and null-conditional operators -- none of which appear below.
// Anything newer would pull in Roslyn and therefore an SDK dependency, which
// is exactly the cost this design exists to avoid.
//
// **Trust model.** The manifest names one immutable version, one URL and one
// SHA-256. There is no "latest", no fall-forward to another version and no
// second host. A payload that does not match its hash is deleted without ever
// being unpacked, so a corrupted or substituted download can never become the
// runtime that prices a trade.
//
// **Nothing is overwritten in place.** A download is verified, unpacked into a
// staging directory and only then promoted; the version marker is written last.
// Every failure before that point leaves whatever was already installed
// working, which is why an interrupted install cannot brick a desk.
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;

static class Bootstrapper
{
    public const string AppName = "Government Bond Futures Converter";
    const string DirName = "GovernmentBondFuturesConverter";

    // Exit codes are part of this program's contract with its tests and with
    // any shortcut that inspects them. 0 is the only success.
    public const int ExitOk = 0;
    public const int ExitPrerequisiteMissing = 2;
    public const int ExitDownloadFailed = 4;
    public const int ExitIntegrityFailed = 5;
    public const int ExitInstallFailed = 6;
    public const int ExitRuntimeUnusable = 7;
    public const int ExitUnexpected = 1;

    // ---------------------------------------------------------------- options

    sealed class Options
    {
        public string InstallRoot;
        public string AssetUrl;
        public string Sha256;
        public long SizeBytes;
        public bool NoLaunch;
    }

    // Every override below is test-only and must be passed explicitly on the
    // command line. A coworker double-clicking passes nothing, so the compiled
    // manifest and the real per-user root are the only things in play --
    // there is no ambient environment variable that can redirect a download.
    static Options Parse(string[] args)
    {
        Options o = new Options();
        o.InstallRoot = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), DirName);
        o.AssetUrl = Manifest.AssetUrl;
        o.Sha256 = Manifest.Sha256;
        o.SizeBytes = Manifest.SizeBytes;
        bool sizeGiven = false;
        for (int i = 0; i < args.Length; i++)
        {
            string a = args[i];
            if (a == "--no-launch") { o.NoLaunch = true; }
            else if (a == "--install-root" && i + 1 < args.Length) { o.InstallRoot = args[++i]; }
            else if (a == "--asset-url" && i + 1 < args.Length) { o.AssetUrl = args[++i]; }
            else if (a == "--sha256" && i + 1 < args.Length)
            {
                o.Sha256 = args[++i];
                // The expected size describes the manifest's own asset. A caller
                // naming a different hash is pointing at a different payload, so
                // the compiled size no longer applies to it -- carrying it over
                // would reject every substituted asset as "truncated" before its
                // hash was ever checked. It can still be stated explicitly.
                if (!sizeGiven) o.SizeBytes = 0;
            }
            else if (a == "--size-bytes" && i + 1 < args.Length)
            {
                long parsed;
                if (long.TryParse(args[++i], NumberStyles.Integer, CultureInfo.InvariantCulture,
                                  out parsed) && parsed >= 0)
                {
                    o.SizeBytes = parsed;
                    sizeGiven = true;
                }
            }
        }
        return o;
    }

    // ------------------------------------------------------------------- main

    static int Main(string[] args)
    {
        Options o = null;
        try
        {
            o = Parse(args);
            Log(o, "start; version=" + Manifest.RuntimeVersion + " root=" + o.InstallRoot);
            RequirePrerequisites(o);

            string exe = InstalledExecutable(o);
            if (exe == null)
            {
                Install(o);
                exe = InstalledExecutable(o);
                if (exe == null)
                {
                    throw new BootstrapError(ExitRuntimeUnusable,
                        AppName + " did not finish installing correctly.\n\n" +
                        "Start it again. If it keeps failing, delete this folder and retry:\n" +
                        o.InstallRoot);
                }
            }
            else
            {
                Log(o, "runtime " + Manifest.RuntimeVersion + " already installed; no network used");
            }

            if (o.NoLaunch) { Log(o, "--no-launch: not starting the converter"); return ExitOk; }
            Launch(o, exe);
            return ExitOk;
        }
        catch (BootstrapError e)
        {
            Log(o, "FAILED(" + e.ExitCode + "): " + e.Message + " | detail: " + e.Detail);
            Report(o, e.Message);
            return e.ExitCode;
        }
        catch (Exception e)
        {
            // The last line of defence. A trader sees a sentence and a support
            // path; the CLR type and stack go to the log only.
            Log(o, "UNEXPECTED: " + e.ToString());
            Report(o, "Something went wrong starting " + AppName + ".\n\n" +
                      "Start it again. If it keeps failing, send the desk's support contact this file:\n" +
                      LogPath(o));
            return ExitUnexpected;
        }
    }

    // ---------------------------------------------------------- prerequisites

    static void RequirePrerequisites(Options o)
    {
        if (!Environment.Is64BitOperatingSystem)
        {
            throw new BootstrapError(ExitPrerequisiteMissing,
                AppName + " needs 64-bit Windows, and this computer is running 32-bit Windows.\n\n" +
                "Ask IT for a 64-bit workstation.");
        }
        if (FindEdge() == null)
        {
            throw new BootstrapError(ExitPrerequisiteMissing,
                "Microsoft Edge could not be found on this computer, and " + AppName +
                " needs it to display its window.\n\nAsk IT to install Microsoft Edge, then start this app again.");
        }
        Log(o, "prerequisites OK (64-bit Windows, Microsoft Edge present)");
    }

    static string FindEdge()
    {
        string[] vars = new string[] { "ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA" };
        for (int i = 0; i < vars.Length; i++)
        {
            string b = Environment.GetEnvironmentVariable(vars[i]);
            if (string.IsNullOrEmpty(b)) continue;
            string p = Path.Combine(b, "Microsoft\\Edge\\Application\\msedge.exe");
            if (File.Exists(p)) return p;
        }
        return null;
    }

    // --------------------------------------------------------- installed state

    static string CurrentFile(Options o) { return Path.Combine(o.InstallRoot, "current.json"); }
    static string RuntimeDir(Options o)
    {
        return Path.Combine(Path.Combine(o.InstallRoot, "runtime"), Manifest.RuntimeVersion);
    }

    /// <summary>The runtime executable, or null when nothing usable is installed.</summary>
    static string InstalledExecutable(Options o)
    {
        string marker = CurrentFile(o);
        if (!File.Exists(marker)) return null;
        string installed;
        try { installed = ReadJsonString(File.ReadAllText(marker), "runtime_version"); }
        catch (Exception) { installed = null; }
        // Unreadable or truncated metadata is treated exactly like "nothing
        // installed": the app reinstalls rather than guessing what is on disk.
        if (installed == null) { Log(o, "current.json is unreadable; reinstalling"); return null; }
        if (installed != Manifest.RuntimeVersion)
        {
            Log(o, "installed version " + installed + " != required " + Manifest.RuntimeVersion);
            return null;
        }
        string exe = Path.Combine(RuntimeDir(o), Manifest.ExecutableRelativePath);
        if (!File.Exists(exe)) { Log(o, "runtime marked installed but its executable is missing"); return null; }
        return exe;
    }

    /// <summary>Read one top-level string field, or null if it is not plainly there.
    ///
    /// Deliberately tiny and strict rather than a JSON parser: the only file
    /// this ever reads is one this program wrote, with two known fields. It
    /// returns null for anything it does not recognise, and the caller treats
    /// null as "nothing is installed" -- so a truncated or hand-edited marker
    /// causes a clean reinstall instead of an error or a wrong launch.
    /// </summary>
    static string ReadJsonString(string json, string field)
    {
        if (json == null) return null;
        string key = "\"" + field + "\"";
        int k = json.IndexOf(key, StringComparison.Ordinal);
        if (k < 0) return null;
        int colon = json.IndexOf(':', k + key.Length);
        if (colon < 0) return null;
        int open = json.IndexOf('"', colon + 1);
        if (open < 0) return null;
        int close = json.IndexOf('"', open + 1);
        if (close < 0) return null;
        return json.Substring(open + 1, close - open - 1);
    }

    // ---------------------------------------------------------------- install

    static void Install(Options o)
    {
        Directory.CreateDirectory(o.InstallRoot);
        string work = Path.Combine(o.InstallRoot, "staging");
        string zip = Path.Combine(work, Guid.NewGuid().ToString("N") + ".partial");
        string unpack = Path.Combine(work, Guid.NewGuid().ToString("N"));
        try
        {
            Directory.CreateDirectory(work);
            Download(o, zip);
            VerifyOrDelete(o, zip);
            Extract(o, zip, unpack);
            Promote(o, unpack);
            // Written last, and only now: until this line exists on disk the
            // install has not happened as far as the next launch is concerned.
            File.WriteAllText(CurrentFile(o),
                "{\n  \"runtime_version\": \"" + Manifest.RuntimeVersion + "\",\n" +
                "  \"installed_utc\": \"" + DateTime.UtcNow.ToString("o", CultureInfo.InvariantCulture) + "\"\n}\n");
            Log(o, "installed " + Manifest.RuntimeVersion);
        }
        finally
        {
            TryDeleteFile(zip);
            TryDeleteDir(unpack);
            TryDeleteDir(work);
        }
    }

    static void Download(Options o, string dest)
    {
        Log(o, "downloading " + o.AssetUrl);
        try
        {
            // TLS 1.2 explicitly: the .NET Framework default on older Windows
            // builds still negotiates TLS 1.0, which GitHub refuses.
            ServicePointManager.SecurityProtocol =
                SecurityProtocolType.Tls12 | (SecurityProtocolType)3072;
            using (WebClient wc = new WebClient())
            {
                wc.Headers.Add("User-Agent", DirName + "-bootstrapper/" + Manifest.RuntimeVersion);
                // Straight to a `.partial` file inside staging. An interrupted
                // download is left there and deleted in the `finally` above; it
                // is never named, hashed as, or promoted to the runtime.
                wc.DownloadFile(o.AssetUrl, dest);
            }
        }
        catch (WebException e)
        {
            throw new BootstrapError(ExitDownloadFailed, DownloadMessage(e), e.ToString());
        }
        catch (IOException e)
        {
            throw new BootstrapError(ExitDownloadFailed,
                "Unable to save the " + AppName + " runtime while downloading it.\n\n" +
                "Check that you have at least 500 MB free disk space, then try again.", e.ToString());
        }
        long size = new FileInfo(dest).Length;
        Log(o, "downloaded " + size.ToString(CultureInfo.InvariantCulture) + " bytes");
        if (o.SizeBytes > 0 && size != o.SizeBytes)
        {
            // A short file is the ordinary shape of an interrupted download, and
            // saying so is more useful than a hash mismatch.
            throw new BootstrapError(ExitDownloadFailed,
                "The download of the " + AppName + " runtime did not complete.\n\n" +
                "Check your network connection and try again.",
                "expected " + o.SizeBytes + " bytes, got " + size);
        }
    }

    /// <summary>Turn a WebException into something a trader can act on.</summary>
    static string DownloadMessage(WebException e)
    {
        string retry = "\n\nCheck your network connection and try again. If this keeps happening, " +
                       "send the desk's support contact this address:\n";
        switch (e.Status)
        {
            case WebExceptionStatus.NameResolutionFailure:
            case WebExceptionStatus.ProxyNameResolutionFailure:
            case WebExceptionStatus.ConnectFailure:
                return "Unable to download the " + AppName + " runtime because the download site " +
                       "could not be reached." + retry;
            case WebExceptionStatus.Timeout:
                return "The download of the " + AppName + " runtime timed out." + retry;
            case WebExceptionStatus.ConnectionClosed:
            case WebExceptionStatus.ReceiveFailure:
            case WebExceptionStatus.KeepAliveFailure:
                return "The download of the " + AppName + " runtime was interrupted before it finished." + retry;
            case WebExceptionStatus.TrustFailure:
            case WebExceptionStatus.SecureChannelFailure:
                return "The secure connection to the download site could not be established.\n\n" +
                       "This is usually a corporate network setting. Please contact IT.";
            case WebExceptionStatus.ProtocolError:
                HttpWebResponse r = e.Response as HttpWebResponse;
                int code = r == null ? 0 : (int)r.StatusCode;
                if (code == 404)
                {
                    return "The " + AppName + " runtime for version " + Manifest.RuntimeVersion +
                           " is not available at the expected address.\n\n" +
                           "Please contact the desk's support contact -- this build may not have been published yet.";
                }
                return "The download site refused the request for the " + AppName +
                       " runtime (error " + code.ToString(CultureInfo.InvariantCulture) + ")." + retry;
            default:
                return "Unable to download the " + AppName + " runtime." + retry;
        }
    }

    static void VerifyOrDelete(Options o, string zip)
    {
        string actual = Sha256(zip);
        Log(o, "sha256 " + actual);
        if (!string.Equals(actual, o.Sha256, StringComparison.OrdinalIgnoreCase))
        {
            // Deleted here, not merely rejected: nothing that failed its hash
            // stays on disk where a later run or a curious user could open it.
            TryDeleteFile(zip);
            throw new BootstrapError(ExitIntegrityFailed,
                "The downloaded " + AppName + " runtime failed its safety check and was not installed.\n\n" +
                "Nothing on this computer was changed. Try again; if it keeps failing, " +
                "contact the desk's support contact.",
                "expected " + o.Sha256 + " got " + actual);
        }
        Log(o, "integrity verified before anything was unpacked");
    }

    static string Sha256(string path)
    {
        using (FileStream s = File.OpenRead(path))
        using (SHA256 h = SHA256.Create())
        {
            byte[] d = h.ComputeHash(s);
            StringBuilder sb = new StringBuilder(d.Length * 2);
            for (int i = 0; i < d.Length; i++) sb.Append(d[i].ToString("x2", CultureInfo.InvariantCulture));
            return sb.ToString();
        }
    }

    static void Extract(Options o, string zip, string unpack)
    {
        try
        {
            Directory.CreateDirectory(unpack);
            ZipFile.ExtractToDirectory(zip, unpack);
        }
        catch (InvalidDataException e)
        {
            throw new BootstrapError(ExitInstallFailed,
                "The downloaded " + AppName + " runtime could not be opened.\n\n" +
                "Nothing on this computer was changed. Try again; if it keeps failing, " +
                "contact the desk's support contact.", e.ToString());
        }
        catch (IOException e)
        {
            throw new BootstrapError(ExitInstallFailed,
                "There was not enough room to install the " + AppName + " runtime.\n\n" +
                "Free up at least 500 MB of disk space and try again.", e.ToString());
        }
        catch (UnauthorizedAccessException e)
        {
            throw new BootstrapError(ExitInstallFailed,
                "Windows would not let " + AppName + " write to:\n" + o.InstallRoot +
                "\n\nThis usually means security software is blocking it. Please contact IT.", e.ToString());
        }
        Log(o, "unpacked to staging");
    }

    /// <summary>Find the payload root inside the staging tree.</summary>
    static string PayloadRoot(string unpack)
    {
        if (File.Exists(Path.Combine(unpack, Manifest.ExecutableRelativePath))) return unpack;
        string[] dirs = Directory.GetDirectories(unpack);
        for (int i = 0; i < dirs.Length; i++)
        {
            if (File.Exists(Path.Combine(dirs[i], Manifest.ExecutableRelativePath))) return dirs[i];
        }
        return null;
    }

    static void Promote(Options o, string unpack)
    {
        string payload = PayloadRoot(unpack);
        if (payload == null)
        {
            throw new BootstrapError(ExitInstallFailed,
                "The downloaded " + AppName + " runtime is not the expected package.\n\n" +
                "Nothing on this computer was changed. Please contact the desk's support contact.",
                "no " + Manifest.ExecutableRelativePath + " under " + unpack);
        }
        string target = RuntimeDir(o);
        Directory.CreateDirectory(Path.GetDirectoryName(target));
        string displaced = null;
        try
        {
            // The previous copy is moved aside rather than deleted, so if the
            // promotion below fails the good runtime can be put straight back.
            if (Directory.Exists(target))
            {
                displaced = target + ".replaced-" + Guid.NewGuid().ToString("N");
                Directory.Move(target, displaced);
            }
            Directory.Move(payload, target);
        }
        catch (IOException e)
        {
            RestoreDisplaced(target, displaced);
            throw new BootstrapError(ExitInstallFailed,
                "The " + AppName + " runtime could not be installed.\n\n" +
                "Close the app if it is already open, then try again.", e.ToString());
        }
        catch (UnauthorizedAccessException e)
        {
            RestoreDisplaced(target, displaced);
            throw new BootstrapError(ExitInstallFailed,
                "Windows would not let " + AppName + " finish installing to:\n" + o.InstallRoot +
                "\n\nThis usually means security software is blocking it. Please contact IT.", e.ToString());
        }
        TryDeleteDir(displaced);
        Log(o, "promoted into " + target);
    }

    static void RestoreDisplaced(string target, string displaced)
    {
        if (displaced == null || !Directory.Exists(displaced)) return;
        try
        {
            if (Directory.Exists(target)) return;
            Directory.Move(displaced, target);
        }
        catch (Exception) { /* the throw that follows carries the real message */ }
    }

    // ----------------------------------------------------------------- launch

    static void Launch(Options o, string exe)
    {
        Log(o, "launching " + exe);
        try
        {
            // UseShellExecute = true, deliberately: with `false` the converter
            // inherits this process's console and dies the moment the
            // bootstrapper exits, which is exactly what a double-click does.
            ProcessStartInfo psi = new ProcessStartInfo(exe);
            psi.UseShellExecute = true;
            psi.WorkingDirectory = Path.GetDirectoryName(exe);
            Process.Start(psi);
        }
        catch (Exception e)
        {
            throw new BootstrapError(ExitRuntimeUnusable,
                AppName + " is installed but would not start.\n\n" +
                "Try again. If it keeps failing, delete this folder and start the app again:\n" +
                o.InstallRoot, e.ToString());
        }
    }

    // ------------------------------------------------------------- clean-up

    // Best effort by design. These only ever run against paths this program
    // created inside its own install root, and a file still held open by
    // antivirus must not turn a successful install into an error dialog -- the
    // leftover is a stale temp file, which the next run overwrites anyway.
    static void TryDeleteFile(string path)
    {
        try { if (path != null && File.Exists(path)) File.Delete(path); }
        catch (Exception) { }
    }

    static void TryDeleteDir(string path)
    {
        try { if (path != null && Directory.Exists(path)) Directory.Delete(path, true); }
        catch (Exception) { }
    }

    // -------------------------------------------------------- errors and log

    sealed class BootstrapError : Exception
    {
        public readonly int ExitCode;
        public readonly string Detail;
        public BootstrapError(int exitCode, string message) : this(exitCode, message, "") { }
        public BootstrapError(int exitCode, string message, string detail) : base(message)
        {
            ExitCode = exitCode;
            Detail = detail;
        }
    }

    static void Report(Options o, string message)
    {
        // `--no-launch` is the automation seam, and a modal dialog with nobody
        // to click it would hang forever -- a smoke test, a scheduled build or
        // any non-interactive caller would simply never return. The message is
        // already in the log, which is what such a caller reads.
        if (o != null && o.NoLaunch) { Log(o, "REPORT (suppressed, non-interactive): " + message); return; }
        // Otherwise no console exists in the shipped (winexe) build, so the
        // message box is the only channel a trader has.
        // MB_OK | MB_ICONERROR | MB_SETFOREGROUND.
        try { MessageBoxW(IntPtr.Zero, message, AppName, 0x10 | 0x10000); }
        catch (Exception) { }
    }

    [System.Runtime.InteropServices.DllImport("user32.dll", CharSet = System.Runtime.InteropServices.CharSet.Unicode)]
    static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    static string LogPath(Options o)
    {
        string root = o == null
            ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), DirName)
            : o.InstallRoot;
        return Path.Combine(Path.Combine(root, "logs"), "bootstrapper.log");
    }

    /// <summary>Append one diagnostic line to a local file. Never sent anywhere.</summary>
    static void Log(Options o, string line)
    {
        try
        {
            string p = LogPath(o);
            Directory.CreateDirectory(Path.GetDirectoryName(p));
            // Trimmed rather than rotated: this is a support aid, not a record.
            if (File.Exists(p) && new FileInfo(p).Length > 262144) File.Delete(p);
            File.AppendAllText(p,
                DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss'Z'", CultureInfo.InvariantCulture) +
                " " + line + Environment.NewLine);
        }
        catch (Exception) { /* diagnostics must never be the reason a launch fails */ }
    }
}
