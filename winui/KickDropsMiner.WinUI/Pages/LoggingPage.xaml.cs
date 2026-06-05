using Microsoft.UI.Xaml.Controls;
using KickDropsMiner_WinUI.Models;
using KickDropsMiner_WinUI.Services;

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
        DropFilter.ItemsSource = AppServices.State.DropFilterValues();
        CreatorFilter.ItemsSource = AppServices.State.CreatorFilterValues();
        DropFilter.SelectedIndex = 0;
        CreatorFilter.SelectedIndex = 0;
    }

    private void RefreshLogs()
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
        LogList.ItemsSource = logs.ToArray();
    }

    private void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (LogList is not null)
        {
            RefreshLogs();
        }
    }

    private void Clear_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        AppServices.State.Logs.Clear();
        RefreshFilters();
        RefreshLogs();
    }
}
