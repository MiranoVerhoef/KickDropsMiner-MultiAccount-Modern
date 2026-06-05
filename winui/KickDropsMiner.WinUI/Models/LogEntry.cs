namespace KickDropsMiner_WinUI.Models;

public sealed class LogEntry
{
    public DateTimeOffset Time { get; set; } = DateTimeOffset.Now;
    public string Drop { get; set; } = "";
    public string Creator { get; set; } = "";
    public string Message { get; set; } = "";
    public string Display => $"[{Time:HH:mm:ss}] {Message}";
}
