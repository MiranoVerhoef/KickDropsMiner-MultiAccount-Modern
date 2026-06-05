using Microsoft.UI.Xaml.Controls;
using KickDropsMiner_WinUI.Services;
using KickDropsMiner_WinUI.Models;

namespace KickDropsMiner_WinUI.Pages;

public sealed partial class HomePage : Page
{
    public AppState State => AppServices.State;
    public string StatusText => State.StatusText;

    public HomePage()
    {
        InitializeComponent();
        DataContext = State;
        _ = State.RefreshFromBridgeAsync();
    }

    private async void AddLink_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        await ShowManualLinkDialogAsync(null);
    }

    private async Task ShowManualLinkDialogAsync(DropItem? item)
    {
        var urlBox = new TextBox { PlaceholderText = "https://kick.com/channel" };
        var targetMode = new ComboBox
        {
            Header = "Target handling",
            ItemsSource = new[] { "Manual", "Timed Based" },
            SelectedIndex = 0
        };
        var minutesBox = new NumberBox
        {
            Header = "Online time minutes",
            Minimum = 0,
            Value = 0,
            SpinButtonPlacementMode = NumberBoxSpinButtonPlacementMode.Compact
        };
        var accountBox = new ComboBox
        {
            Header = "Account",
            ItemsSource = State.Accounts.ToArray(),
            DisplayMemberPath = "Name",
            SelectedIndex = State.Accounts.Count > 0 ? 0 : -1
        };
        if (item is not null)
        {
            urlBox.Text = item.Url;
            if (item.TargetSeconds > 0)
            {
                targetMode.SelectedIndex = 1;
                minutesBox.Value = Math.Max(0, item.TargetSeconds / 60);
            }

            var accounts = State.Accounts.ToArray();
            accountBox.ItemsSource = accounts;
            var accountIndex = Array.FindIndex(accounts, account => account.Id == item.AccountId);
            accountBox.SelectedIndex = accountIndex >= 0 ? accountIndex : (accounts.Length > 0 ? 0 : -1);
        }
        targetMode.SelectionChanged += (_, _) =>
        {
            minutesBox.IsEnabled = (targetMode.SelectedItem as string) == "Timed Based";
        };
        minutesBox.IsEnabled = (targetMode.SelectedItem as string) == "Timed Based";

        var panel = new StackPanel { Spacing = 12 };
        panel.Children.Add(urlBox);
        panel.Children.Add(targetMode);
        panel.Children.Add(minutesBox);
        panel.Children.Add(accountBox);

        var dialog = new ContentDialog
        {
            Title = item is null ? "Add stream link" : "Edit stream link",
            Content = panel,
            PrimaryButtonText = item is null ? "Add" : "Save",
            CloseButtonText = "Cancel",
            XamlRoot = XamlRoot
        };

        if (await dialog.ShowAsync() == ContentDialogResult.Primary && !string.IsNullOrWhiteSpace(urlBox.Text))
        {
            var minutes = (targetMode.SelectedItem as string) == "Timed Based"
                ? (int)Math.Max(0, minutesBox.Value)
                : 0;
            var account = accountBox.SelectedItem as AccountItem;
            var command = item is null ? "add_manual" : "edit_manual";
            object payload = item is null
                ? (object)new
                {
                    url = urlBox.Text.Trim(),
                    minutes,
                    account_id = account?.Id
                }
                : new
                {
                    index = item.Index,
                    url = urlBox.Text.Trim(),
                    minutes,
                    account_id = account?.Id
                };
            var result = await AppServices.Bridge.SendCommandAsync(command, payload);
            if (result.HasValue)
            {
                State.ApplyBackendState(result.Value);
            }
        }
    }

    private async void Refresh_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        await State.RefreshFromBridgeAsync();
    }

    private async void StartQueue_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        var result = await AppServices.Bridge.SendCommandAsync("start_queue");
        if (result.HasValue)
        {
            State.ApplyBackendState(result.Value);
        }
        Bindings.Update();
    }

    private async void StopQueue_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        var result = await AppServices.Bridge.SendCommandAsync("stop_queue");
        if (result.HasValue)
        {
            State.ApplyBackendState(result.Value);
        }
        Bindings.Update();
    }

    private async void SkipCreator_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        var result = await AppServices.Bridge.SendCommandAsync("skip_creator");
        if (result.HasValue)
        {
            State.ApplyBackendState(result.Value);
        }
    }

    private async void Edit_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        if (DropList.SelectedItem is not DropItem item)
        {
            return;
        }

        if (!item.IsManualLink)
        {
            var dialog = new ContentDialog
            {
                Title = "Campaign drops cannot be edited",
                Content = "Only manually added stream links can be edited. Campaign drops are managed from Browse Drops.",
                CloseButtonText = "OK",
                XamlRoot = XamlRoot
            };
            await dialog.ShowAsync();
            return;
        }

        await ShowManualLinkDialogAsync(item);
    }

    private async void Remove_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        if (DropList.SelectedItem is not DropItem item)
        {
            return;
        }

        var result = await AppServices.Bridge.SendCommandAsync("remove", new { index = item.Index });
        if (result.HasValue)
        {
            State.ApplyBackendState(result.Value);
        }
    }
}
