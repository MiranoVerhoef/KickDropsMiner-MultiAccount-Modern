using System.Collections.ObjectModel;
using System.Text.Json;
using KickDropsMiner_WinUI.Models;
using KickDropsMiner_WinUI.Services;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace KickDropsMiner_WinUI.Pages;

public sealed partial class AboutPage : Page
{
    private readonly ObservableCollection<CampaignItem> _campaigns = [];
    private readonly DispatcherQueue _dispatcherQueue;

    public AboutPage()
    {
        InitializeComponent();
        _dispatcherQueue = DispatcherQueue.GetForCurrentThread();
        CampaignList.ItemsSource = _campaigns;
        AccountPicker.ItemsSource = AppServices.State.Accounts;
        if (AppServices.State.Accounts.Count > 0)
        {
            AccountPicker.SelectedIndex = 0;
        }
        AppServices.Bridge.EventReceived += Bridge_EventReceived;
        Unloaded += (_, _) => AppServices.Bridge.EventReceived -= Bridge_EventReceived;
        _ = LoadCachedDropsAsync();
    }

    private async void Refresh_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        await LoadCachedDropsAsync();
        StatusText.Text = _campaigns.Count > 0 ? $"Showing {_campaigns.Count} cached drop(s). Updating..." : "Loading Kick drops...";
        LoadProgress.IsActive = true;
        LoadProgress.Visibility = Visibility.Visible;
        LoadBar.Value = 0;
        LoadBar.Visibility = Visibility.Visible;

        var result = await AppServices.Bridge.SendCommandAsync("fetch_drops");
        LoadProgress.IsActive = false;
        LoadProgress.Visibility = Visibility.Collapsed;
        LoadBar.Visibility = Visibility.Collapsed;
        if (!result.HasValue)
        {
            StatusText.Text = "Could not load drops.";
            return;
        }

        if (result.Value.TryGetProperty("error", out var error) && error.ValueKind == JsonValueKind.String)
        {
            StatusText.Text = error.GetString() ?? "Could not load drops.";
            return;
        }

        if (!result.Value.TryGetProperty("campaigns", out var campaigns) || campaigns.ValueKind != JsonValueKind.Array)
        {
            StatusText.Text = "No campaigns found.";
            return;
        }

        if (_campaigns.Count == 0)
        {
            foreach (var campaign in campaigns.EnumerateArray())
            {
                AddOrUpdateCampaign(campaign);
                await Task.Delay(35);
            }
        }

        StatusText.Text = $"{_campaigns.Count} campaign(s) found.";
    }

    private async Task LoadCachedDropsAsync()
    {
        var cached = await AppServices.Bridge.SendCommandAsync("cached_drops");
        if (!cached.HasValue
            || !cached.Value.TryGetProperty("campaigns", out var campaigns)
            || campaigns.ValueKind != JsonValueKind.Array)
        {
            return;
        }

        _campaigns.Clear();
        foreach (var campaign in campaigns.EnumerateArray())
        {
            AddOrUpdateCampaign(campaign);
        }
    }

    private void Bridge_EventReceived(JsonElement eventElement)
    {
        if (!eventElement.TryGetProperty("type", out var typeElement))
        {
            return;
        }

        var type = typeElement.GetString();
        _dispatcherQueue.TryEnqueue(() =>
        {
            if (type == "drops_begin")
            {
                LoadBar.Value = 0;
                LoadBar.Visibility = Visibility.Visible;
                StatusText.Text = "Loading Kick drops...";
            }
            else if (type == "drops_campaign" && eventElement.TryGetProperty("campaign", out var campaign))
            {
                AddOrUpdateCampaign(campaign);
                var loaded = ReadInt(eventElement, "loaded");
                var total = Math.Max(loaded, ReadInt(eventElement, "total"));
                LoadBar.Value = total > 0 ? Math.Min(100, loaded * 100.0 / total) : 0;
                StatusText.Text = $"Loaded {loaded} of {total} drop(s)...";
            }
            else if (type == "drops_end")
            {
                LoadBar.Value = 100;
                StatusText.Text = $"{_campaigns.Count} campaign(s) found.";
            }
            else if (type == "drops_error")
            {
                StatusText.Text = ReadString(eventElement, "message");
            }
        });
    }

    private void AddOrUpdateCampaign(JsonElement campaign)
    {
        var item = ToCampaignItem(campaign);
        var existing = _campaigns.FirstOrDefault(c => c.Id == item.Id && !string.IsNullOrWhiteSpace(item.Id));
        if (existing is not null)
        {
            var index = _campaigns.IndexOf(existing);
            _campaigns[index] = item;
        }
        else
        {
            _campaigns.Add(item);
        }
    }

    private static CampaignItem ToCampaignItem(JsonElement campaign)
    {
        return new CampaignItem
        {
            Id = ReadString(campaign, "id"),
            Name = ReadString(campaign, "name"),
            Game = ReadString(campaign, "game"),
            Creator = ReadString(campaign, "channels"),
            Drop = NormalizeDrop(ReadString(campaign, "rewards"), ReadString(campaign, "name")),
            Time = NormalizeTime(ReadString(campaign, "time"), ReadInt(campaign, "minutes")),
            Status = ReadString(campaign, "status"),
            Rewards = ReadString(campaign, "rewards"),
            Channels = ReadString(campaign, "channels"),
            GameImage = ReadString(campaign, "game_image"),
            RewardImage = ReadString(campaign, "reward_image"),
            RawJson = campaign.TryGetProperty("raw", out var raw) ? raw.GetRawText() : "{}"
        };
    }

    private static string NormalizeDrop(string rewards, string fallback)
    {
        if (string.IsNullOrWhiteSpace(rewards))
        {
            return fallback;
        }

        return rewards.Replace(", ", " / ");
    }

    private static string NormalizeTime(string time, int minutes)
    {
        if (!string.IsNullOrWhiteSpace(time))
        {
            return time;
        }

        return minutes > 0 ? $"{minutes} Minutes" : "";
    }

    private async void AddCampaign_Click(object sender, Microsoft.UI.Xaml.RoutedEventArgs e)
    {
        if (sender is not Button button || button.Tag is not CampaignItem campaign)
        {
            return;
        }

        var account = AccountPicker.SelectedItem as AccountItem;
        using var raw = JsonDocument.Parse(campaign.RawJson);
        var result = await AppServices.Bridge.SendCommandAsync("add_campaign", new
        {
            campaign = raw.RootElement,
            account_id = account?.Id
        });

        if (result.HasValue)
        {
            AppServices.State.ApplyBackendState(result.Value);
            StatusText.Text = $"Added {campaign.Name}.";
        }
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
}
