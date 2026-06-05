using KickDropsMiner_WinUI.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace KickDropsMiner_WinUI.Pages;

public sealed partial class SettingsPage : Page
{
    public AppState State => AppServices.State;
    private bool _loading = true;

    public SettingsPage()
    {
        InitializeComponent();
        _ = LoadAsync();
    }

    private async Task LoadAsync()
    {
        _loading = true;
        await State.RefreshFromBridgeAsync();
        MuteSwitch.IsOn = State.Mute;
        HidePlayerSwitch.IsOn = State.HidePlayer;
        MiniPlayerSwitch.IsOn = State.MiniPlayer;
        Force160pSwitch.IsOn = State.Force160p;
        AutoStartSwitch.IsOn = State.AutoStart;
        SetThemeSelection(State.ThemeMode);
        SetLanguageSelection(State.Language);
        BrowserPathsText.Text = $"Chromedriver: {Empty(State.ChromedriverPath)}   Extension: {Empty(State.ExtensionPath)}";
        ApplyTheme(State.ThemeMode);
        _loading = false;
    }

    private async void Setting_Toggled(object sender, RoutedEventArgs e)
    {
        if (_loading)
        {
            return;
        }

        await SaveSettingsAsync();
    }

    private async void ThemeMode_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading)
        {
            return;
        }

        ApplyTheme(SelectedThemeMode());
        await SaveSettingsAsync();
    }

    private async void Language_SelectionChanged(object sender, SelectionChangedEventArgs e)
    {
        if (_loading)
        {
            return;
        }

        await SaveSettingsAsync();
    }

    private void ApplyTheme(string themeMode)
    {
        if (XamlRoot?.Content is FrameworkElement root)
        {
            root.RequestedTheme = themeMode switch
            {
                "light" => ElementTheme.Light,
                "dark" => ElementTheme.Dark,
                _ => ElementTheme.Default
            };
        }
    }

    private void SetThemeSelection(string themeMode)
    {
        foreach (var item in ThemeModeBox.Items.OfType<ComboBoxItem>())
        {
            if ((item.Tag as string) == themeMode)
            {
                ThemeModeBox.SelectedItem = item;
                return;
            }
        }
        ThemeModeBox.SelectedIndex = 2;
    }

    private string SelectedThemeMode()
    {
        return (ThemeModeBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "dark";
    }

    private void SetLanguageSelection(string language)
    {
        foreach (var item in LanguageBox.Items.OfType<ComboBoxItem>())
        {
            if ((item.Tag as string) == language)
            {
                LanguageBox.SelectedItem = item;
                return;
            }
        }
        LanguageBox.SelectedIndex = 0;
    }

    private string SelectedLanguage()
    {
        return (LanguageBox.SelectedItem as ComboBoxItem)?.Tag as string ?? "en";
    }

    private async void Chromedriver_Click(object sender, RoutedEventArgs e)
    {
        var path = await PickFileAsync([".exe"], "Pick chromedriver.exe");
        if (path is null)
        {
            return;
        }

        await SaveSettingsAsync(chromedriverPath: path);
    }

    private async void Extension_Click(object sender, RoutedEventArgs e)
    {
        var path = await PickFileAsync([".crx"], "Pick Chrome extension");
        if (path is null)
        {
            return;
        }

        await SaveSettingsAsync(extensionPath: path);
    }

    private async Task SaveSettingsAsync(string? chromedriverPath = null, string? extensionPath = null)
    {
        var result = await AppServices.Bridge.SendCommandAsync("update_settings", new
        {
            mute = MuteSwitch.IsOn,
            hide_player = HidePlayerSwitch.IsOn,
            mini_player = MiniPlayerSwitch.IsOn,
            force_160p = Force160pSwitch.IsOn,
            auto_start = AutoStartSwitch.IsOn,
            dark_mode = SelectedThemeMode() == "dark",
            theme_mode = SelectedThemeMode(),
            language = SelectedLanguage(),
            chromedriver_path = chromedriverPath ?? State.ChromedriverPath,
            extension_path = extensionPath ?? State.ExtensionPath
        });
        if (result.HasValue)
        {
            State.ApplyBackendState(result.Value);
            BrowserPathsText.Text = $"Chromedriver: {Empty(State.ChromedriverPath)}   Extension: {Empty(State.ExtensionPath)}";
        }
    }

    private async Task<string?> PickFileAsync(IReadOnlyList<string> extensions, string title)
    {
        var picker = new FileOpenPicker
        {
            SuggestedStartLocation = PickerLocationId.Downloads,
            ViewMode = PickerViewMode.List
        };
        picker.FileTypeFilter.Clear();
        foreach (var extension in extensions)
        {
            picker.FileTypeFilter.Add(extension);
        }

        var hwnd = WindowNative.GetWindowHandle(App.MainWindow);
        InitializeWithWindow.Initialize(picker, hwnd);
        var file = await picker.PickSingleFileAsync();
        return file?.Path;
    }

    private static string Empty(string value)
    {
        return string.IsNullOrWhiteSpace(value) ? "not set" : value;
    }
}
