using Microsoft.UI.Xaml.Controls;
using KickDropsMiner_WinUI.Models;
using KickDropsMiner_WinUI.Services;
using System.Text;

namespace KickDropsMiner_WinUI.Pages;

public sealed partial class LoggingPage : Page
{
    public LoggingPage()
    {
        InitializeComponent();
        RefreshFilters();
        RefreshLogs();
    }

    private void RefreshFilters()
    {
        var selectedDrop = DropFilter.SelectedItem as string ?? "All drops";
        var selectedCreator = CreatorFilter.SelectedItem as string ?? "All creators";
        DropFilter.ItemsSource = AppServices.State.DropFilterValues();
        CreatorFilter.ItemsSource = AppServices.State.CreatorFilterValues();
        DropFilter.SelectedItem = DropFilter.Items.Contains(selectedDrop) ? selectedDrop : "All drops";
        CreatorFilter.SelectedItem = CreatorFilter.Items.Contains(selectedCreator) ? selectedCreator : "All creators";
    }

    private void RefreshLogs()
    {
        LogList.ItemsSource = FilteredLogs().ToArray();
    }

    private void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (LogList is not null)
        {
            RefreshLogs();
        }
    }

    private async void Refresh_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        await AppServices.State.RefreshFromBridgeAsync();
        RefreshFilters();
        RefreshLogs();
    }

    private void Clear_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        AppServices.State.Logs.Clear();
        RefreshFilters();
        RefreshLogs();
    }

    private void Export_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        var logs = FilteredLogs().ToArray();
        var desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        var fileName = $"KickDropMiner-logs-{DateTime.Now:yyyyMMdd-HHmmss}.txt";
        var path = Path.Combine(desktop, fileName);

        var builder = new StringBuilder();
        builder.AppendLine("Kick Drop Miner logs");
        builder.AppendLine($"Exported: {DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss zzz}");
        builder.AppendLine($"Drop filter: {DropFilter.SelectedItem as string ?? "All drops"}");
        builder.AppendLine($"Creator filter: {CreatorFilter.SelectedItem as string ?? "All creators"}");
        builder.AppendLine();

        foreach (var log in logs)
        {
            builder.AppendLine($"[{log.Time:yyyy-MM-dd HH:mm:ss}] Drop=\"{log.Drop}\" Creator=\"{log.Creator}\" {log.Message}");
        }

        File.WriteAllText(path, builder.ToString(), Encoding.UTF8);
        ExportStatus.Text = $"Exported {logs.Length} log line(s) to Desktop: {fileName}";
    }

    private IEnumerable<LogEntry> FilteredLogs()
    {
        var drop = DropFilter.SelectedItem as string ?? "All drops";
        var creator = CreatorFilter.SelectedItem as string ?? "All creators";
        IEnumerable<LogEntry> logs = AppServices.State.Logs;
        if (drop != "All drops")
        {
            logs = logs.Where(l => l.Drop == drop);
        }
        if (creator != "All creators")
        {
            logs = logs.Where(l => l.Creator == creator);
        }
        return logs;
    }
}
