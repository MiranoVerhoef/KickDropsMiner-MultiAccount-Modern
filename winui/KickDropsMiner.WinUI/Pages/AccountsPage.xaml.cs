using KickDropsMiner_WinUI.Services;
using Microsoft.UI.Xaml.Controls;

namespace KickDropsMiner_WinUI.Pages;

public sealed partial class AccountsPage : Page
{
    public AppState State => AppServices.State;

    public AccountsPage()
    {
        InitializeComponent();
        _ = State.RefreshFromBridgeAsync();
    }

    private async void AddAccount_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        await RunLoginFlowAsync(null);
    }

    private async void LoginAccount_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        if (sender is Button button && button.Tag is string accountId && !string.IsNullOrWhiteSpace(accountId))
        {
            await RunLoginFlowAsync(accountId);
        }
    }

    private async void RemoveAccount_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not string accountId || string.IsNullOrWhiteSpace(accountId))
        {
            return;
        }

        var dialog = new ContentDialog
        {
            Title = "Remove account?",
            Content = "This removes the account and all drops bound to it.",
            PrimaryButtonText = "Remove",
            CloseButtonText = "Cancel",
            XamlRoot = XamlRoot
        };

        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            return;
        }

        var result = await AppServices.Bridge.SendCommandAsync("remove_account", new { account_id = accountId });
        if (result.HasValue)
        {
            State.ApplyBackendState(result.Value);
        }
    }

    private async Task RunLoginFlowAsync(string? accountId)
    {
        var start = await AppServices.Bridge.SendCommandAsync("start_login", new { account_id = accountId });
        string? startError = null;
        if (!start.HasValue || IsError(start.Value, out startError))
        {
            await ShowMessageAsync("Login failed", startError ?? "Could not start Chrome.");
            return;
        }

        var loginId = accountId;
        if (start.Value.TryGetProperty("login_id", out var loginElement)
            && loginElement.ValueKind == System.Text.Json.JsonValueKind.String)
        {
            loginId = loginElement.GetString();
        }

        var dialog = new ContentDialog
        {
            Title = "Please sign in",
            Content = "Sign in to Kick in the Chrome window, then click Save cookies.",
            PrimaryButtonText = "Save cookies",
            CloseButtonText = "Cancel",
            XamlRoot = XamlRoot
        };

        if (await dialog.ShowAsync() != ContentDialogResult.Primary)
        {
            await AppServices.Bridge.SendCommandAsync("cancel_login", new { login_id = loginId, account_id = accountId });
            return;
        }

        var finish = await AppServices.Bridge.SendCommandAsync("finish_login", new
        {
            login_id = loginId,
            account_id = accountId
        });
        if (finish.HasValue && IsNeedsName(finish.Value))
        {
            var fallbackName = await AskAccountNameAsync();
            if (!string.IsNullOrWhiteSpace(fallbackName))
            {
                finish = await AppServices.Bridge.SendCommandAsync("finish_login", new
                {
                    login_id = loginId,
                    account_id = accountId,
                    account_name = fallbackName
                });
            }
        }

        string? finishError = null;
        if (!finish.HasValue || IsError(finish.Value, out finishError))
        {
            await ShowMessageAsync("Login failed", finishError ?? "Could not save cookies.");
            return;
        }

        State.ApplyBackendState(finish.Value);
    }

    private async Task<string?> AskAccountNameAsync()
    {
        var nameBox = new TextBox { Header = "Account name", PlaceholderText = "Kick username" };
        var dialog = new ContentDialog
        {
            Title = "Name this Kick account",
            Content = nameBox,
            PrimaryButtonText = "Save",
            CloseButtonText = "Cancel",
            XamlRoot = XamlRoot
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary ? nameBox.Text.Trim() : null;
    }

    private async Task ShowMessageAsync(string title, string message)
    {
        var dialog = new ContentDialog
        {
            Title = title,
            Content = message,
            CloseButtonText = "OK",
            XamlRoot = XamlRoot
        };
        await dialog.ShowAsync();
    }

    private static bool IsNeedsName(System.Text.Json.JsonElement element)
    {
        return element.TryGetProperty("needs_name", out var needsName)
            && needsName.ValueKind == System.Text.Json.JsonValueKind.True;
    }

    private static bool IsError(System.Text.Json.JsonElement element, out string? error)
    {
        error = null;
        if (element.TryGetProperty("ok", out var ok) && ok.ValueKind is System.Text.Json.JsonValueKind.True)
        {
            return false;
        }

        if (element.TryGetProperty("error", out var errorElement) && errorElement.ValueKind == System.Text.Json.JsonValueKind.String)
        {
            error = errorElement.GetString();
        }
        return true;
    }
}
