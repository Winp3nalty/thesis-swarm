setup.sh#!/bin/bash
echo "=== Starting VDI Environment Setup ==="

# 1. Install Node.js packages
npm install

# 2. Install Ollama locally (No sudo required)
echo "Installing Ollama without root..."
mkdir -p ~/ollama
curl -fsSL https://ollama.com/download/ollama-linux-amd64.tar.zst | tar --zstd -xvf - -C ~/ollama
export PATH="$HOME/ollama/bin:$PATH"

# 3. Start the Ollama server in the background
nohup ollama serve &

# 4. Download OpenClaw framework
git clone https://github.com/cft0808/edict.git openclaw-framework

# 5. Pull Qwen 2.5
echo "Pulling Qwen 2.5 model..."
ollama pull qwen2.5

echo "=== Setup Complete! ==="