#!/usr/bin/env python3
"""OpenAI-compatible embedding server using ONNX (no torch needed)"""
import os, json, http.server, urllib.parse, sys, numpy as np

MODEL_CACHE = os.path.expanduser("~/.cache/huggingface/hub/models--BAAI--bge-m3/snapshots/5617a9f61b028005a4858fdac845db406aefb181")
ONNX_PATH = os.path.join(MODEL_CACHE, "onnx/model.onnx")

print(f"Loading ONNX model from {ONNX_PATH}...")
from onnxruntime import InferenceSession
session = InferenceSession(ONNX_PATH, providers=["CPUExecutionProvider"])
print("ONNX model loaded!")

# Tokenizer
print("Loading tokenizer...")
from transformers import AutoTokenizer
tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")  # no torch needed for tokenizer
print("Tokenizer ready!")

class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/v1/models":
            self.send_json({
                "object": "list",
                "data": [{"id": "BAAI/bge-m3", "object": "model"}]
            })
        else:
            self.send_error(404)
    
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        
        if self.path == "/v1/embeddings":
            texts = body.get("input", [])
            if isinstance(texts, str):
                texts = [texts]
            
            data = []
            for i, text in enumerate(texts):
                inputs = tokenizer(text, return_tensors="np", padding=True, truncation=True, max_length=512)
                ort_inputs = {k: session._sess._inputs_meta[j].name: v 
                             for j, (k, v) in enumerate(inputs.items())}
                result = session.run(None, ort_inputs)
                emb = result[0].mean(axis=1).tolist()[0]
                data.append({"object": "embedding", "index": i, "embedding": emb})
            
            self.send_json({
                "object": "list",
                "data": data,
                "model": "BAAI/bge-m3",
                "usage": {"prompt_tokens": 0, "total_tokens": 0}
            })
        else:
            self.send_error(404)
    
    def send_json(self, obj):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode())
    
    def log_message(self, format, *args):
        print(f"[embed] {args[0]} {args[1]} {args[2]}")

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
server = http.server.HTTPServer(("127.0.0.1", port), Handler)
print(f"Embedding server: http://127.0.0.1:{port}/v1")
print(f"Models: BAAI/bge-m3")
server.serve_forever()
