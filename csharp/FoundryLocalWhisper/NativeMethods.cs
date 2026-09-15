using System.Runtime.InteropServices;
using System.Text;

namespace FoundryLocalWhisper;

/// <summary>P/Invoke surface mirroring the Win32 calls used by the Python ctypes implementation.</summary>
internal static class NativeMethods
{
    public const int WH_KEYBOARD_LL = 13;
    public const int WM_KEYDOWN = 0x0100;
    public const int WM_KEYUP = 0x0101;
    public const int WM_SYSKEYDOWN = 0x0104;
    public const int WM_SYSKEYUP = 0x0105;

    public const int VK_LCONTROL = 0xA2;
    public const int VK_RCONTROL = 0xA3;
    public const int VK_LWIN = 0x5B;
    public const int VK_RWIN = 0x5C;
    public const int VK_LMENU = 0xA4;
    public const int VK_RMENU = 0xA5;
    public const int VK_Q = 0x51;
    public const int VK_CONTROL = 0x11;
    public const int VK_V = 0x56;

    public const uint KEYEVENTF_KEYUP = 0x0002;

    [StructLayout(LayoutKind.Sequential)]
    public struct KBDLLHOOKSTRUCT
    {
        public uint vkCode;
        public uint scanCode;
        public uint flags;
        public uint time;
        public nint dwExtraInfo;
    }

    public delegate nint LowLevelKeyboardProc(int nCode, nint wParam, nint lParam);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern nint SetWindowsHookEx(int idHook, LowLevelKeyboardProc lpfn, nint hMod, uint dwThreadId);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static extern bool UnhookWindowsHookEx(nint hhk);

    [DllImport("user32.dll")]
    public static extern nint CallNextHookEx(nint hhk, int nCode, nint wParam, nint lParam);

    [DllImport("user32.dll")]
    public static extern short GetAsyncKeyState(int vKey);

    [DllImport("user32.dll")]
    public static extern void keybd_event(byte bVk, byte bScan, uint dwFlags, nint dwExtraInfo);

    [DllImport("kernel32.dll")]
    public static extern bool Beep(uint freq, uint duration);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    public static extern int MessageBox(nint hWnd, string text, string caption, uint type);

    public const uint CF_UNICODETEXT = 13;
    public const uint GMEM_MOVEABLE = 0x0002;

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool OpenClipboard(nint hWndNewOwner);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool EmptyClipboard();

    [DllImport("user32.dll", SetLastError = true)]
    private static extern nint SetClipboardData(uint uFormat, nint hMem);

    [DllImport("user32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool CloseClipboard();

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nint GlobalAlloc(uint uFlags, nuint dwBytes);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nint GlobalLock(nint hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool GlobalUnlock(nint hMem);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern nint GlobalFree(nint hMem);

    /// <summary>
    /// Raw Win32 clipboard write, mirroring transcribe.py's ctypes implementation exactly.
    /// Deliberately avoids System.Windows.Forms.Clipboard: its OLE IDataObject marshaling adds
    /// tens of ms over the direct OpenClipboard/SetClipboardData path the benchmark measured,
    /// and unlike OLE clipboard formats, raw SetClipboardData has no STA-thread requirement.
    /// </summary>
    public static void SetClipboardText(string text)
    {
        if (!OpenClipboard(0))
        {
            throw new InvalidOperationException("OpenClipboard failed");
        }

        var hGlobal = nint.Zero;
        try
        {
            if (!EmptyClipboard())
            {
                throw new InvalidOperationException("EmptyClipboard failed");
            }

            var bytes = Encoding.Unicode.GetBytes(text + "\0");
            hGlobal = GlobalAlloc(GMEM_MOVEABLE, (nuint)bytes.Length);
            if (hGlobal == 0)
            {
                throw new InvalidOperationException("GlobalAlloc failed");
            }

            var pGlobal = GlobalLock(hGlobal);
            if (pGlobal == 0)
            {
                throw new InvalidOperationException("GlobalLock failed");
            }
            try
            {
                Marshal.Copy(bytes, 0, pGlobal, bytes.Length);
            }
            finally
            {
                GlobalUnlock(hGlobal);
            }

            if (SetClipboardData(CF_UNICODETEXT, hGlobal) == 0)
            {
                throw new InvalidOperationException("SetClipboardData failed");
            }
            hGlobal = nint.Zero;
        }
        finally
        {
            if (hGlobal != 0)
            {
                GlobalFree(hGlobal);
            }
            CloseClipboard();
        }
    }

    public static void SimulateCtrlV()
    {
        Thread.Sleep(80);
        keybd_event(VK_CONTROL, 0, 0, 0);
        keybd_event(VK_V, 0, 0, 0);
        Thread.Sleep(30);
        keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0);
        keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0);
    }

    public static void BeepAsync(uint freq, uint durationMs)
    {
        Task.Run(() =>
        {
            try { Beep(freq, durationMs); } catch (Exception) { /* best-effort */ }
        });
    }

    public static bool IsKeyDown(int vk) => (GetAsyncKeyState(vk) & 0x8000) != 0;
}
