namespace KickDropsMiner_WinUI.Services;

public static class AppServices
{
    public static AppState State { get; } = new();
    public static PythonBridgeClient Bridge { get; } = new();

    static AppServices()
    {
        Bridge.EventReceived += State.ApplyBridgeEvent;
    }
}
