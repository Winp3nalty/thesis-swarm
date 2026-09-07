import requests

# 1. Ollama's local endpoint
url = "http://localhost:11434/api/generate"

# 2. Payload with stream set to False
payload = {
    "model": "qwen2.5:7b",
    "prompt": "Hello! Tell me a story.",
    "stream": False  # Crucial: Tells Ollama to return the full response at once
}

try:
    # 3. Send POST request to local model
    response = requests.post(url, json=payload)
    
    # 4. Extract and print the generated text
    result = response.json()
    print("Model Output:\n", result["response"])

except Exception as e:
    print(f"Error connecting to Ollama: {e}")