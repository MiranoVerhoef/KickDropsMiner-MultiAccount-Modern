namespace KickDropsMiner_WinUI.Models;

public sealed class AccountItem
{
    public string Id { get; set; } = "";
    public string Name { get; set; } = "";
    public bool CookiesValid { get; set; }
    public string CookieStatus => CookiesValid ? "Valid" : "Missing";
}
