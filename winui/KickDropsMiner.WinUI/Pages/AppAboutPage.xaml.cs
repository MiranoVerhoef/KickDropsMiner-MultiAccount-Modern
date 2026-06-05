using System.Reflection;
using Microsoft.UI.Xaml.Controls;

namespace KickDropsMiner_WinUI.Pages;

public sealed partial class AppAboutPage : Page
{
    public AppAboutPage()
    {
        InitializeComponent();
        var version = Assembly.GetExecutingAssembly().GetName().Version?.ToString(3) ?? "1.0.0";
        VersionText.Text = $"Version {version}";
    }
}
