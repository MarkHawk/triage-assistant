# Using Ollama on another machine on your LAN

The triage assistant can use an Ollama server that runs on a different computer, such as a Windows PC with a GPU. Nothing runs on the GPU machine except Ollama itself. The assistant sends it masked text over your local network.

Use a placeholder name in these notes and your real address only in `.env`, which is gitignored.

## 1. On the GPU machine (Windows)

1. Install Ollama and pull the models you want to compare. Check the exact names with `ollama list`; the tags below are examples.

   ```powershell
   ollama pull qwen2.5-coder:14b
   ollama pull qwen2.5:14b
   ```

2. **Make Ollama listen on the network.** By default it listens only on the PC itself (`127.0.0.1:11434`), so other machines are refused. Set a user environment variable, then quit Ollama from the system tray and start it again:

   ```powershell
   setx OLLAMA_HOST "0.0.0.0:11434"
   ```

3. **Open the port, for your home or office network only.** In an administrator PowerShell:

   ```powershell
   New-NetFirewallRule -DisplayName "Ollama on the LAN" -Direction Inbound -Protocol TCP -LocalPort 11434 -Action Allow -Profile Private -RemoteAddress LocalSubnet
   ```

   If Windows has labelled your network "Public", change it to "Private" first, or the rule will not apply.

4. Give the PC a fixed address (a DHCP reservation in your router) or a name that resolves on your network, so `.env` does not go stale.

## 2. On the machine running the assistant

Check you can reach it. This lists the models it has:

```bash
curl http://ollama-pc.lan:11434/api/tags
```

Then, in `.env`:

```
BACKEND=ollama
OLLAMA_HOST=http://ollama-pc.lan:11434
OLLAMA_MODEL=qwen2.5-coder:14b
OLLAMA_NUM_CTX=12288
```

No trailing slash on the host. `.env` is never committed.

## 3. Things to know

- **Ollama has no login.** Anyone on your network can use it. Do not forward the port from your router. The assistant only ever sends text that has passed its secrets check, but the server itself is open to your LAN.
- **The first request is slow.** It loads the model into the GPU. `OLLAMA_FIRST_TIMEOUT_S` (default 600) covers that, and later requests use `OLLAMA_TIMEOUT_S`. `OLLAMA_KEEP_ALIVE` (default 30m) keeps the model loaded between runs.
- **The context window is always sent** (`OLLAMA_NUM_CTX`) and the prompt is trimmed to fit it. If the server reports it read almost the whole window, the run stops with an error rather than answering from a cut prompt. A larger window means more VRAM for the key-value cache: check Task Manager's GPU memory while a run is going before raising it.
- **Refused, or "Could not reach Ollama":** the environment variable was not picked up (restart Ollama from the tray, or sign out and in), or the firewall rule does not apply to the network's profile.

## 4. Comparing models and window sizes

`--model` and `--num-ctx` override `.env` for one run, so you can compare without editing it:

```bash
python -m triage ticket tickets/001.md --logs lab.log --model qwen2.5-coder:14b --num-ctx 8192
python -m triage ticket tickets/001.md --logs lab.log --model qwen2.5:14b --num-ctx 12288
```

Each run prints a one-line footer on stderr: backend, model, window, time, the prompt size the assistant estimated, the tokens the server says it read, and whether the first answer was sent back for breaking the rules (`RETRIED`) or replaced by one built by code (`CODE FALLBACK`).
