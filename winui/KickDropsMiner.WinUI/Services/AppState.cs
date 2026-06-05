using System.Collections.ObjectModel;
using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Text.Json;
using KickDropsMiner_WinUI.Models;
using Microsoft.UI.Dispatching;

namespace KickDropsMiner_WinUI.Services;

public sealed class AppState : INotifyPropertyChanged
{
    public ObservableCollection<DropItem> Drops { get; } = [];
    public ObservableCollection<AccountItem> Accounts { get; } = [];
    public ObservableCollection<LogEntry> Logs { get; } = [];

    private bool _queueRunning;
    private bool _mute = true;
    private bool _hidePlayer;
    private bool _miniPlayer;
    private bool _force160p;
    private bool _autoStart;
    private bool _darkMode = true;
    private string _themeMode = "dark";
    private string _statusText = "Ready";
    private string _language = "en";
    private string _chromedriverPath = "";
    private string _extensionPath = "";

    public bool QueueRunning
    {
        get => _queueRunning;
        private set => SetField(ref _queueRunning, value);
    }

    public string StatusText
    {
        get => _statusText;
        private set => SetField(ref _statusText, value);
    }

    public bool Mute { get => _mute; private set => SetField(ref _mute, value); }
    public bool HidePlayer { get => _hidePlayer; private set => SetField(ref _hidePlayer, value); }
    public bool MiniPlayer { get => _miniPlayer; private set => SetField(ref _miniPlayer, value); }
    public bool Force160p { get => _force160p; private set => SetField(ref _force160p, value); }
    public bool AutoStart { get => _autoStart; private set => SetField(ref _autoStart, value); }
    public bool DarkMode { get => _darkMode; private set => SetField(ref _darkMode, value); }
    public string ThemeMode { get => _themeMode; private set => SetField(ref _themeMode, value); }
    public string Language { get => _language; private set => SetField(ref _language, value); }
    public string ChromedriverPath { get => _chromedriverPath; private set => SetField(ref _chromedriverPath, value); }
    public string ExtensionPath { get => _extensionPath; private set => SetField(ref _extensionPath, value); }
    private readonly DispatcherQueue? _dispatcherQueue;

    public event PropertyChangedEventHandler? PropertyChanged;

    public AppState()
    {
        _dispatcherQueue = DispatcherQueue.GetForCurrentThread();
        LoadConfig();
    }

    public async Task RefreshFromBridgeAsync()
    {
        var result = await AppServices.Bridge.SendCommandAsync("state");
        if (result.HasValue)
        {
            ApplyBackendState(result.Value);
        }
    }

    public void ApplyBridgeEvent(JsonElement eventElement)
    {
        void Apply()
        {
            if (eventElement.TryGetProperty("type", out var type))
            {
                var typeValue = type.GetString();
                if (typeValue == "log" && eventElement.TryGetProperty("log", out var log))
                {
                    AddLog(ReadString(log, "drop"), ReadString(log, "creator"), ReadString(log, "message"));
                }
                else if ((typeValue == "state" || typeValue == "progress") && eventElement.TryGetProperty("state", out var state))
                {
                    ApplyBackendState(state);
                }
            }
        }

        if (_dispatcherQueue is not null && !_dispatcherQueue.HasThreadAccess)
        {
            _dispatcherQueue.TryEnqueue(Apply);
        }
        else
        {
            Apply();
        }
    }

    public void ApplyBackendState(JsonElement root)
    {
        if (root.TryGetProperty("queue_running", out var queueRunning) && queueRunning.ValueKind is JsonValueKind.True or JsonValueKind.False)
        {
            QueueRunning = queueRunning.GetBoolean();
        }

        if (root.TryGetProperty("status", out var status) && status.ValueKind == JsonValueKind.String)
        {
            StatusText = status.GetString() ?? "Ready";
        }

        if (root.TryGetProperty("settings", out var settings) && settings.ValueKind == JsonValueKind.Object)
        {
            Mute = ReadBool(settings, "mute");
            HidePlayer = ReadBool(settings, "hide_player");
            MiniPlayer = ReadBool(settings, "mini_player");
            Force160p = ReadBool(settings, "force_160p");
            AutoStart = ReadBool(settings, "auto_start");
            DarkMode = ReadBool(settings, "dark_mode");
            var themeMode = ReadString(settings, "theme_mode");
            ThemeMode = string.IsNullOrWhiteSpace(themeMode) ? (DarkMode ? "dark" : "light") : themeMode;
            Language = ReadString(settings, "language");
            ChromedriverPath = ReadString(settings, "chromedriver_path");
            ExtensionPath = ReadString(settings, "extension_path");
        }

        Accounts.Clear();
        if (root.TryGetProperty("accounts", out var accounts) && accounts.ValueKind == JsonValueKind.Array)
        {
            foreach (var account in accounts.EnumerateArray())
            {
                Accounts.Add(new AccountItem
                {
                    Id = ReadString(account, "id"),
                    Name = ReadString(account, "name"),
                    CookiesValid = ReadBool(account, "cookies_valid")
                });
            }
        }

        Drops.Clear();
        if (root.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in items.EnumerateArray())
            {
                Drops.Add(new DropItem
                {
                    Index = ReadInt(item, "index"),
                    Drop = ReadString(item, "drop"),
                    Creator = ReadString(item, "creator"),
                    Url = ReadString(item, "url"),
                    AccountId = ReadString(item, "account_id"),
                    Account = ReadString(item, "account"),
                    TargetSeconds = ReadInt(item, "target_seconds"),
                    WatchedSeconds = ReadInt(item, "watched_seconds"),
                    Status = ReadString(item, "status"),
                    IsManualLink = ReadBool(item, "is_manual_link")
                });
            }
        }
    }

    public void LoadConfig()
    {
        Drops.Clear();
        Accounts.Clear();

        var configPath = FindRepoFile("config.json");
        if (configPath is null)
        {
            AddLog("Application", "", "No config.json found. Add a manual link to start.");
            return;
        }

        try
        {
            using var document = JsonDocument.Parse(File.ReadAllText(configPath));
            var root = document.RootElement;
            Mute = ReadBool(root, "mute");
            HidePlayer = ReadBool(root, "hide_player");
            MiniPlayer = ReadBool(root, "mini_player");
            Force160p = ReadBool(root, "force_160p");
            AutoStart = ReadBool(root, "auto_start");
            DarkMode = ReadBool(root, "dark_mode");
            var themeMode = ReadString(root, "theme_mode");
            ThemeMode = string.IsNullOrWhiteSpace(themeMode) ? (DarkMode ? "dark" : "light") : themeMode;
            Language = ReadString(root, "language");
            ChromedriverPath = ReadString(root, "chromedriver_path");
            ExtensionPath = ReadString(root, "extension_path");

            if (root.TryGetProperty("accounts", out var accounts) && accounts.ValueKind == JsonValueKind.Array)
            {
                foreach (var account in accounts.EnumerateArray())
                {
                    Accounts.Add(new AccountItem
                    {
                        Id = ReadString(account, "id"),
                        Name = ReadString(account, "name"),
                        CookiesValid = true
                    });
                }
            }

            if (root.TryGetProperty("items", out var items) && items.ValueKind == JsonValueKind.Array)
            {
                foreach (var item in items.EnumerateArray())
                {
                    var url = ReadString(item, "url");
                    var creator = CreatorFromUrl(url);
                    var drop = ReadString(item, "campaign_name");
                    if (string.IsNullOrWhiteSpace(drop))
                    {
                        drop = ReadString(item, "drop_name");
                    }
                    if (string.IsNullOrWhiteSpace(drop))
                    {
                        drop = "Manual link";
                    }

                    var minutes = ReadInt(item, "minutes");
                    var watchedSeconds = ReadInt(item, "watched_seconds");
                    if (watchedSeconds == 0)
                    {
                        watchedSeconds = ReadInt(item, "cumulative_time");
                    }

                    Drops.Add(new DropItem
                    {
                        Index = Drops.Count,
                        Drop = drop,
                        Creator = creator,
                        Url = url,
                        AccountId = ReadString(item, "account_id"),
                        Account = AccountName(ReadString(item, "account_id")),
                        TargetSeconds = Math.Max(0, minutes * 60),
                        WatchedSeconds = Math.Max(0, watchedSeconds),
                        Status = ReadBool(item, "finished") ? "Finished" : "Queued",
                        IsManualLink = ReadBool(item, "is_manual_link")
                    });
                }
            }

            AddLog("Application", "", $"Loaded {Drops.Count} drop(s) from config.");
        }
        catch (Exception ex)
        {
            AddLog("Application", "", $"Could not read config.json: {ex.Message}");
        }
    }

    public void AddManualLink(string url, int targetMinutes, string accountName)
    {
        var item = new DropItem
        {
            Drop = "Manual link",
            Creator = CreatorFromUrl(url),
            Url = url,
            Account = accountName,
            AccountId = Accounts.FirstOrDefault(a => a.Name == accountName)?.Id ?? "",
            TargetSeconds = Math.Max(0, targetMinutes * 60),
            WatchedSeconds = 0,
            Status = "Queued",
            IsManualLink = true
        };
        Drops.Add(item);
        AddLog(item.Drop, item.Creator, $"Added manual link for {item.Creator}.");
    }

    public void Remove(DropItem? item)
    {
        if (item is null)
        {
            return;
        }

        Drops.Remove(item);
        AddLog(item.Drop, item.Creator, $"Removed {item.Creator}.");
    }

    public void StartQueue()
    {
        QueueRunning = true;
        StatusText = "Running";
        AddLog("Queue", "", "Queue started.");
    }

    public void StopQueue()
    {
        QueueRunning = false;
        StatusText = "Ready";
        AddLog("Queue", "", "Queue stopped.");
    }

    public void SkipCreator()
    {
        var current = Drops.FirstOrDefault(d => d.Status == "Watching") ?? Drops.FirstOrDefault();
        if (current is null)
        {
            return;
        }

        AddLog(current.Drop, current.Creator, $"Skipped creator: {current.Creator}.");
    }

    public void AddLog(string drop, string creator, string message)
    {
        void Add()
        {
            Logs.Add(new LogEntry
            {
                Drop = drop,
                Creator = creator,
                Message = message
            });
        }

        if (_dispatcherQueue is not null && !_dispatcherQueue.HasThreadAccess)
        {
            _dispatcherQueue.TryEnqueue(Add);
        }
        else
        {
            Add();
        }
    }

    public string[] DropFilterValues()
    {
        return new[] { "All drops" }.Concat(Logs.Select(l => l.Drop).Where(v => !string.IsNullOrWhiteSpace(v)).Distinct().Order()).ToArray();
    }

    public string[] CreatorFilterValues()
    {
        return new[] { "All creators" }.Concat(Logs.Select(l => l.Creator).Where(v => !string.IsNullOrWhiteSpace(v)).Distinct().Order()).ToArray();
    }

    private string AccountName(string id)
    {
        if (string.IsNullOrWhiteSpace(id))
        {
            return Accounts.FirstOrDefault()?.Name ?? "No account";
        }

        return Accounts.FirstOrDefault(a => a.Id == id)?.Name ?? id;
    }

    private static string? FindRepoFile(string fileName)
    {
        var directory = AppContext.BaseDirectory;
        for (var i = 0; i < 8 && directory is not null; i++)
        {
            var candidate = Path.Combine(directory, fileName);
            if (File.Exists(candidate))
            {
                return candidate;
            }

            directory = Directory.GetParent(directory)?.FullName;
        }

        var current = Path.Combine(Environment.CurrentDirectory, fileName);
        return File.Exists(current) ? current : null;
    }

    private static string CreatorFromUrl(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri))
        {
            return url;
        }

        return uri.AbsolutePath.Trim('/').Split('/', StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? url;
    }

    private static string ReadString(JsonElement element, string property)
    {
        return element.TryGetProperty(property, out var value) && value.ValueKind == JsonValueKind.String
            ? value.GetString() ?? ""
            : "";
    }

    private static int ReadInt(JsonElement element, string property)
    {
        return element.TryGetProperty(property, out var value) && value.TryGetInt32(out var result) ? result : 0;
    }

    private static bool ReadBool(JsonElement element, string property)
    {
        return element.TryGetProperty(property, out var value) && value.ValueKind is JsonValueKind.True or JsonValueKind.False && value.GetBoolean();
    }

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
        return true;
    }
}
