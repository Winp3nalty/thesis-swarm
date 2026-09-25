#!/bin/bash
echo "========================================="
echo " Setting up W2 Environment & Tools"
echo "========================================="

# 1. Ensure local user bin directory exists and is in PATH
mkdir -p ~/.local/bin
export PATH="$HOME/.local/bin:$PATH"

# 2. Install Ollama locally if missing
if ! command -v ollama &> /dev/null; then
    echo "📥 Installing Ollama locally..."
    curl -L https://ollama.com/download/ollama-linux-amd64.tgz -o ollama.tgz
    tar -xzf ollama.tgz -C ~/.local/bin/
    rm ollama.tgz
else
    echo "✅ Ollama is already installed."
fi

# 3. Start Ollama server in the background if not running
if ! pgrep -x "ollama" > /dev/null; then
    echo "🚀 Starting Ollama server..."
    nohup ollama serve > ollama.log 2>&1 &
    sleep 3
else
    echo "✅ Ollama server is already running."
fi

# 4. Pull the lightweight Qwen 2.5 1.5B model
echo "📥 Pulling qwen2.5:1.5b model..."
ollama pull qwen2.5:1.5b

# 5. Install Python requirements
echo "📦 Installing Python dependencies..."
pip install --user requests

echo "========================================="
echo " Setup Complete! You are ready for W2."
echo "========================================="
