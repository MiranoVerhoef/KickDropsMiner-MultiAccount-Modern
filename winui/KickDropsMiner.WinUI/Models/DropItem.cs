using System.ComponentModel;
using System.Runtime.CompilerServices;

namespace KickDropsMiner_WinUI.Models;

public sealed class DropItem : INotifyPropertyChanged
{
    private int _index;
    private string _drop = "";
    private string _creator = "";
    private string _account = "";
    private int _targetSeconds;
    private int _watchedSeconds;
    private string _status = "Queued";
    private bool _isManualLink;

    public int Index
    {
        get => _index;
        set => SetField(ref _index, value);
    }

    public string Drop
    {
        get => _drop;
        set => SetField(ref _drop, value);
    }

    public string Creator
    {
        get => _creator;
        set => SetField(ref _creator, value);
    }

    public string Account
    {
        get => _account;
        set => SetField(ref _account, value);
    }

    public int TargetSeconds
    {
        get => _targetSeconds;
        set
        {
            if (SetField(ref _targetSeconds, value))
            {
                OnPropertyChanged(nameof(Progress));
            }
        }
    }

    public int WatchedSeconds
    {
        get => _watchedSeconds;
        set
        {
            if (SetField(ref _watchedSeconds, value))
            {
                OnPropertyChanged(nameof(Progress));
            }
        }
    }

    public string Status
    {
        get => _status;
        set => SetField(ref _status, value);
    }

    public string Url { get; set; } = "";

    public string AccountId { get; set; } = "";

    public bool IsManualLink
    {
        get => _isManualLink;
        set => SetField(ref _isManualLink, value);
    }

    public string Progress
    {
        get
        {
            if (TargetSeconds <= 0)
            {
                return $"Manual | {FormatDuration(WatchedSeconds)}";
            }

            var remaining = Math.Max(0, TargetSeconds - WatchedSeconds);
            return $"{FormatDuration(WatchedSeconds)} / {FormatDuration(TargetSeconds)} | {FormatDuration(remaining)} left";
        }
    }

    public event PropertyChangedEventHandler? PropertyChanged;

    public static string FormatDuration(int seconds)
    {
        seconds = Math.Max(0, seconds);
        var minutes = seconds / 60;
        var secs = seconds % 60;
        return minutes > 0 ? $"{minutes}m {secs}s" : $"{secs}s";
    }

    private bool SetField<T>(ref T field, T value, [CallerMemberName] string? propertyName = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        OnPropertyChanged(propertyName);
        return true;
    }

    private void OnPropertyChanged([CallerMemberName] string? propertyName = null)
    {
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(propertyName));
    }
}
