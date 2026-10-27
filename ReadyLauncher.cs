using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;

internal static class PortableLauncher
{
    private const uint MbIconError = 0x00000010;
    private const uint MbYesNo = 0x00000004;
    private const uint MbIconQuestion = 0x00000020;
    private const uint MbDefaultButton2 = 0x00000100;
    private const int IdYes = 6;

    [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern int MessageBoxW(IntPtr owner, string text, string caption, uint type);

    private static string QuoteArgument(string value)
    {
        if (value.Length > 0 && value.IndexOfAny(new[] { ' ', '\t', '\n', '\v', '"' }) < 0)
        {
            return value;
        }

        var output = new StringBuilder();
        output.Append('"');
        var backslashes = 0;
        foreach (var character in value)
        {
            if (character == '\\')
            {
                backslashes++;
                continue;
            }

            if (character == '"')
            {
                output.Append('\\', backslashes * 2 + 1);
                output.Append('"');
                backslashes = 0;
                continue;
            }

            output.Append('\\', backslashes);
            backslashes = 0;
            output.Append(character);
        }

        output.Append('\\', backslashes * 2);
        output.Append('"');
        return output.ToString();
    }

    private static int Fail(string applicationName, string message)
    {
        MessageBoxW(IntPtr.Zero, message, applicationName, MbIconError);
        return 1;
    }

    private static bool IsSupportedSelfTest(string applicationName, string[] arguments)
    {
        // These exact command forms enter the apps' audited, non-mutating
        // diagnostic branches. An extra or unrelated argument never bypasses
        // the normal first-run license prompt.
        if (applicationName == "Mass Installer")
        {
            return arguments.Length == 1 && arguments[0] == "--self-test";
        }
        if (applicationName != "Screen Color Changer" && applicationName != "Auto Clicker" &&
            applicationName != "File Converter" && applicationName != "Video + Audio Downloader")
        {
            return false;
        }
        return arguments.Length == 2 && arguments[0] == "--self-test" &&
            Directory.Exists(arguments[1]) &&
            Directory.GetFileSystemEntries(arguments[1]).Length == 0;
    }

    private static bool ConfirmLicense(string root, string applicationName)
    {
        var license = Path.Combine(root, "LICENSE");
        if (!File.Exists(license))
        {
            throw new IOException("The bundled Tool License is missing. Extract the complete official ZIP again.");
        }
        string digest;
        using (var input = File.OpenRead(license))
        using (var sha = SHA256.Create())
        {
            digest = BitConverter.ToString(sha.ComputeHash(input)).Replace("-", "");
        }
        var runtime = Path.Combine(root, ".runtime");
        var marker = Path.Combine(runtime, "license-accepted-v1.txt");
        if (File.Exists(marker) && File.ReadAllText(marker).Trim() == digest)
        {
            return true;
        }
        var answer = MessageBoxW(
            IntPtr.Zero,
            "Before using " + applicationName + ":\n\n" +
            "Read the bundled LICENSE and THIRD_PARTY_NOTICES.txt files.\n" +
            "The site Terms are at https://fleece.wtf/terms\n\n" +
            "This ready-to-run version uses a private runtime in this folder. " +
            "It does not install system Python or change PATH.\n\n" +
            "Choose Yes to accept the Terms and Tool License and open the app. " +
            "Choose No to close without opening it.",
            applicationName + " - first run",
            MbYesNo | MbIconQuestion | MbDefaultButton2
        );
        if (answer != IdYes) return false;
        Directory.CreateDirectory(runtime);
        File.WriteAllText(marker, digest + Environment.NewLine, Encoding.ASCII);
        return true;
    }

    [STAThread]
    private static int Main(string[] arguments)
    {
        var launcherPath = Process.GetCurrentProcess().MainModule.FileName;
        var root = Path.GetDirectoryName(launcherPath);
        var applicationName = Path.GetFileNameWithoutExtension(launcherPath);
        var pythonw = Path.Combine(root, ".runtime", "python", "pythonw.exe");
        var applicationScript = Path.Combine(root, applicationName + ".pyw");

        if (!File.Exists(pythonw) || !File.Exists(applicationScript))
        {
            return Fail(
                applicationName,
                "This ready-to-run folder is incomplete.\n\n" +
                "Extract the entire official ZIP into one normal folder, then try again."
            );
        }

        try
        {
            var diagnosticMode = IsSupportedSelfTest(applicationName, arguments);
            if (!diagnosticMode && !ConfirmLicense(root, applicationName))
            {
                return 0;
            }
            // Command diagnostics must run in python.exe. Some apps otherwise
            // re-exec from pythonw.exe and return before their test completes.
            // CreateNoWindow keeps this diagnostic console runtime hidden.
            var runtimeExecutable = diagnosticMode ? Path.Combine(root, ".runtime", "python", "python.exe") : pythonw;
            if (!File.Exists(runtimeExecutable))
            {
                return Fail(applicationName, "The private diagnostic runtime is missing. Extract the complete official ZIP again.");
            }
            var command = new List<string>();
            command.Add("-I");
            command.Add("-X");
            command.Add("utf8");
            command.Add(QuoteArgument(applicationScript));
            foreach (var argument in arguments)
            {
                command.Add(QuoteArgument(argument));
            }
            var start = new ProcessStartInfo();
            start.FileName = runtimeExecutable;
            start.Arguments = string.Join(" ", command.ToArray());
            start.WorkingDirectory = root;
            start.UseShellExecute = false;
            start.CreateNoWindow = true;

            using (var child = Process.Start(start))
            {
                if (child == null)
                {
                    return Fail(applicationName, "Windows could not start the private application runtime.");
                }
                child.WaitForExit();
                return child.ExitCode;
            }
        }
        catch (Exception error)
        {
            return Fail(
                applicationName,
                "Windows could not start this application.\n\n" + error.Message
            );
        }
    }
}
