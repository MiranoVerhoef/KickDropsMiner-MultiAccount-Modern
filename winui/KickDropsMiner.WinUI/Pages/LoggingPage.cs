using System.Text;
using KickDropsMiner_WinUI.Models;
using KickDropsMiner_WinUI.Services;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace KickDropsMiner_WinUI.Pages;

public sealed class LoggingPage : Page
{
    private readonly ComboBox _dropFilter = new() { Header = "Drop", MinWidth = 260 };
    private readonly ComboBox _creatorFilter = new() { Header = "Creator", MinWidth = 220 };
    private readonly ListView _logList = new();
    private readonly TextBlock _status = new()
    {
        Text = "Queue, creator switching, and progress history.",
        Foreground = new SolidColorBrush(Colors.Gray)
    };

    public LoggingPage()
    {
        Content = BuildContent();
        _dropFilter.SelectionChanged += Filter_SelectionChanged;
        _creatorFilter.SelectionChanged += Filter_SelectionChanged;
        RefreshFilters();
        RefreshLogs();
    }

    private UIElement BuildContent()
    {
        var root = new Grid
        {
            Padding = new Thickness(28, 24, 28, 24),
            RowSpacing = 18
        };
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = GridLength.Auto });
        root.RowDefinitions.Add(new RowDefinition { Height = new GridLength(1, GridUnitType.Star) });

        var header = new Grid { ColumnSpacing = 12 };
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = new GridLength(1, GridUnitType.Star) });
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });
        header.ColumnDefinitions.Add(new ColumnDefinition { Width = GridLength.Auto });

        var titleStack = new StackPanel();
        titleStack.Children.Add(new TextBlock
        {
            Text = "Logging",
            FontSize = 32,
            FontWeight = Microsoft.UI.Text.FontWeights.SemiBold
        });
        titleStack.Children.Add(_status);
        header.Children.Add(titleStack);

        var refreshButton = new Button { Content = "Refresh", MinWidth = 100 };
        refreshButton.Click += Refresh_Click;
        Grid.SetColumn(refreshButton, 1);
        header.Children.Add(refreshButton);

        var exportButton = new Button { Content = "Export logs", MinWidth = 110 };
        exportButton.Click += Export_Click;
        Grid.SetColumn(exportButton, 2);
        header.Children.Add(exportButton);

        var clearButton = new Button { Content = "Clear", MinWidth = 100 };
        clearButton.Click += Clear_Click;
        Grid.SetColumn(clearButton, 3);
        header.Children.Add(clearButton);

        root.Children.Add(header);

        var filters = new StackPanel
        {
            Orientation = Orientation.Horizontal,
            Spacing = 12
        };
        filters.Children.Add(_dropFilter);
        filters.Children.Add(_creatorFilter);
        Grid.SetRow(filters, 1);
        root.Children.Add(filters);

        var border = new Border
        {
            BorderThickness = new Thickness(1),
            CornerRadius = new CornerRadius(8),
            BorderBrush = new SolidColorBrush(Colors.Gray),
            Child = _logList
        };
        Grid.SetRow(border, 2);
        root.Children.Add(border);

        return root;
    }

    private void RefreshFilters()
    {
        var selectedDrop = _dropFilter.SelectedItem as string ?? "All drops";
        var selectedCreator = _creatorFilter.SelectedItem as string ?? "All creators";
        var drops = AppServices.State.DropFilterValues();
        var creators = AppServices.State.CreatorFilterValues();
        _dropFilter.ItemsSource = drops;
        _creatorFilter.ItemsSource = creators;
        _dropFilter.SelectedItem = drops.Contains(selectedDrop) ? selectedDrop : "All drops";
        _creatorFilter.SelectedItem = creators.Contains(selectedCreator) ? selectedCreator : "All creators";
    }

    private void RefreshLogs()
    {
        _logList.ItemsSource = FilteredLogs()
            .Select(log => $"[{log.Time:HH:mm:ss}] {log.Drop} | {log.Creator} | {log.Message}")
            .ToArray();
    }

    private void Filter_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        RefreshLogs();
    }

    private async void Refresh_Click(object sender, RoutedEventArgs e)
    {
        await AppServices.State.RefreshFromBridgeAsync();
        RefreshFilters();
        RefreshLogs();
    }

    private void Clear_Click(object sender, RoutedEventArgs e)
    {
        AppServices.State.Logs.Clear();
        RefreshFilters();
        RefreshLogs();
    }

    private void Export_Click(object sender, RoutedEventArgs e)
    {
        var logs = FilteredLogs().ToArray();
        var desktop = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
        var fileName = $"KickDropMiner-logs-{DateTime.Now:yyyyMMdd-HHmmss}.txt";
        var path = Path.Combine(desktop, fileName);

        var builder = new StringBuilder();
        builder.AppendLine("Kick Drop Miner logs");
        builder.AppendLine($"Exported: {DateTimeOffset.Now:yyyy-MM-dd HH:mm:ss zzz}");
        builder.AppendLine($"Drop filter: {_dropFilter.SelectedItem as string ?? "All drops"}");
        builder.AppendLine($"Creator filter: {_creatorFilter.SelectedItem as string ?? "All creators"}");
        builder.AppendLine();

        foreach (var log in logs)
        {
            builder.AppendLine($"[{log.Time:yyyy-MM-dd HH:mm:ss}] Drop=\"{log.Drop}\" Creator=\"{log.Creator}\" {log.Message}");
        }

        File.WriteAllText(path, builder.ToString(), Encoding.UTF8);
        _status.Text = $"Exported {logs.Length} log line(s) to Desktop: {fileName}";
    }

    private IEnumerable<LogEntry> FilteredLogs()
    {
        var drop = _dropFilter.SelectedItem as string ?? "All drops";
        var creator = _creatorFilter.SelectedItem as string ?? "All creators";
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
