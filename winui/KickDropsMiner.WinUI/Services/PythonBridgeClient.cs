using System.Diagnostics;
using System.Text.Json;

namespace KickDropsMiner_WinUI.Services;

public sealed class PythonBridgeClient
{
    private readonly string _repoRoot;
    private readonly Dictionary<string, TaskCompletionSource<JsonElement>> _pending = [];
    private readonly object _sync = new();
    private Process? _process;
    private int _nextId;

    public event Action<JsonElement>? EventReceived;

    public PythonBridgeClient()
    {
        _repoRoot = FindRepoRoot() ?? AppContext.BaseDirectory;
    }

    public async Task<JsonElement?> SendCommandAsync(string command, object? payload = null)
    {
        await EnsureStartedAsync();

        Process process;
        string requestId;
        TaskCompletionSource<JsonElement> completion;

        lock (_sync)
        {
            if (_process is null || _process.HasExited)
            {
                AppServices.State.AddLog("Bridge", "", "Python bridge is not running.");
                return null;
            }

            process = _process;
            requestId = Interlocked.Increment(ref _nextId).ToString();
            completion = new TaskCompletionSource<JsonElement>(TaskCreationOptions.RunContinuationsAsynchronously);
            _pending[requestId] = completion;
        }

        var request = JsonSerializer.Serialize(new
        {
            id = requestId,
            command,
            payload
        });

        await process.StandardInput.WriteLineAsync(request);
        await process.StandardInput.FlushAsync();

        var timeout = CommandTimeout(command);
        var completed = await Task.WhenAny(completion.Task, Task.Delay(timeout));
        if (completed != completion.Task)
        {
            lock (_sync)
            {
                _pending.Remove(requestId);
            }

            AppServices.State.AddLog("Bridge", "", $"Python bridge command timed out: {command}");
            return null;
        }

        return await completion.Task;
    }

    public async Task EnsureStartedAsync()
    {
        lock (_sync)
        {
            if (_process is not null && !_process.HasExited)
            {
                return;
            }
        }

        var bridge = Path.Combine(_repoRoot, "winui_bridge.py");
        if (!File.Exists(bridge))
        {
            AppServices.State.AddLog("Bridge", "", "Python bridge is not installed yet.");
            return;
        }

        var startInfo = new ProcessStartInfo
        {
            FileName = "python",
            Arguments = $"\"{bridge}\" --daemon",
            WorkingDirectory = _repoRoot,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true
        };

        var process = Process.Start(startInfo);
        if (process is null)
        {
            AppServices.State.AddLog("Bridge", "", "Could not start Python bridge.");
            return;
        }

        lock (_sync)
        {
            _process = process;
        }

        _ = Task.Run(() => ReadStdoutAsync(process));
        _ = Task.Run(() => ReadStderrAsync(process));
        await Task.Delay(150);
    }

    private async Task ReadStdoutAsync(Process process)
    {
        while (!process.HasExited)
        {
            var line = await process.StandardOutput.ReadLineAsync();
            if (line is null)
            {
                break;
            }

            if (string.IsNullOrWhiteSpace(line))
            {
                continue;
            }

            try
            {
                using var document = JsonDocument.Parse(line);
                var root = document.RootElement.Clone();

                if (root.TryGetProperty("event", out var eventElement))
                {
                    EventReceived?.Invoke(eventElement.Clone());
                    continue;
                }

                if (!root.TryGetProperty("id", out var idElement))
                {
                    continue;
                }

                var id = idElement.GetString();
                if (id is null)
                {
                    continue;
                }

                TaskCompletionSource<JsonElement>? completion = null;
                lock (_sync)
                {
                    if (_pending.TryGetValue(id, out completion))
                    {
                        _pending.Remove(id);
                    }
                }

                if (completion is null)
                {
                    continue;
                }

                if (root.TryGetProperty("result", out var result))
                {
                    completion.TrySetResult(result.Clone());
                }
                else if (root.TryGetProperty("error", out var error))
                {
                    completion.TrySetException(new InvalidOperationException(error.GetString()));
                }
            }
            catch (Exception ex)
            {
                AppServices.State.AddLog("Bridge", "", $"Could not parse bridge output: {ex.Message}");
            }
        }
    }

    private static async Task ReadStderrAsync(Process process)
    {
        while (!process.HasExited)
        {
            var line = await process.StandardError.ReadLineAsync();
            if (line is null)
            {
                break;
            }
            if (!string.IsNullOrWhiteSpace(line))
            {
                AppServices.State.AddLog("Bridge", "", line);
            }
        }
    }

    private static TimeSpan CommandTimeout(string command)
    {
        return command switch
        {
            "start_queue" => TimeSpan.FromSeconds(3),
            "start_login" => TimeSpan.FromSeconds(60),
            "fetch_drops" => TimeSpan.FromMinutes(2),
            _ => TimeSpan.FromSeconds(30),
        };
    }

    private static string? FindRepoRoot()
    {
        var directory = AppContext.BaseDirectory;
        for (var i = 0; i < 10 && directory is not null; i++)
        {
            if (File.Exists(Path.Combine(directory, "main.py")) && Directory.Exists(Path.Combine(directory, "core")))
            {
                return directory;
            }

            directory = Directory.GetParent(directory)?.FullName;
        }

        return null;
    }
}
